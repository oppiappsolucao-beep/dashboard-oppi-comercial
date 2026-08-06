"""Testes da API de provisionamento Comercial → financeiro → Ponto."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.api_provisioning import (
    ProvisioningError,
    build_financeiro_from_api,
    build_registration_form_from_api,
    format_money,
    normalize_pagamento_modalidade,
    parse_plano_vencimento,
)
from app.services.oppi_ponto_bridge import build_onboard_payload_from_crm


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch):
    monkeypatch.setenv("COMERCIAL_API_KEY", "test-comercial-key")
    monkeypatch.setenv("APP_USERNAME", "oppitech")
    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "session-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_normalize_pagamento_modalidade():
    assert normalize_pagamento_modalidade("Boleto") == "boleto"
    assert normalize_pagamento_modalidade("cartao") == "cartao_recorrente"
    assert normalize_pagamento_modalidade("Cartão recorrente") == "cartao_recorrente"
    assert normalize_pagamento_modalidade("manual") == "manual"
    with pytest.raises(ProvisioningError):
        normalize_pagamento_modalidade("pix")


def test_parse_plano_vencimento():
    assert parse_plano_vencimento("15-09-2026") == "2026-09-15"
    assert parse_plano_vencimento("15/09/2026") == "2026-09-15"
    assert parse_plano_vencimento("2026-09-15") == "2026-09-15"
    with pytest.raises(ProvisioningError):
        parse_plano_vencimento("32-13-2026")


def test_format_money():
    assert format_money("199.90") == "R$ 199,90"
    assert format_money(199.9) == "R$ 199,90"
    assert format_money("R$ 199,90") == "R$ 199,90"


def test_build_registration_novo_cliente():
    form = build_registration_form_from_api(
        {
            "tipo_cadastro": "novo_cliente",
            "responsavel_nome": "Higo Silva",
            "gestor_login": "Higo Silva",
            "whatsapp": "11999998888",
            "telefone": "1133334444",
            "cnpj": "12.345.678/0001-95",
            "razao_social": "Empresa Teste LTDA",
            "email_login": "gestor@teste.com",
            "email_cobranca": "financeiro@teste.com",
            "email_verificacao": "gestor@teste.com",
            "senha": "SenhaForte1",
        }
    )
    assert form["cadastro_tipo"] == "empresa"
    assert form["empresa"] == "Empresa Teste LTDA"
    assert form["is_filial"] is False
    assert form["_access"]["email_login_gestor"] == "gestor@teste.com"
    assert form["_access"]["senha_acesso"] == "SenhaForte1"


def test_build_registration_filial_requires_matriz():
    with pytest.raises(ProvisioningError, match="gestor existente"):
        build_registration_form_from_api(
            {
                "tipo_cadastro": "nova_filial",
                "whatsapp": "11999998888",
                "cnpj": "12345678000195",
                "razao_social": "Filial Teste",
                "email_login": "gestor@teste.com",
                "email_cobranca": "financeiro@teste.com",
            }
        )


def test_build_financeiro_from_api():
    closed, payments, modalidade = build_financeiro_from_api(
        {
            "plano_valor": "199.90",
            "plano_vencimento": "10-08-2026",
            "pagamento_modalidade": "boleto",
        }
    )
    assert modalidade == "boleto"
    assert closed[0]["forma_pagamento"] == "Boleto"
    assert closed[0]["vencimento"] == "2026-08-10"
    assert payments[0]["status"] == "Pendente"


def test_onboard_payload_respects_modalidade():
    payload = build_onboard_payload_from_crm(
        values={
            "empresa": "Empresa X",
            "cnpj": "12345678000195",
            "socio_1": "Gestor",
            "telefone_b2b": "11999998888",
            "email_empresa": "a@b.com",
        },
        access={
            "email_login_gestor": "a@b.com",
            "email_cobranca": "a@b.com",
            "email_confirmacao_admin": "a@b.com",
            "senha_acesso": "abc12345",
        },
        closed_services=[{"valor": "R$ 199,90", "vencimento": "2026-08-10", "forma_pagamento": "Boleto"}],
        pagamento_modalidade="boleto",
    )
    assert payload["pagamento_modalidade"] == "boleto"
    assert payload["plano_valor"] == "199,90"


def test_onboard_payload_defaults_manual_without_modalidade():
    payload = build_onboard_payload_from_crm(
        values={"empresa": "X", "cnpj": "12345678000195", "telefone_b2b": "11999998888"},
        access={"email_login_gestor": "a@b.com", "email_cobranca": "a@b.com", "senha_acesso": "x"},
        closed_services=[{"valor": "100", "forma_pagamento": "Boleto"}],
    )
    assert payload["pagamento_modalidade"] == "manual"


def test_api_health_requires_key():
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/v1/health").status_code == 401
    ok = client.get("/api/v1/health", headers={"X-Oppi-Comercial-Key": "test-comercial-key"})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True


def test_api_create_cadastro_orchestrates(monkeypatch):
    from app.main import app

    client = TestClient(app)

    with patch("app.services.api_provisioning.save_new_company", return_value=4242), patch(
        "app.services.api_provisioning.save_cadastro_tipo"
    ), patch("app.services.api_provisioning.save_access_fields"), patch(
        "app.services.api_provisioning.save_closed_services"
    ), patch("app.services.api_provisioning.save_payment_history"), patch(
        "app.services.api_provisioning.save_lead_action"
    ), patch("app.services.api_provisioning.oppi_ponto_configured", return_value=True), patch(
        "app.services.api_provisioning.sync_or_onboard_company",
        return_value={"ok": True, "action": "onboarded", "company_id": 99, "password": "gerada"},
    ) as onboard:
        response = client.post(
            "/api/v1/cadastros",
            headers={"X-Oppi-Comercial-Key": "test-comercial-key"},
            json={
                "tipo_cadastro": "novo_cliente",
                "responsavel_nome": "Higo Silva",
                "whatsapp": "(11) 99999-8888",
                "cnpj": "12.345.678/0001-95",
                "razao_social": "Empresa API LTDA",
                "email_login": "gestor@teste.com",
                "email_cobranca": "financeiro@teste.com",
                "email_verificacao": "gestor@teste.com",
                "plano_valor": "199.90",
                "plano_vencimento": "06-09-2026",
                "pagamento_modalidade": "boleto",
            },
        )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["sheet_row"] == 4242
    assert data["financeiro"]["pagamento_modalidade"] == "boleto"
    assert data["ponto"]["company_id"] == 99
    onboard.assert_called_once()
    kwargs = onboard.call_args.kwargs
    assert kwargs["pagamento_modalidade"] == "boleto"
    assert kwargs["vincular_gestor_existente"] is False
