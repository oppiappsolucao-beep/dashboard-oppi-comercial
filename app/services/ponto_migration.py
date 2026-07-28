"""Migração Oppi Ponto → CRM: clientes entram como empresa (não lead).

Fluxo: empresa → fechamento → financeiro → suporte.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.services.closed_services import save_closed_services
from app.services.legacy_core import (
    DuplicateRegistrationError,
    normalize_cnpj_for_duplicate,
    normalize_text,
)
from app.services.payment_history import save_payment_history
from app.services.registration import (
    save_access_fields,
    save_cadastro_ativo,
    save_cadastro_tipo,
    save_company_edit,
    save_new_company,
)
from app.services.lead_actions_storage import DEFAULT_TENANT_ID, save_lead_action

MIGRATED_STATUS = "Fechado"
MIGRATED_SERVICE_NAME = "Ponto Eletrônico Oppi"


def _format_money(value: str) -> str:
    raw = normalize_text(value)
    if not raw:
        return ""
    if raw.upper().startswith("R$"):
        return raw
    candidate = raw.replace("R$", "").strip()
    if "," in candidate and "." in candidate:
        candidate = candidate.replace(".", "").replace(",", ".")
    elif "," in candidate:
        candidate = candidate.replace(",", ".")
    try:
        amount = float(candidate)
        formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {formatted}"
    except ValueError:
        return f"R$ {raw}"


def _map_forma_pagamento(modalidade: str) -> str:
    key = normalize_text(modalidade).lower()
    if key in {"avista", "à vista", "a vista", "avulsa"}:
        return "À vista"
    if key in {"anual", "ano"}:
        return "Anual"
    return "Mensal"


def _map_payment_status(status: str) -> str:
    key = normalize_text(status).lower()
    if key in {"pago", "confirmado", "paid", "received"}:
        return "Pago"
    if key in {"vencido", "overdue", "atrasado"}:
        return "Atrasado"
    if key in {"cancelado", "cancelled", "canceled"}:
        return "Cancelado"
    return "Pendente"


def _format_cnpj_display(digits: str) -> str:
    d = normalize_cnpj_for_duplicate(digits)
    if len(d) != 14:
        return normalize_text(digits)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _iso_or_empty(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = normalize_text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def map_ponto_company_to_form(company: dict) -> dict:
    """Monta formulário de cadastro CRM (tipo empresa + status Fechado)."""
    cnpj_digits = normalize_cnpj_for_duplicate(company.get("cnpj"))
    empresa = normalize_text(company.get("razao_social")) or normalize_text(company.get("nome"))
    valor = _format_money(company.get("plano_valor", ""))
    whatsapp = normalize_text(company.get("whatsapp")) or normalize_text(company.get("telefone"))
    telefone = normalize_text(company.get("telefone"))
    created = company.get("created_at")
    data_chamado = date.today().strftime("%d/%m/%Y")
    if isinstance(created, datetime):
        data_chamado = created.strftime("%d/%m/%Y")
    elif isinstance(created, str) and len(created) >= 10:
        try:
            data_chamado = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%d/%m/%Y")
        except ValueError:
            pass

    colaboradores = company.get("funcionarios")
    colaboradores_txt = ""
    if colaboradores not in (None, ""):
        colaboradores_txt = f"{colaboradores} colaboradores"

    return {
        "cadastro_tipo": "empresa",
        "empresa": empresa,
        "cnpj": _format_cnpj_display(cnpj_digits) if cnpj_digits else normalize_text(company.get("cnpj")),
        "endereco": normalize_text(company.get("endereco")),
        "endereco_numero": normalize_text(company.get("numero")),
        "endereco_complemento": normalize_text(company.get("complemento")),
        "bairro": normalize_text(company.get("bairro")),
        "municipio": normalize_text(company.get("cidade")),
        "uf": normalize_text(company.get("estado")).upper()[:2],
        "cep": normalize_text(company.get("cep")),
        "telefone_b2b": whatsapp,
        "telefone_fixo": telefone if telefone != whatsapp else "",
        "email_empresa": normalize_text(company.get("email_cobranca")),
        "socio_1": normalize_text(company.get("responsavel_nome")) or normalize_text(company.get("admin_nome")),
        "email_socio_1": normalize_text(company.get("admin_email")),
        "telefone_socio_1": whatsapp,
        "vendedor": "Oppi",
        "observacoes": (
            f"Migrado do Oppi Ponto (company_id={company.get('oppi_ponto_company_id')}). "
            "Cadastro tipo Empresa — fluxo comercial fechado."
        ),
        "status": MIGRATED_STATUS,
        "data_chamado": data_chamado,
        "servico": MIGRATED_SERVICE_NAME,
        "valor_proposta": valor,
        "colaboradores": colaboradores_txt,
    }


def map_closed_services(company: dict) -> list[dict]:
    return [
        {
            "servico": MIGRATED_SERVICE_NAME,
            "valor": _format_money(company.get("plano_valor", "")),
            "forma_pagamento": _map_forma_pagamento(company.get("pagamento_modalidade", "recorrente")),
            "vencimento": _iso_or_empty(company.get("plano_vencimento")),
        }
    ]


def map_payment_history(company: dict) -> list[dict]:
    status = _map_payment_status(company.get("pagamento_status", "pendente"))
    valor = _format_money(company.get("plano_valor", ""))
    due = _iso_or_empty(company.get("plano_vencimento")) or date.today().isoformat()
    return [
        {
            "data": due,
            "descricao": "Plano Oppi Ponto (migração)",
            "valor": valor,
            "status": status,
            "forma_pagamento": _map_forma_pagamento(company.get("pagamento_modalidade", "recorrente")),
        }
    ]


def map_access_fields(company: dict) -> dict:
    return {
        "email_login_gestor": normalize_text(company.get("admin_email")),
        "email_confirmacao_admin": normalize_text(company.get("admin_email_verificacao"))
        or normalize_text(company.get("admin_email")),
        "email_cobranca": normalize_text(company.get("email_cobranca")),
        "senha_acesso": "",
    }


def find_sheet_row_by_cnpj(cnpj: str) -> int | None:
    from app.dependencies import get_prepared_data

    digits = normalize_cnpj_for_duplicate(cnpj)
    if not digits:
        return None

    df, columns = get_prepared_data()
    if df.empty:
        return None

    cnpj_col = columns.get("cnpj")
    if not cnpj_col or cnpj_col not in df.columns:
        return None

    for _, row in df.iterrows():
        existing = normalize_cnpj_for_duplicate(row.get(cnpj_col, ""))
        if existing == digits:
            sheet_row = int(row.get("_sheet_row") or 0)
            return sheet_row or None
    return None


def find_sheet_row_by_ponto_id(company_id: int) -> int | None:
    from app.services.lead_actions_storage import get_all_lead_actions

    if not company_id:
        return None
    actions = get_all_lead_actions(DEFAULT_TENANT_ID) or {}
    for sheet_row_key, record in actions.items():
        if not isinstance(record, dict):
            continue
        if int(record.get("oppi_ponto_company_id") or 0) == int(company_id):
            try:
                return int(sheet_row_key)
            except (TypeError, ValueError):
                continue
    return None


def preview_company(company: dict) -> dict:
    cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)
    existing_by_id = find_sheet_row_by_ponto_id(ponto_id) if ponto_id else None
    existing_by_cnpj = find_sheet_row_by_cnpj(cnpj) if cnpj else None
    existing = existing_by_id or existing_by_cnpj

    if not cnpj:
        action = "skip_missing_cnpj"
    elif existing:
        action = "update"
    else:
        action = "create"

    return {
        "action": action,
        "oppi_ponto_company_id": ponto_id,
        "empresa": normalize_text(company.get("razao_social")) or normalize_text(company.get("nome")),
        "cnpj": cnpj,
        "sheet_row": existing,
        "pagamento_status": company.get("pagamento_status"),
        "ativo": company.get("ativo"),
    }


def apply_company(company: dict) -> dict:
    """Grava no CRM como empresa. Retorna resultado da operação."""
    preview = preview_company(company)
    action = preview["action"]
    if action == "skip_missing_cnpj":
        return {**preview, "ok": False, "message": "CNPJ inválido ou ausente — não migrado."}

    form = map_ponto_company_to_form(company)
    closed = map_closed_services(company)
    payments = map_payment_history(company)
    access = map_access_fields(company)
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)
    ativo_crm = bool(company.get("ativo")) and not bool(company.get("bloqueado_plataforma"))

    try:
        if action == "update":
            sheet_row = int(preview["sheet_row"])
            save_company_edit(sheet_row, form)
        else:
            sheet_row = save_new_company(form)
    except DuplicateRegistrationError as err:
        # Corrida / telefone duplicado: tenta achar por CNPJ de novo
        sheet_row = find_sheet_row_by_cnpj(form.get("cnpj", ""))
        if not sheet_row:
            return {**preview, "ok": False, "message": str(err)}
        save_company_edit(sheet_row, form)
        action = "update"

    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, "empresa")
    save_cadastro_ativo(DEFAULT_TENANT_ID, sheet_row, ativo_crm)
    save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed)
    save_payment_history(DEFAULT_TENANT_ID, sheet_row, payments)
    save_access_fields(DEFAULT_TENANT_ID, sheet_row, access)
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {
            "oppi_ponto_company_id": ponto_id,
            "migrated_from_ponto": True,
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    return {
        **preview,
        "action": action,
        "sheet_row": sheet_row,
        "ok": True,
        "message": f"{'Atualizada' if action == 'update' else 'Criada'} como empresa (linha {sheet_row}).",
    }


def migrate_companies(payload: dict | list, *, apply: bool = False) -> dict:
    if isinstance(payload, dict):
        companies = payload.get("companies") or []
    else:
        companies = payload
    if not isinstance(companies, list):
        raise ValueError("JSON inválido: esperado lista em 'companies'.")

    results = []
    for item in companies:
        if not isinstance(item, dict):
            continue
        if apply:
            results.append(apply_company(item))
        else:
            results.append(preview_company(item))

    summary = {
        "apply": apply,
        "total": len(results),
        "create": sum(1 for r in results if r.get("action") == "create"),
        "update": sum(1 for r in results if r.get("action") == "update"),
        "skip_missing_cnpj": sum(1 for r in results if r.get("action") == "skip_missing_cnpj"),
        "ok": sum(1 for r in results if r.get("ok")) if apply else None,
        "failed": sum(1 for r in results if apply and not r.get("ok")),
        "results": results,
    }
    return summary
