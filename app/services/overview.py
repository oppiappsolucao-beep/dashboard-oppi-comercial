"""Métricas, gráficos e componentes da Visão Geral."""
import html
import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from config.crm_options import OVERVIEW_FUNNEL_STAGES as CRM_FUNNEL_STAGES, PIPELINE_STAGE_OPTIONS, PIPELINE_STAGE_SHEET_STATUSES
from app.services.filters import DashboardFilters, apply_dashboard_filters
from app.services.leads import ETAPA_BADGE, map_etapa
from app.services.legacy_core import (
    DASHBOARD_STATUS_OPTIONS,
    STATUS_COLORS,
    apply_period_filter,
    as_python_date,
    count_dashboard_status,
    deal_value_from_row,
    normalize_digits,
    normalize_text,
    row_matches_dashboard_card,
    safe_series,
    status_group,
)

FUNNEL_STAGES = [(stage, PIPELINE_STAGE_SHEET_STATUSES.get(stage, [])) for stage in PIPELINE_STAGE_OPTIONS]

FUNNEL_PAGE_STAGES = FUNNEL_STAGES

FUNNEL_PAGE_ACTIONS = [
    ("Retornos de hoje", ["Retornar", "Ligação retornar", "Sem Resposta"], "action-purple"),
    ("Propostas aguardando retorno", ["Proposta"], "action-pink"),
    ("Reuniões agendadas", ["Reunião"], "action-indigo"),
    ("Contatos sem retorno", ["Sem Resposta", "Não responde"], "action-green"),
]

OVERVIEW_FUNNEL_STAGES = CRM_FUNNEL_STAGES

OPPORTUNITY_STATUSES = {"Conversando", "Reunião", "Proposta", "Retornar", "Ligação - Conversando Whats", "Ligação retornar", "Negociação"}
COMPLETED_STATUSES = {"Fechado", "Sem interesse"}

DAILY_ACTION_SLOTS = ["09:00", "10:30", "14:00", "16:00", "18:30"]
DAILY_ACTION_DEFS = [
    ("Ligar para", ["Ligação", "Ligação - Conversando Whats", "Ligação retornar", "Retornar"], "phone", "action-purple"),
    ("Retornar WhatsApp -", ["Chamado Whats", "Conversando"], "whatsapp", "action-pink"),
    ("Enviar proposta -", ["Proposta"], "email", "action-blue"),
    ("Confirmar reunião -", ["Reunião"], "calendar", "action-green"),
    ("Acompanhar negociação -", ["Retornar", "Ligação retornar", "Proposta"], "check", "action-orange"),
]

OVERDUE_ACTION_DEFS = [
    ("Ligar para (WhatsApp) -", ["Chamado Whats", "Conversando", "Sem Resposta"], "phone"),
    ("Enviar proposta -", ["Proposta"], "email"),
    ("Acompanhar negociação -", ["Retornar", "Ligação retornar"], "check"),
]

ACTION_STATUS_MAP = [
    ("Retornar contatos hoje", ["Retornar", "Ligação retornar"], "action-purple"),
    ("Responder propostas", ["Proposta"], "action-pink"),
    ("Agendar reuniões", ["Reunião"], "action-indigo"),
    ("Enviar propostas", ["Proposta"], "action-rose"),
    ("Acompanhar no WhatsApp", ["Chamado Whats", "Conversando"], "action-green"),
]


def _count_statuses(filtered_df: pd.DataFrame, names: list[str]) -> int:
    return sum(count_dashboard_status(filtered_df, name) for name in names)


