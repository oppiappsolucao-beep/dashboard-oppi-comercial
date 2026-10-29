"""Protocolo sequencial das ordens de serviço."""
from datetime import timedelta

import pytest

from app.services.service_orders import (
    _now,
    create_manual_lead,
    create_service_order,
    list_orders_by_sector,
    list_presentation_appointments,
    list_service_orders,
    schedule_presentation,
)


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path))
    import app.services.crm_local_db as db

    db._initialized = False
    yield
    db._initialized = False


def test_protocolo_e_gerado_e_nao_repete(isolated_storage):
    from app.services.org_registry import list_sectors, save_person

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    save_person(kind="funcionario", name="Ana", sector_id=sector["id"], username="ana.suporte", password="123456")
    first = create_service_order(
        tenant_id="default",
        sheet_row=42,
        empresa="Marmoraria Alfa",
        subject="Implantação do ponto",
        description="Primeiro acesso da equipe",
        sector="Suporte",
        scheduled_date="2026-10-03",
        responsible="Ana",
        priority="Alta",
        created_by="Ana",
    )
    second = create_service_order(
        tenant_id="default",
        sheet_row=42,
        empresa="Marmoraria Alfa",
        subject="Ajuste de acesso",
        sector="Suporte",
        scheduled_date="2026-10-04",
        responsible="Ana",
        created_by="Ana",
    )

    assert first["protocol"].startswith("OS-")
    assert second["protocol"].startswith("OS-")
    assert first["protocol"] != second["protocol"]
    assert first["status_label"] == "Aberta"
    assert first["subject"] == "Implantação do ponto"

    listed = list_service_orders("default", 42)
    assert [item["protocol"] for item in listed] == [second["protocol"], first["protocol"]]


def test_assunto_obrigatorio(isolated_storage):
    from app.services.org_registry import list_sectors, save_person

    sector = next(item for item in list_sectors() if item["name"] == "Suporte")
    save_person(kind="funcionario", name="Ana", sector_id=sector["id"], username="ana.suporte", password="123456")
    with pytest.raises(ValueError, match="assunto"):
        create_service_order(
            tenant_id="default",
            sheet_row=7,
            empresa="Beta",
            subject="   ",
            sector="Suporte",
            scheduled_date="2026-10-03",
            responsible="Ana",
        )


def test_lead_manual_agenda_apresentacao(isolated_storage):
    from app.services.org_registry import list_sectors, save_person

    sector = next(item for item in list_sectors() if item["name"] == "Comercial")
    assert "agenda" in sector["accesses"]
    save_person(kind="funcionario", name="Lia", sector_id=sector["id"], username="lia.comercial", password="123456")
    save_person(kind="funcionario", name="Caio", sector_id=sector["id"], username="caio.comercial", password="123456")
    lead_day = _now().date().isoformat()
    appointment = (_now().date() + timedelta(days=3)).isoformat()

    order = create_manual_lead(
        sector="Comercial",
        responsible="Lia",
        empresa="Pedras Sul",
        contato="Carlos",
        whatsapp="(11) 98888-7777",
        email="carlos@pedras.com",
        created_by="Lia",
    )
    assert order["subject"] == "Lead"
    assert order["queue_id"] == "analise"
    assert order["sheet_row"] == 0
    assert order["scheduled_date"] == lead_day
    assert order["presentation_date"] == ""

    schedule_presentation(
        order["id"],
        day=appointment,
        hour="14:00",
        link="https://meet.example/abc",
        responsible="Lia",
        author="Lia",
    )
    saved = next(item for item in list_orders_by_sector("Comercial") if item["id"] == order["id"])
    assert saved["scheduled_date"] == lead_day
    assert saved["presentation_date"] == appointment
    assert saved["presentation_hour"] == "14:00"
    assert "Carlos" in saved["description"]

    agenda = list_presentation_appointments("Lia")
    assert len(agenda) == 1
    assert agenda[0]["scheduled_date"] == appointment
    assert agenda[0]["hour"] == "14:00"
    assert agenda[0]["empresa"] == "Pedras Sul"
    assert agenda[0]["trainee"] == "Carlos"
    assert list_presentation_appointments("Caio") == []

    schedule_presentation(
        order["id"],
        day=appointment,
        hour="14:00",
        link="https://meet.example/abc",
        responsible="Lia",
        author="Lia",
    )

    other = create_manual_lead(
        sector="Comercial",
        responsible="Caio",
        contato="Marina",
        whatsapp="11977776666",
        created_by="Caio",
    )
    with pytest.raises(ValueError, match="já tem uma apresentação"):
        schedule_presentation(
            other["id"],
            day=appointment,
            hour="14:00",
            responsible="Lia",
            author="Caio",
        )
    with pytest.raises(ValueError, match="hoje ou em um dia futuro"):
        schedule_presentation(
            other["id"],
            day="2020-01-06",
            hour="15:00",
            responsible="Caio",
            author="Caio",
        )
