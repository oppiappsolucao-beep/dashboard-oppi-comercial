"""Leads e Empresas — KPIs e tabela."""
from datetime import date, datetime, timedelta

import pandas as pd

from config.crm_options import NEXT_ACTION_BY_STAGE, PIPELINE_STAGE_BADGE, PIPELINE_STAGE_OPTIONS
from app.services.crm_validation_service import resolve_pipeline_stage
from app.services.followup_service import _email_for_row, _phone_for_row, _whatsapp_href
from app.services.lead_actions_storage import get_lead_action
from app.services.legacy_core import deal_value_from_row, row_field_value, safe_series, status_group

ETAPA_STAGES = PIPELINE_STAGE_OPTIONS
ETAPA_BADGE = PIPELINE_STAGE_BADGE

ACTIVE_STATUSES = {
    "Novo Lead", "Chamado Whats", "Conversando", "Reunião", "Proposta",
    "Retornar", "Ligação", "Ligação - Conversando Whats", "Ligação retornar",
    "Sem Resposta", "Não responde",
}

OPPORTUNITY_STATUSES = {"Conversando", "Reunião", "Proposta", "Retornar", "Ligação - Conversando Whats", "Negociação"}

NEXT_ACTION_ICONS = {
    "whatsapp": "💬",
    "email": "✉",
    "ligacao": "📞",
    "telefone": "📞",
    "reuniao": "📅",
    "reunião": "📅",
}


def map_etapa(status: str, stored: dict | None = None) -> str:
    grouped = status_group(status)
    return resolve_pipeline_stage(grouped, stored)


