"""Cadastro FAKE de teste — grava na planilha e nas abas auxiliares."""
from __future__ import annotations

from datetime import date, timedelta

from app.services.activities_storage import DEFAULT_TENANT_ID
from app.services.activity_service import criar_atividade
from app.services.closed_services import save_closed_services
from app.services.legacy_core import normalize_text
from app.services.registration import save_cadastro_tipo, save_new_company

FAKE_COMPANY_NAME = "EMPRESA FAKE TESTE OPPI LTDA"

FAKE_COMPANY_FORM = {
    "cadastro_tipo": "empresa",
    "empresa": FAKE_COMPANY_NAME,
    "cnpj": "12.345.678/0001-99",
    "data_abertura": "15/03/2018",
    "capital": "R$ 150.000,00",
    "colaboradores": "28 colaboradores",
    "site": "https://www.empresafake-teste.com.br",
    "email_empresa": "contato@empresafake-teste.com.br",
    "telefone_b2b": "(11) 98765-4321",
    "telefone_fixo": "(11) 3456-7890",
    "telefone_alternativo": "(11) 91234-5678",
    "cep": "01310-100",
    "endereco": "Avenida Paulista",
    "endereco_numero": "1000",
    "endereco_complemento": "Sala 1205",
    "bairro": "Bela Vista",
    "municipio": "São Paulo",
    "uf": "SP",
    "quantidade_socios": "3",
    "socio_1": "Ana Paula Ferreira",
    "telefone_socio_1": "(11) 99876-5432",
    "cpf_socio_1": "123.456.789-01",
    "email_socio_1": "ana.ferreira@empresafake-teste.com.br",
    "socio_2": "Bruno Mendes Silva",
    "telefone_socio_2": "(11) 99765-4321",
    "cpf_socio_2": "987.654.321-09",
    "socio_3": "Carla Souza Oliveira",
    "telefone_socio_3": "(11) 99654-3210",
    "cpf_socio_3": "456.789.123-45",
    "instagram": "@empresafake_teste",
    "linkedin": "linkedin.com/company/empresa-fake-teste-oppi",
    "vendedor": "Raíssa",
    "observacoes": (
        "Cadastro FAKE criado automaticamente para teste de persistência na planilha. "
        "Empresa fictícia — pode ser excluída após validação."
    ),
    "status": "Novo Lead",
    "data_chamado": date.today().strftime("%d/%m/%Y"),
    "servico": "Consultoria Comercial FAKE",
    "valor_proposta": "R$ 4.500,00",
}

FAKE_CLOSED_SERVICES = [
    {
        "servico": "Consultoria Comercial FAKE",
        "valor": "R$ 4.500,00",
        "forma_pagamento": "Mensal",
        "vencimento": (date.today() + timedelta(days=30)).isoformat(),
    },
]


def find_fake_company_sheet_row() -> int | None:
    from app.dependencies import get_prepared_data

    df, _columns = get_prepared_data()
    if df.empty or "_empresa" not in df.columns:
        return None
    target = normalize_text(FAKE_COMPANY_NAME).lower()
    for _, row in df.iterrows():
        if normalize_text(row.get("_empresa", "")).lower() == target:
            return int(row.get("_sheet_row") or 0) or None
    return None


def seed_fake_test_company(*, user: str = "admin") -> dict:
    from app.config import settings

    if not settings.sheets_configured:
        raise RuntimeError("Planilha não configurada. Verifique GCP_SERVICE_ACCOUNT_B64 no Easypanel.")

    existing_row = find_fake_company_sheet_row()
    if existing_row:
        return {
            "created": False,
            "sheet_row": existing_row,
            "empresa": FAKE_COMPANY_NAME,
            "edit_url": f"/cadastro/todos/{existing_row}/editar",
            "message": "Empresa FAKE já estava cadastrada na planilha.",
        }

    sheet_row = save_new_company(FAKE_COMPANY_FORM)
    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, FAKE_COMPANY_FORM["cadastro_tipo"])
    save_closed_services(DEFAULT_TENANT_ID, sheet_row, FAKE_CLOSED_SERVICES)

    activity_warning = ""
    _, activity_error = criar_atividade(
        DEFAULT_TENANT_ID,
        {
            "sheet_row": sheet_row,
            "empresa": FAKE_COMPANY_FORM["empresa"],
            "contato": FAKE_COMPANY_FORM["socio_1"],
            "stage": "Novo Lead",
            "activity_type": "Contato",
            "process_action": "Fazer primeiro contato",
            "channel": "WhatsApp",
            "assigned_user_id": FAKE_COMPANY_FORM["vendedor"],
            "scheduled_date": date.today().isoformat(),
            "scheduled_time": "10:30",
            "status": "pendente",
            "priority": "Alta",
            "description": "Primeiro contato comercial para apresentação da Oppi e qualificação do lead FAKE.",
            "next_action": "Qualificar lead",
        },
        user,
        is_admin_user=True,
    )
    if activity_error:
        activity_warning = f" Cadastro criado, mas a atividade não foi registrada: {activity_error}"

    return {
        "created": True,
        "sheet_row": sheet_row,
        "empresa": FAKE_COMPANY_NAME,
        "edit_url": f"/cadastro/todos/{sheet_row}/editar",
        "message": f'Empresa FAKE cadastrada na linha {sheet_row} da planilha.{activity_warning}',
    }
