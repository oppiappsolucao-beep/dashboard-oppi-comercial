"""Protocolo sequencial das ordens de serviço."""
import pytest

from app.services.service_orders import create_service_order, list_service_orders


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
            responsible="Ana",
        )
