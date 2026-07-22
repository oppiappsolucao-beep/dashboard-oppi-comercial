"""Validação e payloads de cadastro."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from app.services.lead_actions_storage import get_lead_action, save_lead_action
from app.services.legacy_core import (
    DuplicateRegistrationError,
    STATUS_OPTIONS,
    append_company_to_sheet,
    as_python_date,
    normalize_cnpj_for_duplicate,
    normalize_phone_for_duplicate,
    normalize_text,
    parse_date,
    status_group,
    update_company_in_sheet,
)


CADASTRO_TIPO_OPTIONS = [
    {"value": "lead", "label": "Lead"},
    {"value": "empresa", "label": "Empresa"},
]

CADASTRO_PIPELINE_STEPS = [
    {"key": "novo", "label": "Novo Lead"},
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

EDIT_FIELDS_PRESERVE_WHEN_ABSENT = ("status", "data_chamado", "servico", "valor_proposta")


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
    else:
        raw = normalize_text(data_chamado)
        # Converte ISO (input date) para o padrão da planilha.
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            try:
                data_chamado = datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                data_chamado = raw
        else:
            data_chamado = raw

    payload = {field: normalize_text(form.get(field, "")) for field in REGISTRATION_FIELDS}
    payload["data_chamado"] = normalize_text(data_chamado)
    payload["ultima_atualizacao"] = now_text
    tipo = normalize_text(form.get("cadastro_tipo")).lower()
    payload["cadastro_tipo"] = "empresa" if tipo == "empresa" else "lead"
    return payload


def save_new_company(form: dict) -> int:
    error = validate_registration_form(form)
    if error:
        raise ValueError(error)
    return append_company_to_sheet(build_registration_payload(form))


def _existing_edit_field_values(sheet_row: int) -> dict[str, str]:
    from app.dependencies import get_prepared_data
    from app.services.legacy_core import status_group

    df, columns = get_prepared_data()
    matches = df[df["_sheet_row"] == int(sheet_row)]
    if matches.empty:
        return {}

    row = matches.iloc[0]

    def cell(key: str) -> str:
        column_name = columns.get(key)
        if column_name and column_name in row.index:
            return normalize_text(row.get(column_name, ""))
        return ""

    return {
        "status": status_group(row.get("_status_original", row.get("_status_grupo", "Novo Lead"))),
        "data_chamado": cell("data_chamado"),
        "servico": cell("servico"),
        "valor_proposta": cell("valor_proposta"),
    }


def save_company_edit(sheet_row: int, form: dict) -> None:
    error = validate_registration_form(form)
    if error:
        raise ValueError(error)
    payload = build_registration_payload(form)
    existing = _existing_edit_field_values(sheet_row)
    for field in EDIT_FIELDS_PRESERVE_WHEN_ABSENT:
        if field not in form:
            payload[field] = normalize_text(existing.get(field, ""))
    update_company_in_sheet(sheet_row, payload)


def delete_company_registration(tenant_id: str | None, sheet_row: int) -> None:
    from app.services.legacy_core import delete_company_from_sheet
    from app.services.lead_actions_storage import delete_lead_action

    delete_company_from_sheet(sheet_row)
    delete_lead_action(tenant_id, sheet_row)


SELLER_ROLES = {"Vendedor"}
_FAKE_SELLER_USERNAMES = frozenset({"usuario.fake", "usuario.fake.test"})
_DEFAULT_ALLOWED_SELLERS = ("Raissa", "Raíssa", "Higo Silva")


def _normalize_person_name(value: str) -> str:
    return normalize_text(value)


def _is_fake_or_test_seller(name: str, username: str = "") -> bool:
    lowered_name = _normalize_person_name(name).lower()
    lowered_username = _normalize_person_name(username).lower()
    if not lowered_name and not lowered_username:
        return True
    if "fake" in lowered_name or " fake " in f" {lowered_name} ":
        return True
    if lowered_username in _FAKE_SELLER_USERNAMES:
        return True
    return False


def _is_admin_login(name: str) -> bool:
    from app.config import settings

    admin_login = _normalize_person_name(settings.app_username).lower()
    return bool(admin_login) and _normalize_person_name(name).lower() == admin_login


def _allowed_seller_names() -> set[str]:
    import os

    raw = os.getenv("ALLOWED_SELLERS", "").strip()
    if raw:
        parts = [_normalize_person_name(part).lower() for part in raw.split(",")]
        return {part for part in parts if part}
    return {_normalize_person_name(part).lower() for part in _DEFAULT_ALLOWED_SELLERS}


def get_seller_options(df) -> list[str]:
    names: set[str] = set()

    try:
        from app.services.account_users import load_account_users

        for user in load_account_users():
            if not user.get("active", True):
                continue
            if user.get("role") not in SELLER_ROLES:
                continue
            name = _normalize_person_name(user.get("name", ""))
            username = _normalize_person_name(user.get("username", ""))
            if not name or name == "Sem vendedor":
                continue
            if _is_fake_or_test_seller(name, username) or _is_admin_login(name):
                continue
            names.add(name)
    except Exception:
        pass

    if df is not None and not getattr(df, "empty", True) and "_vendedor" in df.columns:
        for value in df["_vendedor"].tolist():
            clean = _normalize_person_name(value)
            if not clean or clean == "Sem vendedor":
                continue
            if _is_fake_or_test_seller(clean) or _is_admin_login(clean):
                continue
            names.add(clean)

    allowed = _allowed_seller_names()
    names = {name for name in names if name.lower() in allowed}

    return sorted(names, key=str.lower) or ["Sem vendedor"]


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


ACCESS_FIELDS = (
    "email_login_gestor",
    "email_confirmacao_admin",
    "email_cobranca",
    "senha_acesso",
)


def load_access_fields(tenant_id: str | None, sheet_row: int) -> dict[str, str]:
    if not sheet_row:
        return {field: "" for field in ACCESS_FIELDS}
    stored = get_lead_action(tenant_id, sheet_row) or {}
    return {field: normalize_text(stored.get(field)) for field in ACCESS_FIELDS}


def save_access_fields(tenant_id: str | None, sheet_row: int, form: dict) -> None:
    if not sheet_row:
        return
    payload = {field: normalize_text(form.get(field)) for field in ACCESS_FIELDS}
    save_lead_action(tenant_id, sheet_row, payload)


def save_cadastro_tipo(tenant_id: str | None, sheet_row: int, tipo: str) -> None:
    if not sheet_row:
        return
    normalized = "empresa" if normalize_text(tipo).lower() == "empresa" else "lead"
    save_lead_action(tenant_id, sheet_row, {"cadastro_tipo": normalized})


def is_cadastro_ativo(tenant_id: str | None, sheet_row: int) -> bool:
    if not sheet_row:
        return True
    stored = get_lead_action(tenant_id, sheet_row) or {}
    if "cadastro_ativo" not in stored:
        return True
    raw = stored.get("cadastro_ativo")
    if isinstance(raw, bool):
        return raw
    text = normalize_text(raw).lower()
    if text in {"0", "false", "nao", "não", "inativo", "desativado", "off", "no"}:
        return False
    return True


def save_cadastro_ativo(tenant_id: str | None, sheet_row: int, ativo: bool) -> None:
    if not sheet_row:
        return
    save_lead_action(tenant_id, sheet_row, {"cadastro_ativo": bool(ativo)})


NICHE_OPTIONS = [
    "Marmoraria",
    "Marcenaria",
    "Academia",
    "Clínica",
    "Pet shop",
    "Construção civil",
    "Restaurante",
    "Loja",
    "Serviços",
    "Outros",
]


def resolve_nicho(
    tenant_id: str | None,
    sheet_row: int,
    *,
    empresa: str = "",
    fallback: str = "",
) -> str:
    if sheet_row:
        stored = get_lead_action(tenant_id, sheet_row) or {}
        nicho = normalize_text(stored.get("nicho"))
        if nicho:
            return nicho
    fallback_nicho = normalize_text(fallback)
    if fallback_nicho:
        return fallback_nicho
    from app.services.legacy_core import infer_niche_from_company_name

    return infer_niche_from_company_name(empresa)


def save_nicho(tenant_id: str | None, sheet_row: int, nicho: str) -> None:
    if not sheet_row:
        return
    normalized = normalize_text(nicho)
    save_lead_action(tenant_id, sheet_row, {"nicho": normalized})


def _cadastro_initials(name: str) -> str:
    parts = [part for part in str(name or "").split() if part]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _format_registered_at(data_chamado: str, *, cadastro_tipo: str) -> str:
    verb = "Cadastrado" if cadastro_tipo == "lead" else "Cadastrada"
    raw = normalize_text(data_chamado)
    if not raw:
        return f"{verb}"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(raw[:10], fmt)
            return f"{verb} em {parsed.strftime('%d/%m/%Y')}"
        except ValueError:
            continue
    return f"{verb} em {raw}"


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
    lead_action = get_lead_action(tenant_id, sheet_row) or {}
    forma_pagamento = normalize_text(lead_action.get("forma_pagamento")) or "—"
    vencimento_raw = normalize_text(lead_action.get("vencimento"))
    vencimento = vencimento_raw or "—"
    if vencimento_raw:
        parsed_venc = as_python_date(parse_date(vencimento_raw))
        if parsed_venc:
            vencimento = parsed_venc.strftime("%d/%m/%Y")
    proposals_count = 1 if servico or (valor_proposta and valor_proposta != "—") else 0

    empresa = normalize_text(values.get("empresa")) or "—"
    proposals_href = f"/propostas?search={empresa}" if empresa != "—" else "/propostas"

    valor_display = valor_proposta if valor_proposta not in {"", "—"} else "—"
    if valor_display != "—" and not str(valor_display).upper().startswith("R$"):
        valor_display = f"R$ {valor_display}"
    elif valor_display == "—":
        valor_display = "R$ —"

    return {
        "header_initials": _cadastro_initials(empresa),
        "header_subtitle": _format_registered_at(data_chamado, cadastro_tipo=cadastro_tipo),
        "header_id": f"ID #EMP-{sheet_row}",
        "pipeline_steps": _build_pipeline_steps(pipeline_key),
        "summary_cards": [
            {
                "icon": "🚩",
                "label": "Etapa Atual",
                "value": etapa,
                "hint": STAGE_SUMMARY_HINTS.get(etapa, "Acompanhamento comercial"),
            },
            {
                "icon": "👤",
                "label": "Vendedor Responsável",
                "value": vendedor or "Sem vendedor",
                "hint": "Responsável comercial",
            },
            {
                "icon": "📅",
                "label": "Última Atividade",
                "value": last_activity_when,
                "hint": last_activity_label,
            },
            {
                "icon": "✅",
                "label": "Próxima Ação",
                "value": proxima_acao,
                "hint": proxima_acao_when,
            },
            {
                "icon": "💲",
                "label": "Valor da Proposta",
                "value": valor_display,
                "hint": servico or "Proposta em elaboração",
            },
        ],
        "proposals_count": proposals_count,
        "proposals_href": proposals_href,
        "commercial_summary": {
            "servico": servico or "—",
            "valor_proposta": valor_proposta,
            "forma_pagamento": forma_pagamento,
            "vencimento": vencimento,
            "colaboradores": normalize_text(values.get("colaboradores")) or "—",
            "has_data": bool(servico or (valor_proposta and valor_proposta != "—")),
        },
    }


def build_cadastro_new_page_context(
    *,
    values: dict,
    cadastro_tipo: str,
    vendedor: str,
) -> dict:
    empresa = normalize_text(values.get("empresa"))
    tipo_label = "Lead" if cadastro_tipo == "lead" else "Empresa"
    display_name = empresa or "Novo cadastro"

    return {
        "header_initials": _cadastro_initials(empresa) if empresa else "NL",
        "header_subtitle": f"{tipo_label} · cadastro em andamento",
        "header_id": "Novo cadastro",
        "client_edit_title": display_name,
        "pipeline_steps": _build_pipeline_steps("novo"),
        "summary_cards": [
            {
                "icon": "🚩",
                "label": "Etapa atual",
                "value": "Novo Lead",
                "hint": STAGE_SUMMARY_HINTS.get("Novo Lead", "Início do relacionamento"),
            },
            {
                "icon": "👤",
                "label": "Vendedor responsável",
                "value": vendedor or "Selecionar",
                "hint": "Responsável comercial",
            },
            {
                "icon": "📞",
                "label": "Última atividade",
                "value": "—",
                "hint": "Nenhuma atividade ainda",
            },
            {
                "icon": "📅",
                "label": "Próxima ação",
                "value": "Primeira atividade",
                "hint": "Configure na aba Atividades",
            },
            {
                "icon": "💰",
                "label": "Valor da proposta",
                "value": "—",
                "hint": "Proposta em elaboração",
            },
        ],
        "proposals_count": 0,
        "proposals_href": "/propostas",
        "commercial_summary": {
            "servico": "—",
            "valor_proposta": "—",
            "forma_pagamento": "—",
            "vencimento": "—",
            "colaboradores": "—",
            "has_data": False,
        },
    }
