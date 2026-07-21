"""Cadastro FAKE de teste — grava na planilha e nas abas auxiliares."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from app.services.activities_storage import DEFAULT_TENANT_ID
from app.services.activity_service import criar_atividade
from app.services.closed_services import save_closed_services
from app.services.legacy_core import invalidate_sheet_cache, normalize_text
from app.services.registration import get_seller_options, save_cadastro_tipo, save_new_company

logger = logging.getLogger(__name__)

FAKE_COMPANY_NAME = "EMPRESA FAKE TESTE OPPI LTDA"


def _fake_company_form(vendedor: str) -> dict:
    return {
        "cadastro_tipo": "empresa",
        "empresa": FAKE_COMPANY_NAME,
        "cnpj": "45.997.418/0001-53",
        "data_abertura": "15/03/2018",
        "capital": "R$ 150.000,00",
        "colaboradores": "28 colaboradores",
        "site": "https://www.empresafake-teste.com.br",
        "email_empresa": "contato@empresafake-teste.com.br",
        "telefone_b2b": "(11) 99001-0001",
        "telefone_fixo": "(11) 99001-0002",
        "telefone_alternativo": "(11) 99001-0003",
        "cep": "01310-100",
        "endereco": "Avenida Paulista",
        "endereco_numero": "1000",
        "endereco_complemento": "Sala 1205",
        "bairro": "Bela Vista",
        "municipio": "São Paulo",
        "uf": "SP",
        "quantidade_socios": "3",
        "socio_1": "Ana Paula Ferreira",
        "telefone_socio_1": "(11) 99001-0011",
        "cpf_socio_1": "529.982.247-25",
        "email_socio_1": "ana.ferreira@empresafake-teste.com.br",
        "socio_2": "Bruno Mendes Silva",
        "telefone_socio_2": "(11) 99001-0012",
        "cpf_socio_2": "390.533.447-05",
        "socio_3": "Carla Souza Oliveira",
        "telefone_socio_3": "(11) 99001-0013",
        "cpf_socio_3": "153.509.460-56",
        "instagram": "@empresafake_teste",
        "linkedin": "linkedin.com/company/empresa-fake-teste-oppi",
        "vendedor": vendedor,
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


def _resolve_vendedor() -> str:
    from app.dependencies import get_prepared_data

    df, _columns = get_prepared_data()
    sellers = get_seller_options(df)
    for preferred in ("Raíssa", "Raissa", "oppitech"):
        for seller in sellers:
            if normalize_text(seller).lower() == preferred.lower():
                return seller
    return sellers[0] if sellers else "Sem vendedor"


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

    vendedor = _resolve_vendedor()
    form = _fake_company_form(vendedor)
    sheet_row = save_new_company(form)
    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, form["cadastro_tipo"])
    save_closed_services(DEFAULT_TENANT_ID, sheet_row, FAKE_CLOSED_SERVICES)

    activity_warning = ""
    _, activity_error = criar_atividade(
        DEFAULT_TENANT_ID,
        {
            "sheet_row": sheet_row,
            "empresa": form["empresa"],
            "contato": form["socio_1"],
            "stage": "Novo Lead",
            "activity_type": "Contato",
            "process_action": "Fazer primeiro contato",
            "channel": "WhatsApp",
            "assigned_user_id": form["vendedor"],
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

    invalidate_sheet_cache()
    logger.info("Empresa FAKE cadastrada na linha %s da planilha.", sheet_row)

    return {
        "created": True,
        "sheet_row": sheet_row,
        "empresa": FAKE_COMPANY_NAME,
        "edit_url": f"/cadastro/todos/{sheet_row}/editar",
        "message": f'Empresa FAKE cadastrada na linha {sheet_row} da planilha.{activity_warning}',
    }


def ensure_fake_test_company_on_startup() -> None:
    try:
        result = seed_fake_test_company(user="startup")
        logger.info("Seed FAKE: %s", result.get("message"))
    except Exception as error:
        logger.warning("Seed FAKE não executado: %s", error)

