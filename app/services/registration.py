"""Validação e payloads de cadastro."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from app.services.lead_actions_storage import get_lead_action, save_lead_action
from app.services.legacy_core import (
    DuplicateRegistrationError,
    STATUS_OPTIONS,
    append_company_to_sheet,
    normalize_cnpj_for_duplicate,
    normalize_phone_for_duplicate,
    normalize_text,
    status_group,
    update_company_in_sheet,
)


CADASTRO_TIPO_OPTIONS = [
    {"value": "lead", "label": "Lead"},
    {"value": "empresa", "label": "Empresa"},
]

CADASTRO_PIPELINE_STEPS = [
    {"key": "novo", "label": "Novo"},
    {"key": "qualificado", "label": "Qualificado"},
    {"key": "proposta", "label": "Proposta"},
    {"key": "negociacao", "label": "Negociação"},
    {"key": "ganho", "label": "Ganho"},
    {"key": "perdido", "label": "Perdido"},
]

STAGE_SUMMARY_HINTS = {
    "Novo Lead": "Início do relacionamento",
    "Contato": "Primeiro contato em andamento",
    "Qualificação": "Lead em qualificação",
    "Reunião": "Reunião agendada ou realizada",
    "Proposta": "Proposta em elaboração",
    "Retorno": "Aguardando retorno do cliente",
    "Negociação": "Negociação comercial ativa",
    "Fechado": "Negócio concluído",
}

REGISTRATION_FIELDS = [
    "empresa", "data_abertura", "capital", "cnpj", "endereco", "endereco_numero", "endereco_complemento",
    "cep", "bairro", "municipio", "uf", "email_empresa", "site",
    "telefone_b2b", "telefone_fixo", "telefone_alternativo",
    "socio_1", "cpf_socio_1", "email_socio_1", "telefone_socio_1",
    "socio_2", "telefone_socio_2", "cpf_socio_2",
    "socio_3", "telefone_socio_3", "cpf_socio_3",
    "instagram", "linkedin", "vendedor", "status", "data_chamado", "observacoes",
    "servico", "valor_proposta", "colaboradores",
]


def infer_partners_count(values: dict) -> int:
    if any(normalize_text(values.get(key)) for key in ("socio_3", "telefone_socio_3", "cpf_socio_3")):
        return 3
    if any(normalize_text(values.get(key)) for key in ("socio_2", "telefone_socio_2", "cpf_socio_2")):
        return 2
    if any(normalize_text(values.get(key)) for key in ("socio_1", "telefone_socio_1", "cpf_socio_1", "email_socio_1")):
        return 1
    return 0


def validate_registration_form(form: dict) -> str | None:
    empresa = normalize_text(form.get("empresa"))
    cnpj = normalize_text(form.get("cnpj"))
    telefone_b2b = normalize_text(form.get("telefone_b2b"))
    telefone_fixo = normalize_text(form.get("telefone_fixo"))
    telefone_alternativo = normalize_text(form.get("telefone_alternativo"))

    if not empresa:
        return "Preencha o nome da empresa para concluir o cadastro."
    if cnpj and not normalize_cnpj_for_duplicate(cnpj):
        return "Digite um CNPJ válido com 14 números."

    for label, phone in [
        ("Celular WhatsApp", telefone_b2b),
        ("Telefone fixo", telefone_fixo),
        ("Telefone alternativo", telefone_alternativo),
    ]:
        if phone and not normalize_phone_for_duplicate(phone):
            return f"Digite um número válido no campo {label}."

    return None


def build_registration_payload(form: dict) -> dict:
    now_text = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d/%m/%Y %H:%M")
    data_chamado = form.get("data_chamado") or date.today().strftime("%d/%m/%Y")

    if hasattr(data_chamado, "strftime"):
        data_chamado = data_chamado.strftime("%d/%m/%Y")

    payload = {field: normalize_text(form.get(field, "")) for field in REGISTRATION_FIELDS}
    payload["data_chamado"] = normalize_text(data_chamado)
    payload["ultima_atualizacao"] = now_text
    return payload


def save_new_company(form: dict) -> int:
    error = validate_registration_form(form)
    if error:
        raise ValueError(error)
    return append_company_to_sheet(build_registration_payload(form))


def save_company_edit(sheet_row: int, form: dict) -> None:
    error = validate_registration_form(form)
    if error:
        raise ValueError(error)
    update_company_in_sheet(sheet_row, build_registration_payload(form))


def get_seller_options(df) -> list[str]:
    sellers = sorted({
        normalize_text(v) for v in df["_vendedor"].tolist()
        if normalize_text(v) and normalize_text(v) != "Sem vendedor"
    })
    return sellers or ["Sem vendedor"]


def resolve_cadastro_tipo(
    tenant_id: str | None,
    sheet_row: int,
    *,
    cnpj: str = "",
) -> str:
    if sheet_row:
        stored = get_lead_action(tenant_id, sheet_row) or {}
        tipo = normalize_text(stored.get("cadastro_tipo")).lower()
        if tipo in {"lead", "empresa"}:
            return tipo
    if normalize_text(cnpj):
        return "empresa"
    return "lead"


def save_cadastro_tipo(tenant_id: str | None, sheet_row: int, tipo: str) -> None:
    if not sheet_row:
        return
    normalized = "empresa" if normalize_text(tipo).lower() == "empresa" else "lead"
    save_lead_action(tenant_id, sheet_row, {"cadastro_tipo": normalized})


def _cadastro_initials(name: str) -> str:
    parts = [part for part in str(name or "").split() if part]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _format_registered_at(data_chamado: str, *, cadastro_tipo: str) -> str:
    label = "Lead" if cadastro_tipo == "lead" else "Empresa"
    raw = normalize_text(data_chamado)
    if not raw:
        return f"{label} cadastrado"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(raw[:10], fmt)
            time_part = ""
            if len(raw) > 10 and ":" in raw:
                time_part = f" às {raw.split(' ', 1)[-1][:5]}"
            return f"{label} cadastrado em {parsed.strftime('%d/%m/%Y')}{time_part}"
        except ValueError:
            continue
    return f"{label} cadastrado em {raw}"


def _resolve_pipeline_display_key(etapa: str, status: str) -> str:
    grouped = status_group(status)
    lost_statuses = {"Sem interesse", "Fechada perdida", "Encerrada", "Perdido"}
    if grouped in lost_statuses or "perd" in grouped.lower():
        return "perdido"
    if etapa == "Fechado" or grouped in {"Fechado", "Fechada ganha"}:
        return "ganho"
    if etapa == "Negociação":
        return "negociacao"
    if etapa == "Proposta":
        return "proposta"
    if etapa in {"Qualificação", "Reunião", "Retorno", "Contato"}:
        return "qualificado"
    return "novo"


def _build_pipeline_steps(current_key: str) -> list[dict]:
    order = [step["key"] for step in CADASTRO_PIPELINE_STEPS]
    current_index = order.index(current_key) if current_key in order else 0
    steps: list[dict] = []
    for index, step in enumerate(CADASTRO_PIPELINE_STEPS):
        steps.append({
            **step,
            "is_active": step["key"] == current_key,
            "is_done": index < current_index,
        })
    return steps


def _format_proposal_value(value: str) -> str:
    raw = normalize_text(value)
    if not raw or raw == "—":
        return "—"
    if raw.lower().startswith("r$"):
        return raw
    return raw


def build_cadastro_edit_page_context(
    *,
    tenant_id: str | None,
    sheet_row: int,
    row,
    columns: dict,
    values: dict,
    vendedor: str,
    current_status: str,
    data_chamado: str,
    cadastro_tipo: str,
    activities: list[dict],
    interactions: list[dict],
) -> dict:
    from app.services.leads import map_etapa

    lead_action = get_lead_action(tenant_id, sheet_row) or {}
    etapa = map_etapa(current_status, lead_action)
    pipeline_key = _resolve_pipeline_display_key(etapa, current_status)

    last_activity = activities[0] if activities else None
    if last_activity:
        last_activity_label = last_activity.get("title") or "Atividade registrada"
        if last_activity.get("when_date") and last_activity.get("when_date") != "—":
            last_activity_when = f"{last_activity['when_date']} às {last_activity.get('when_time', '')}".strip()
        else:
            last_activity_when = "Sem data"
    elif interactions:
        last_activity_label = interactions[0].get("description") or "Interação registrada"
        last_activity_when = interactions[0].get("at") or "—"
    else:
        last_activity_label = "Nenhuma atividade"
        last_activity_when = "—"

    next_action_date = normalize_text(lead_action.get("next_action_date"))
    next_action_time = normalize_text(lead_action.get("next_action_time")) or "09:00"
    next_action_description = normalize_text(lead_action.get("next_action_description"))
    if next_action_description and next_action_date:
        proxima_acao = next_action_description
        proxima_acao_when = f"{next_action_date} às {next_action_time}"
    else:
        proxima_acao = "Definir próxima ação"
        proxima_acao_when = "—"

    valor_proposta = _format_proposal_value(values.get("valor_proposta", ""))
    servico = normalize_text(values.get("servico"))
    proposals_count = 1 if servico or (valor_proposta and valor_proposta != "—") else 0

    empresa = normalize_text(values.get("empresa")) or "—"
    proposals_href = f"/propostas?search={empresa}" if empresa != "—" else "/propostas"

    return {
        "header_initials": _cadastro_initials(empresa),
        "header_subtitle": _format_registered_at(data_chamado, cadastro_tipo=cadastro_tipo),
        "header_id": f"ID #EMP-{sheet_row}",
        "pipeline_steps": _build_pipeline_steps(pipeline_key),
        "summary_cards": [
            {
                "icon": "🚩",
                "label": "Etapa atual",
                "value": etapa,
                "hint": STAGE_SUMMARY_HINTS.get(etapa, "Acompanhamento comercial"),
            },
            {
                "icon": "👤",
                "label": "Vendedor responsável",
                "value": vendedor or "Sem vendedor",
                "hint": "Responsável comercial",
            },
            {
                "icon": "📞",
                "label": "Última atividade",
                "value": last_activity_when,
                "hint": last_activity_label,
            },
            {
                "icon": "📅",
                "label": "Próxima ação",
                "value": proxima_acao,
                "hint": proxima_acao_when,
            },
            {
                "icon": "💰",
                "label": "Valor da proposta",
                "value": valor_proposta if valor_proposta != "—" else "—",
                "hint": servico or "Proposta em elaboração",
            },
        ],
        "proposals_count": proposals_count,
        "proposals_href": proposals_href,
        "commercial_summary": {
            "servico": servico or "—",
            "valor_proposta": valor_proposta,
            "colaboradores": normalize_text(values.get("colaboradores")) or "—",
            "has_data": bool(servico or (valor_proposta and valor_proposta != "—")),
        },
    }
