"""Leads e Empresas — KPIs e tabela."""
from datetime import datetime

import pandas as pd

from config.crm_options import PIPELINE_STAGE_BADGE, PIPELINE_STAGE_OPTIONS
from app.services.crm_validation_service import resolve_pipeline_stage
from app.services.legacy_core import safe_series, status_group

ETAPA_STAGES = PIPELINE_STAGE_OPTIONS
ETAPA_BADGE = PIPELINE_STAGE_BADGE

ACTIVE_STATUSES = {
    "Novo Lead", "Chamado Whats", "Conversando", "Reunião", "Proposta",
    "Retornar", "Ligação", "Ligação - Conversando Whats", "Ligação retornar",
    "Sem Resposta", "Não responde",
}

OPPORTUNITY_STATUSES = {"Conversando", "Reunião", "Proposta", "Retornar", "Ligação - Conversando Whats", "Negociação"}


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


def _format_contact_date(value) -> str:
    if not value or pd.isna(value):
        return "—"
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = pd.to_datetime(value)
        except Exception:
            return str(value)
    today = datetime.now().date()
    d = dt.date() if hasattr(dt, "date") else dt
    if d == today:
        return f"Hoje, {dt.strftime('%H:%M')}"
    return dt.strftime("%d/%m/%Y")


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def build_leads_kpi_cards(filtered_df: pd.DataFrame) -> list[dict]:
    total = len(filtered_df)
    companies = filtered_df["_empresa"].replace("", pd.NA).dropna().nunique() if not filtered_df.empty else 0
    active = 0
    opportunities = 0
    negotiation_value = 0.0

    if not filtered_df.empty:
        for _, row in filtered_df.iterrows():
            status = row.get("_status_grupo") or row.get("_status_original") or ""
            grouped = status_group(status)
            etapa = map_etapa(status)
            if grouped not in ("Fechado", "Sem interesse") or etapa != "Fechado":
                active += 1
            if etapa in {"Qualificação", "Reunião", "Proposta", "Retorno", "Negociação"} or grouped in OPPORTUNITY_STATUSES:
                opportunities += 1
                negotiation_value += float(row.get("_capital_num") or 0)

    return [
        {"label": "Total de Leads", "value": total, "note": "no período filtrado", "icon": "👥", "tone": "purple"},
        {"label": "Empresas", "value": companies, "note": "cadastros únicos", "icon": "🏢", "tone": "violet"},
        {"label": "Leads Ativos", "value": active, "note": "em andamento", "icon": "🔥", "tone": "orange"},
        {"label": "Oportunidades", "value": opportunities, "note": "com potencial", "icon": "🤝", "tone": "pink"},
        {"label": "Valor em Negociação", "value": _format_money(negotiation_value), "note": "estimado", "icon": "💰", "tone": "green"},
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
        result = result.sort_values("_capital_num", ascending=False)
    else:
        result = result.sort_values(["_data_chamado", "_empresa"], ascending=[False, True])

    return result


def build_leads_table(
    filtered_df: pd.DataFrame,
    columns: dict,
    tab: str = "todos",
    stage: str = "Todas as etapas",
    sort: str = "recent",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    view_df = apply_leads_view(filtered_df, tab, stage, sort)
    total = len(view_df)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    slice_df = view_df.iloc[start : start + per_page]

    rows = []
    for _, row in slice_df.iterrows():
        status_raw = row.get("_status_grupo") or row.get("_status_original") or ""
        etapa = map_etapa(status_raw)
        vendedor = str(row.get("_vendedor", "") or "Sem vendedor").strip() or "Sem vendedor"
        sheet_row = int(row.get("_sheet_row", 0) or 0)
        rows.append({
            "empresa": str(row.get("_empresa", "") or "—"),
            "contato": _initials(str(row.get("_empresa", ""))),
            "vendedor": vendedor,
            "vendedor_initials": _initials(vendedor),
            "etapa": etapa,
            "etapa_class": ETAPA_BADGE.get(etapa, "novo-lead"),
            "status": status_group(status_raw),
            "ultimo_contato": _format_contact_date(row.get("_ultima_atualizacao") or row.get("_data_chamado")),
            "valor": _format_money(row.get("_capital_num")),
            "sheet_row": sheet_row,
            "href": f"/cadastro/todos/{sheet_row}" if sheet_row else "/cadastro/todos",
        })

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "from_record": start + 1 if total else 0,
        "to_record": min(start + per_page, total),
    }