def _format_money(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number <= 0:
        return "R$ 0"
    formatted = f"{number:,.0f}".replace(",", ".")
    return f"R$ {formatted}"


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _as_date(value) -> date | None:
    return as_python_date(value)


def _period_trend(current: float, previous: float, is_points: bool = False) -> dict:
    if is_points:
        delta = round(current - previous, 1)
        sign = "+" if delta >= 0 else "-"
        return {
            "trend_label": f"{sign} {abs(delta):,.1f} p.p. vs período anterior".replace(",", "."),
            "trend_up": delta >= 0,
            "trend_flat": delta == 0,
        }
    if previous == 0:
        pct = 100 if current > 0 else 0
    else:
        pct = round(((current - previous) / previous) * 100)
    sign = "+" if pct >= 0 else "-"
    return {
        "trend_label": f"{sign} {abs(pct)}% vs período anterior",
        "trend_up": pct >= 0,
        "trend_flat": pct == 0,
    }


def _month_bounds(reference: date) -> tuple[date, date]:
    start = reference.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    return start, end


def _count_opportunities(filtered_df: pd.DataFrame) -> int:
    if filtered_df.empty:
        return 0
    return int(
        filtered_df.apply(
            lambda row: status_group(row.get("_status_grupo") or row.get("_status_original", "")) in OPPORTUNITY_STATUSES,
            axis=1,
        ).sum()
    )


def _negotiation_value(filtered_df: pd.DataFrame) -> float:
    if filtered_df.empty:
        return 0.0
    total = 0.0
    for _, row in filtered_df.iterrows():
        grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
        if grouped in OPPORTUNITY_STATUSES or grouped == "Novo Lead":
            total += deal_value_from_row(row)
    return total


def build_overview_kpi_cards(
    df: pd.DataFrame,
    columns: dict,
    filters: DashboardFilters,
) -> list[dict]:
    filtered_current = apply_dashboard_filters(df, columns, filters)
    filtered_prev = apply_dashboard_filters(df, columns, _previous_period_filters(filters))

    novos = count_dashboard_status(filtered_current, "Novo Lead")
    novos_prev = count_dashboard_status(filtered_prev, "Novo Lead")

    oportunidades = _count_opportunities(filtered_current)
    oportunidades_prev = _count_opportunities(filtered_prev)

    valor = _negotiation_value(filtered_current)
    valor_prev = _negotiation_value(filtered_prev)

    total = max(len(filtered_current), 1)
    total_prev = max(len(filtered_prev), 1)
    fechados = count_dashboard_status(filtered_current, "Fechado")
    fechados_prev = count_dashboard_status(filtered_prev, "Fechado")
    conversao = round((fechados / total) * 100, 1)
    conversao_prev = round((fechados_prev / total_prev) * 100, 1)

    month_start, month_end = _month_bounds(date.today())
    month_df = apply_period_filter(filtered_current.copy(), "_data_chamado", (month_start, month_end))
    month_prev_start, month_prev_end = _month_bounds((month_start - timedelta(days=1)))
    prev_month_df = apply_period_filter(filtered_current.copy(), "_data_chamado", (month_prev_start, month_prev_end))
    fechados_mes = count_dashboard_status(month_df, "Fechado")
    fechados_mes_prev = count_dashboard_status(prev_month_df, "Fechado")

    conversao_label = f"{str(conversao).replace('.', ',')}%"

    return [
        {
            "label": "Novos Leads",
            "value": novos,
            "icon": "👥",
            "tone": "purple",
            **_period_trend(novos, novos_prev),
        },
        {
            "label": "Oportunidades",
            "value": oportunidades,
            "icon": "📅",
            "tone": "pink",
            **_period_trend(oportunidades, oportunidades_prev),
        },
        {
            "label": "Valor em Negociação",
            "value": _format_money(valor),
            "icon": "💰",
            "tone": "blue",
            **_period_trend(valor, valor_prev),
        },
        {
            "label": "Taxa de Conversão",
            "value": conversao_label,
            "icon": "📈",
            "tone": "green",
            **_period_trend(conversao, conversao_prev, is_points=True),
        },
        {
            "label": "Fechados no mês",
            "value": fechados_mes,
            "icon": "✓",
            "tone": "orange",
            **_period_trend(fechados_mes, fechados_mes_prev),
        },
    ]


def build_kpi_cards(filtered_df: pd.DataFrame) -> list[dict]:
    """Compatibilidade legada — preferir build_overview_kpi_cards."""
    total = max(len(filtered_df), 1)
    fechados = count_dashboard_status(filtered_df, "Fechado")
    conversao = round((fechados / total) * 100)
    return [
        {"label": "Novos Leads", "value": count_dashboard_status(filtered_df, "Novo Lead"), "note": "", "icon": "👥", "tone": "purple"},
        {"label": "Oportunidades", "value": _count_opportunities(filtered_df), "note": "", "icon": "📅", "tone": "pink"},
        {"label": "Valor em Negociação", "value": _format_money(_negotiation_value(filtered_df)), "note": "", "icon": "💰", "tone": "blue"},
        {"label": "Taxa de Conversão", "value": f"{conversao}%", "note": "", "icon": "📈", "tone": "green"},
        {"label": "Fechados no mês", "value": fechados, "note": "", "icon": "✓", "tone": "orange"},
    ]


def build_overview_funnel(filtered_df: pd.DataFrame) -> dict:
    counts = [_count_statuses(filtered_df, statuses) for _, statuses, _ in OVERVIEW_FUNNEL_STAGES]
    max_count = max(counts) or 1
    stages = []
    for index, ((name, _, color), count) in enumerate(zip(OVERVIEW_FUNNEL_STAGES, counts)):
        width = max(34, round((count / max_count) * 100))
        stages.append({
            "name": name,
            "count": count,
            "color": color,
            "width": width,
            "level": index,
        })

    first_count = counts[0] or max(len(filtered_df), 1)
    closed_count = counts[-1]
    conversion = round((closed_count / first_count) * 100, 1) if first_count else 0.0

    return {
        "stages": stages,
        "conversion": conversion,
        "conversion_label": f"{str(conversion).replace('.', ',')}%",
    }


def build_conversion_donut_json(conversion: float) -> str:
    value = min(max(conversion, 0), 100)
    figure = go.Figure(go.Pie(
        values=[value, max(0, 100 - value)],
        hole=0.72,
        marker={"colors": ["#7C3AED", "#E5E7EB"]},
        textinfo="none",
        hoverinfo="skip",
    ))
    figure.update_layout(
        height=150,
        width=150,
        margin=dict(l=8, r=8, t=8, b=8),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return figure.to_json()


def _contact_label(row, columns: dict) -> str:
    for key in ("socio_1", "socio_2", "socio_3"):
        column = columns.get(key)
        if column:
            value = safe_series(pd.DataFrame([row]), column).iloc[0]
            if value and str(value).strip():
                return str(value).strip()
    return row.get("_empresa", "") or "Lead"


def _phone_for_row(row, columns: dict) -> str:
    for key in ("telefone_b2b", "telefone_socio_1", "telefone_fixo", "telefone_alternativo"):
        column = columns.get(key)
        if column and column in row.index:
            value = normalize_text(row.get(column, ""))
            if value:
                return value
    return normalize_text(row.get("_telefone", ""))


def _email_for_row(row, columns: dict) -> str:
    for key in ("email", "email_socio_1"):
        column = columns.get(key)
        if column and column in row.index:
            value = normalize_text(row.get(column, ""))
            if value:
                return value
    return ""


def _whatsapp_href(phone: str) -> str:
    digits = normalize_digits(phone)
    if not digits:
        return ""
    if digits.startswith("55") and len(digits) >= 12:
        pass
    elif len(digits) in (10, 11):
        digits = f"55{digits}"
    else:
        return ""
    return f"https://wa.me/{digits}"


def build_row_daily_action(row, columns: dict) -> dict | None:
    grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
    if grouped in COMPLETED_STATUSES:
        return None

    for prefix, statuses, icon, tone in DAILY_ACTION_DEFS:
        if grouped not in statuses:
            continue

        contact = _contact_label(row, columns)
        empresa = normalize_text(row.get("_empresa", "")) or "—"
        label = f"{prefix} {contact} ({empresa})" if prefix.endswith("para") else f"{prefix} {empresa}"
        sheet_row = int(row.get("_sheet_row", 0) or 0)
        telefone = _phone_for_row(row, columns)
        email = _email_for_row(row, columns)

        action = {
            "label": label,
            "action_type": prefix.replace(" -", "").strip(),
            "icon": icon,
            "tone": tone,
            "sort_date": _as_date(row.get("_ultima_atualizacao") or row.get("_data_chamado")) or date.min,
            "sheet_row": sheet_row,
            "empresa": empresa,
            "contact": contact,
            "status": grouped,
            "telefone": telefone,
            "email": email,
            "href": f"/cadastro/todos/{sheet_row}/editar" if sheet_row else "/cadastro/todos",
            "edit_href": f"/cadastro/todos/{sheet_row}/editar" if sheet_row else "/cadastro/todos",
        }
        if telefone:
            action["tel_href"] = f"tel:{normalize_digits(telefone)}"
            whatsapp_href = _whatsapp_href(telefone)
            if whatsapp_href:
                action["whatsapp_href"] = whatsapp_href
        if email:
            action["mailto_href"] = f"mailto:{email}"
        return action

    return None


def build_client_action(row, columns: dict) -> dict | None:
    """Próxima ação recomendada para um cadastro específico."""
    return build_row_daily_action(row, columns)


def build_daily_actions(filtered_df: pd.DataFrame, columns: dict) -> list[dict]:
    if filtered_df.empty:
        return []

    actionable = []
    for _, row in filtered_df.iterrows():
        action = build_row_daily_action(row, columns)
        if action:
            actionable.append(action)

    actionable.sort(key=lambda item: item["sort_date"], reverse=True)
    items = []
    for index, item in enumerate(actionable[:5]):
        items.append({
            **item,
            "time": DAILY_ACTION_SLOTS[index] if index < len(DAILY_ACTION_SLOTS) else "19:00",
        })
    return items


def build_hot_opportunities(filtered_df: pd.DataFrame) -> list[dict]:
    if filtered_df.empty:
        return []

    rows = []
    for _, row in filtered_df.iterrows():
        grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
        if grouped not in OPPORTUNITY_STATUSES and map_etapa(grouped) not in ("Proposta", "Negociação", "Reunião", "Qualificação", "Retorno"):
            continue
        deal_value = deal_value_from_row(row)
        etapa = map_etapa(grouped)
        activity_date = _as_date(row.get("_ultima_atualizacao") or row.get("_data_chamado"))
        days_idle = (date.today() - activity_date).days if activity_date else 0
        if deal_value >= 20000 or days_idle >= 5 or grouped == "Proposta":
            urgency = "Alta"
            urgency_class = "high"
        elif deal_value >= 10000 or days_idle >= 2:
            urgency = "Média"
            urgency_class = "medium"
        else:
            urgency = "Baixa"
            urgency_class = "low"

        rows.append({
            "empresa": row.get("_empresa") or "—",
            "empresa_initials": _initials(row.get("_empresa", "")),
            "etapa": etapa,
            "etapa_class": ETAPA_BADGE.get(etapa, "novo-lead"),
            "valor": _format_money(deal_value),
            "valor_num": deal_value,
            "urgencia": urgency,
            "urgencia_class": urgency_class,
            "sheet_row": int(row.get("_sheet_row", 0) or 0),
            "href": f"/cadastro/todos/{int(row.get('_sheet_row', 0) or 0)}/editar",
        })

    rows.sort(key=lambda item: (item["urgencia_class"] != "high", -item["valor_num"]))
    return rows[:5]


def build_overdue_activities(filtered_df: pd.DataFrame, columns: dict) -> list[dict]:
    if filtered_df.empty:
        return []

    today = date.today()
    overdue = []
    for _, row in filtered_df.iterrows():
        grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
        if grouped in COMPLETED_STATUSES:
            continue
        activity_date = _as_date(row.get("_ultima_atualizacao") or row.get("_data_chamado"))
        if not activity_date or activity_date >= today:
            continue

        days = (today - activity_date).days
        if days <= 0:
            continue

        for prefix, statuses, icon in OVERDUE_ACTION_DEFS:
            if grouped in statuses:
                empresa = row.get("_empresa") or "—"
                overdue.append({
                    "label": f"{prefix} {empresa}",
                    "icon": icon,
                    "days_label": f"{days} dia{'s' if days != 1 else ''}",
                    "days": days,
                    "sheet_row": int(row.get("_sheet_row", 0) or 0),
                    "href": f"/cadastro/todos/{int(row.get('_sheet_row', 0) or 0)}/editar",
                })
                break

    overdue.sort(key=lambda item: item["days"], reverse=True)
    return overdue[:5]


def build_action_items(filtered_df: pd.DataFrame) -> list[dict]:
    items = []
    for label, statuses, tone in ACTION_STATUS_MAP:
        count = _count_statuses(filtered_df, statuses)
        if count:
            items.append({"label": label, "count": count, "tone": tone})
    return items[:5]


def _build_steps_from_stages(filtered_df: pd.DataFrame, stages: list) -> list[dict]:
    counts = [_count_statuses(filtered_df, statuses) for _, statuses in stages]
    max_count = max(counts) or 1
    steps = []

    for index, ((name, _), count) in enumerate(zip(stages, counts)):
        width = max(28, round((count / max_count) * 100))
        conversion = None
        if index < len(counts) - 1 and counts[index] > 0:
            conversion = round((counts[index + 1] / counts[index]) * 100)
        steps.append({
            "name": name,
            "count": count,
            "width": width,
            "conversion": conversion,
            "level": index,
        })
    return steps


def _previous_period_filters(filters: DashboardFilters) -> DashboardFilters:
    if filters.period_start and filters.period_end:
        delta_days = (filters.period_end - filters.period_start).days + 1
        prev_end = filters.period_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=delta_days - 1)
    else:
        today = date.today()
        prev_end = today - timedelta(days=7)
        prev_start = prev_end - timedelta(days=6)

    return replace(
        filters,
        period_start=prev_start,
        period_end=prev_end,
        selected_card_status=None,
    )


def _week_over_week_trend(current: int, previous: int) -> dict:
    if previous == 0:
        pct = 100 if current > 0 else 0
    else:
        pct = round(((current - previous) / previous) * 100)

    return {
        "trend_pct": abs(pct),
        "trend_up": pct >= 0,
        "trend_flat": pct == 0,
    }


def build_funnel_page_kpi_cards(
    df: pd.DataFrame,
    columns: dict,
    filters: DashboardFilters,
) -> list[dict]:
    filtered_current = apply_dashboard_filters(df, columns, filters)
    filtered_prev = apply_dashboard_filters(df, columns, _previous_period_filters(filters))

    card_defs = [
        ("Novos Leads", ["Novo Lead"], "purple", "👥"),
        ("Contato", ["Chamado Whats", "Ligação - Conversando Whats", "Ligação"], "pink", "☎"),
        ("Reuniões", ["Reunião"], "blue", "📅"),
        ("Propostas", ["Proposta"], "rose", "📄"),
        ("Fechados", ["Fechado"], "green", "✓"),
    ]

    cards = []
    for label, statuses, tone, icon in card_defs:
        current = _count_statuses(filtered_current, statuses)
        previous = _count_statuses(filtered_prev, statuses)
        trend = _week_over_week_trend(current, previous)
        cards.append({
            "label": label,
            "value": current,
            "icon": icon,
            "tone": tone,
            **trend,
        })
    return cards


def build_funnel_page_steps(filtered_df: pd.DataFrame) -> list[dict]:
    return _build_steps_from_stages(filtered_df, FUNNEL_PAGE_STAGES)


def build_funnel_page_actions(filtered_df: pd.DataFrame) -> list[dict]:
    items = []
    for label, statuses, tone in FUNNEL_PAGE_ACTIONS:
        count = _count_statuses(filtered_df, statuses)
        if count:
            items.append({"label": label, "count": count, "tone": tone})
    return items


def build_funnel_steps(filtered_df: pd.DataFrame) -> list[dict]:
    return _build_steps_from_stages(filtered_df, FUNNEL_STAGES)


def compute_overview_metrics(filtered_df: pd.DataFrame) -> dict:
    today = pd.Timestamp.now(tz="America/Sao_Paulo").normalize().tz_localize(None)
    start_week = today - pd.Timedelta(days=today.weekday())
    start_month = today.replace(day=1)
    called_dates = pd.to_datetime(filtered_df["_data_chamado"], errors="coerce")

    return {
        "called_today": int((called_dates.dt.normalize() == today).sum()),
        "called_week": int((called_dates >= start_week).sum()),
        "called_month": int((called_dates >= start_month).sum()),
        "companies": int(filtered_df["_empresa"].replace("", pd.NA).dropna().nunique()),
    }


def build_weekly_chart_json(filtered_df: pd.DataFrame) -> str:
    chart_df = filtered_df.copy()
    chart_df["_data_chamado"] = pd.to_datetime(chart_df["_data_chamado"], errors="coerce")
    chart_df = chart_df.dropna(subset=["_data_chamado"]).copy()

    if chart_df.empty:
        current_week_start = (
            pd.Timestamp.today().normalize()
            - pd.to_timedelta(pd.Timestamp.today().weekday(), unit="D")
        )
        week_starts = pd.date_range(end=current_week_start, periods=4, freq="7D")
        chart_df = pd.DataFrame({"InicioSemana": week_starts})
        chart_df["Quantidade"] = 0
    else:
        chart_df["InicioSemana"] = (
            chart_df["_data_chamado"].dt.normalize()
            - pd.to_timedelta(chart_df["_data_chamado"].dt.weekday, unit="D")
        )
        chart_df = (
            chart_df.groupby("InicioSemana").size().reset_index(name="Quantidade").sort_values("InicioSemana")
        )

    chart_df["FimSemana"] = chart_df["InicioSemana"] + pd.Timedelta(days=6)
    chart_df["Semana"] = (
        chart_df["InicioSemana"].dt.strftime("%d/%m")
        + " – "
        + chart_df["FimSemana"].dt.strftime("%d/%m")
    )

    plot_df = chart_df.copy()
    if len(plot_df) == 1:
        support_point = plot_df.iloc[0].copy()
        support_point["InicioSemana"] = support_point["FimSemana"]
        plot_df = pd.concat([plot_df, pd.DataFrame([support_point])], ignore_index=True)

    figure = px.area(plot_df, x="InicioSemana", y="Quantidade", markers=True, custom_data=["Semana"])
    figure.update_traces(
        line=dict(color="#C026D3", width=3, shape="spline"),
        marker=dict(size=8, color="#FFFFFF", line=dict(width=2, color="#A855F7")),
        fill="tozeroy",
        fillcolor="rgba(192,38,211,0.12)",
        hovertemplate="Semana: %{customdata[0]}<br>Chamados: %{y}<extra></extra>",
    )
    figure.update_layout(
        height=320,
        margin=dict(l=12, r=12, t=8, b=8),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#475569"),
        xaxis_title="",
        yaxis_title="",
    )
    figure.update_xaxes(
        showgrid=False,
        tickmode="array",
        tickvals=chart_df["InicioSemana"].tolist(),
        ticktext=chart_df["Semana"].tolist(),
        tickfont=dict(color="#64748B"),
    )
    figure.update_yaxes(gridcolor="rgba(148,163,184,0.18)", tickfont=dict(color="#64748B"))
    return figure.to_json()


def build_status_summary(filtered_df: pd.DataFrame) -> list[dict]:
    total = max(len(filtered_df), 1)
    rows = []
    for status_name in DASHBOARD_STATUS_OPTIONS:
        count = count_dashboard_status(filtered_df, status_name)
        color = STATUS_COLORS.get(status_name, ("#EAF2FF", "#5C9DFF"))[1]
        rows.append({
            "name": status_name,
            "count": count,
            "percent": round((count / total) * 100),
            "color": color,
        })
    return rows


STATUS_ICONS = {
    "Novo Lead": "✦", "Chamado Whats": "☘", "Conversando": "•", "Reunião": "◉",
    "Proposta": "▤", "Sem interesse": "⊘", "Fechado": "✓", "Sem Resposta": "⚑",
    "Sem Whatsapp": "–", "Retornar": "↩", "Ligação - Conversando Whats": "☎",
    "Ligação não atende/cx": "☎", "Ligação Numero errado": "!", "Ligação retornar": "↩",
}


def build_status_cards(filtered_df: pd.DataFrame) -> list[dict]:
    cards = []
    for status_name in DASHBOARD_STATUS_OPTIONS:
        bg, icon_color = STATUS_COLORS.get(status_name, ("#EAF2FF", "#5C9DFF"))
        cards.append({
            "name": status_name,
            "icon": STATUS_ICONS.get(status_name, "•"),
            "bg_color": bg,
            "icon_color": icon_color,
            "count": count_dashboard_status(filtered_df, status_name),
        })
    return cards


def build_calls_table(
    filtered_df: pd.DataFrame,
    columns: dict,
    selected_status: str | None,
    search_term: str,
    status_filter: str,
) -> list[dict]:
    if search_term or status_filter != "Todos os status":
        selected_df = filtered_df.copy()
    elif not selected_status:
        selected_df = filtered_df.copy()
    else:
        selected_df = filtered_df[
            filtered_df.apply(lambda row: row_matches_dashboard_card(row, selected_status), axis=1)
        ].copy()

    selected_df = selected_df.sort_values(["_data_chamado", "_empresa"], ascending=[False, True]).head(25)

    rows = []
    for _, row in selected_df.iterrows():
        rows.append({
            "empresa": row.get("_empresa", ""),
            "telefone": row.get("_telefone", ""),
            "email": safe_series(pd.DataFrame([row]), columns.get("email")).iloc[0],
            "vendedor": row.get("_vendedor", ""),
            "status_whatsapp": row.get("_status_whatsapp_original", ""),
            "status_ligacao": row.get("_status_ligacao_original", ""),
            "data_chamado": row.get("_data_chamado", ""),
            "etapa": status_group(row.get("_status_original", row.get("_status_grupo", "Novo Lead"))),
            "sheet_row": int(row.get("_sheet_row", 0)),
        })
    return rows
