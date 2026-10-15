"""Situação do treinamento na agenda: motivo, cores e mudança de dia."""
import json
from datetime import date

import pytest

from app.services.service_orders import create_service_order, update_training_appointment


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path))
    import app.services.crm_local_db as db

    db._initialized = False
    yield
    db._initialized = False


def _trainer(sector_id: str) -> None:
    from app.services.org_registry import save_person

    save_person(
        kind="treinador",
        name="Lia",
        sector_id=sector_id,
        username="lia.treinadora",
        password="123456",
        schedule_json=json.dumps(
            {
                "days": ["seg", "ter", "qua", "qui", "sex"],
                "slots": ["09:00", "10:00", "11:00"],
            }
        ),
    )


def _order(day: str = "2026-10-20", hour: str = "09:00", empresa: str = "Alfa") -> dict:
    return create_service_order(
        tenant_id="default",
        sheet_row=42,
        empresa=empresa,
        subject="Treinamento",
        description=(
            "Responsável: João. Quantidade de funcionários: 2. "
            f"Treinador: Lia. Videoconferência: https://meet.example/abc. Horário: {hour}."
        ),
        sector="Suporte",
        scheduled_date=day,
        responsible="Lia",
        created_by="Lia",
    )


def test_antes_do_dia_reagenda_horario_e_grava_motivo(isolated_storage):
    from app.services.org_registry import list_sectors

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    _trainer(sector["id"])
    order = _order()

    saved = update_training_appointment(
        order["id"],
        result="reagendou",
        reason="Cliente pediu outro horário",
        day="2026-10-21",
        hour="10:00",
        author="Lia",
        today=date(2026, 10, 15),
    )

    assert saved["training_result"] == "reagendou"
    assert saved["training_result_label"] == "Reagendou"
    assert saved["training_reason"] == "Cliente pediu outro horário"
    assert saved["scheduled_date"] == "2026-10-21"
    assert saved["hour"] == "10:00"
    assert "Horário: 10:00" in saved["description"]


def test_no_dia_nao_altera_horario_sem_ser_reagendamento(isolated_storage):
    from app.services.org_registry import list_sectors

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    _trainer(sector["id"])
    order = _order()

    with pytest.raises(ValueError, match="antes do dia"):
        update_training_appointment(
            order["id"],
            result="realizado",
            reason="Treinamento feito",
            day="2026-10-21",
            hour="10:00",
            today=date(2026, 10, 20),
        )


def test_faltou_e_realizado_guardam_motivo_no_mesmo_horario(isolated_storage):
    from app.services.org_registry import list_sectors

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    _trainer(sector["id"])
    missed = _order(empresa="Beta")
    done = _order(hour="11:00", empresa="Gama")

    missed_saved = update_training_appointment(
        missed["id"],
        result="faltou",
        reason="Ninguém entrou na chamada",
        day="2026-10-20",
        hour="09:00",
        today=date(2026, 10, 20),
    )
    done_saved = update_training_appointment(
        done["id"],
        result="realizado",
        reason="Equipe treinada",
        day="2026-10-20",
        hour="11:00",
        today=date(2026, 10, 20),
    )

    assert missed_saved["training_result"] == "faltou"
    assert missed_saved["scheduled_date"] == "2026-10-20"
    assert missed_saved["hour"] == "09:00"
    assert done_saved["training_result"] == "realizado"
    assert done_saved["training_reason"] == "Equipe treinada"


def test_reagendar_em_horario_ocupado_falha(isolated_storage):
    from app.services.org_registry import list_sectors

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    _trainer(sector["id"])
    _order(hour="10:00", empresa="Ocupado")
    order = _order()

    with pytest.raises(ValueError, match="já tem um treinamento"):
        update_training_appointment(
            order["id"],
            result="reagendou",
            reason="Cliente pediu o horário das dez",
            day="2026-10-20",
            hour="10:00",
            today=date(2026, 10, 15),
        )


def test_motivo_obrigatorio(isolated_storage):
    from app.services.org_registry import list_sectors

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    _trainer(sector["id"])
    order = _order()

    with pytest.raises(ValueError, match="motivo"):
        update_training_appointment(
            order["id"],
            result="faltou",
            reason="  ",
            today=date(2026, 10, 20),
        )


def test_treinador_nao_altera_agenda_de_outro(isolated_storage):
    from app.services.org_registry import list_sectors

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    _trainer(sector["id"])
    order = _order()

    with pytest.raises(ValueError, match="outro treinador"):
        update_training_appointment(
            order["id"],
            result="realizado",
            reason="Treinamento feito",
            author="Paulo",
            actor_name="Paulo",
            actor_is_trainer=True,
            today=date(2026, 10, 20),
        )