def _format_money(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number <= 0:
        return "—"
    formatted = f"{number:,.0f}".replace(",", ".")
    return f"R$ {formatted}"


def _as_datetime(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return pd.to_datetime(value).to_pydatetime()
    except Exception:
        return None


def _format_contact_date(value) -> str:
    dt = _as_datetime(value)
    if not dt:
        return "—"
    today = datetime.now().date()
    d = dt.date()
    if d == today:
        return f"Hoje, {dt.strftime('%H:%M')}"
    if d == today - timedelta(days=1):
        return f"Ontem, {dt.strftime('%H:%M')}"
    return dt.strftime("%d/%m/%Y")


def _format_relative_days(value) -> str:
    dt = _as_datetime(value)
    if not dt:
        return "—"
    days = max(0, (datetime.now().date() - dt.date()).days)
    if days == 0:
        return "hoje"
    if days == 1:
        return "há 1 dia"
    return f"há {days} dias"


def _format_next_action_schedule(action_date, action_time: str) -> str:
    if not action_date:
        return "—"
    if isinstance(action_date, str):
        try:
            parsed_date = date.fromisoformat(action_date[:10])
        except ValueError:
            return "—"
    else:
        dt = _as_datetime(action_date)
        if not dt:
            return "—"
        parsed_date = dt.date()

    today = date.today()
    time_part = (action_time or "09:00")[:5]
    if parsed_date == today:
        return f"Hoje, {time_part}"
    if parsed_date == today + timedelta(days=1):
        return f"Amanhã, {time_part}"
    return f"{parsed_date.strftime('%d/%m/%Y')}, {time_part}"


def _next_action_icon(channel: str) -> str:
    normalized = str(channel or "").strip().lower()
    for key, icon in NEXT_ACTION_ICONS.items():
        if key in normalized:
            return icon
    return "📞"


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _is_active_row(row) -> bool:
    status = row.get("_status_grupo") or row.get("_status_original") or ""
    grouped = status_group(status)
    etapa = map_etapa(status)
    return grouped not in ("Fechado", "Sem interesse") or etapa != "Fechado"


def _is_opportunity_row(row) -> bool:
    status = row.get("_status_grupo") or row.get("_status_original") or ""
    grouped = status_group(status)
    etapa = map_etapa(status)
    return etapa in {"Qualificação", "Reunião", "Proposta", "Retorno", "Negociação"} or grouped in OPPORTUNITY_STATUSES


def _month_start() -> datetime:
    now = datetime.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _count_this_month(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    start = _month_start()
    count = 0
    for _, row in df.iterrows():
        dt = _as_datetime(row.get("_data_chamado"))
        if dt and dt >= start:
            count += 1
    return count


def _pct(part: int, whole: int) -> int:
    if not whole:
        return 0
    return round(part / whole * 100)


def build_leads_kpi_cards(filtered_df: pd.DataFrame) -> list[dict]:
    total = len(filtered_df)
    companies = filtered_df["_empresa"].replace("", pd.NA).dropna().nunique() if not filtered_df.empty else 0
    active = 0
    opportunities = 0
    negotiation_value = 0.0
    total_pipeline_value = 0.0

    if not filtered_df.empty:
        for _, row in filtered_df.iterrows():
            if _is_active_row(row):
                active += 1
            if _is_opportunity_row(row):
                opportunities += 1
                negotiation_value += deal_value_from_row(row)
            total_pipeline_value += deal_value_from_row(row)

    added_this_month = _count_this_month(filtered_df)
    month_note = f"+{added_this_month} este mês"
    active_pct = _pct(active, total)
    conversion_pct = _pct(opportunities, total)
    value_pct = _pct(int(negotiation_value), int(total_pipeline_value)) if total_pipeline_value else 0

    return [
        {"label": "Total de Leads", "value": total, "note": month_note, "icon": "👥", "tone": "purple"},
        {"label": "Empresas", "value": companies, "note": month_note, "icon": "🏢", "tone": "blue"},
        {"label": "Leads Ativos", "value": active, "note": f"{active_pct}% do total", "icon": "🔥", "tone": "orange"},
        {"label": "Oportunidades", "value": opportunities, "note": f"{conversion_pct}% conversão", "icon": "🤝", "tone": "pink"},
        {
            "label": "Valor em Negociação",
            "value": _format_money(negotiation_value),
            "note": f"{value_pct}% do total",
            "icon": "💰",
            "tone": "green",
        },
    ]


def apply_leads_view(df: pd.DataFrame, tab: str, stage: str, sort: str) -> pd.DataFrame:
    result = df.copy()
    if result.empty:
        return result

    if tab == "empresas":
        result = result.sort_values(["_empresa", "_data_chamado"], ascending=[True, False])
        result = result.drop_duplicates(subset=["_empresa"], keep="first")

    if stage and stage != "Todas as etapas":
        result = result[
            result.apply(
                lambda row: map_etapa(row.get("_status_grupo") or row.get("_status_original", "")) == stage,
                axis=1,
            )
        ]

    if sort == "name":
        result = result.sort_values("_empresa", ascending=True)
    elif sort == "value":
        result = result.sort_values("_valor_proposta_num", ascending=False)
    else:
        result = result.sort_values(["_data_chamado", "_empresa"], ascending=[False, True])

    return result


def _build_row(row, columns: dict, tab: str, tenant_id: str | None) -> dict:
    status_raw = row.get("_status_grupo") or row.get("_status_original") or ""
    stored = get_lead_action(tenant_id, int(row.get("_sheet_row", 0) or 0)) or {}
    etapa = map_etapa(status_raw, stored)
    vendedor = str(row.get("_vendedor", "") or "Sem vendedor").strip() or "Sem vendedor"
    sheet_row = int(row.get("_sheet_row", 0) or 0)
    empresa = str(row.get("_empresa", "") or "—")
    socio = row_field_value(row, columns, "socio_1")
    nome_display = socio or empresa
    telefone = _phone_for_row(row, columns) or "—"
    email = _email_for_row(row, columns) or "—"
    last_contact_raw = row.get("_ultima_atualizacao") or row.get("_data_chamado")

    next_action_date = stored.get("next_action_date")
    next_action_time = str(stored.get("next_action_time") or "09:00")
    next_action_completed = bool(stored.get("next_action_completed"))
    next_action_type = str(stored.get("next_action_type") or "whatsapp")
    next_action_description = str(stored.get("next_action_description") or "").strip()

    if next_action_completed or not next_action_date:
        proxima_acao = "—"
        proxima_acao_horario = ""
    else:
        proxima_acao = next_action_description or NEXT_ACTION_BY_STAGE.get(etapa, "Definir próxima ação")
        proxima_acao_horario = _format_next_action_schedule(next_action_date, next_action_time)

    tipo_label = "Empresa" if tab == "empresas" else "Lead"

    return {
        "nome": nome_display,
        "empresa": empresa,
        "empresa_initials": _initials(nome_display),
        "tipo_label": tipo_label,
        "telefone": telefone,
        "email": email,
        "vendedor": vendedor,
        "vendedor_initials": _initials(vendedor),
        "etapa": etapa,
        "etapa_class": ETAPA_BADGE.get(etapa, "novo-lead"),
        "status": status_group(status_raw),
        "ultimo_contato": _format_contact_date(last_contact_raw),
        "ultimo_contato_relativo": _format_relative_days(last_contact_raw),
        "proxima_acao": proxima_acao,
        "proxima_acao_horario": proxima_acao_horario,
        "next_action_icon": _next_action_icon(next_action_type),
        "valor": _format_money(deal_value_from_row(row)),
        "valor_num": deal_value_from_row(row),
        "whatsapp_href": _whatsapp_href(telefone if telefone != "—" else ""),
        "sheet_row": sheet_row,
        "href": f"/cadastro/todos/{sheet_row}" if sheet_row else "/cadastro/todos",
    }


def build_leads_table(
    filtered_df: pd.DataFrame,
    columns: dict,
    tab: str = "todos",
    stage: str = "Todas as etapas",
    sort: str = "recent",
    page: int = 1,
    per_page: int = 10,
    tenant_id: str | None = None,
) -> dict:
    view_df = apply_leads_view(filtered_df, tab, stage, sort)
    total = len(view_df)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    slice_df = view_df.iloc[start : start + per_page]

    rows = [_build_row(row, columns, tab, tenant_id) for _, row in slice_df.iterrows()]

    page_numbers = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "from_record": start + 1 if total else 0,
        "to_record": min(start + per_page, total),
        "page_numbers": page_numbers,
    }


def build_leads_export_rows(filtered_df: pd.DataFrame, columns: dict, tab: str, stage: str, sort: str, tenant_id: str | None) -> list[dict]:
    view_df = apply_leads_view(filtered_df, tab, stage, sort)
    return [_build_row(row, columns, tab, tenant_id) for _, row in view_df.iterrows()]
