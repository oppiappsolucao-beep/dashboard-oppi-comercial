"""Metas e Relatórios — KPIs, gráficos e tabelas da planilha comercial."""
from dataclasses import replace
from datetime import date

import pandas as pd
import plotly.graph_objects as go

from app.services.filters import DashboardFilters, apply_dashboard_filters
from app.services.legacy_core import apply_period_filter, count_dashboard_status, status_group
from app.services.monthly_goals import TEAM_SELLER_LABEL, get_monthly_goal, get_monthly_goal_commission_rate

MONTHS_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

from config.crm_options import (
    OVERVIEW_FUNNEL_STAGES as CRM_FUNNEL_STAGES,
    PIPELINE_STAGE_COLORS,
    PIPELINE_STAGE_OPTIONS,
    PIPELINE_STAGE_SHEET_STATUSES,
)

CONVERSION_STAGES = [(stage, PIPELINE_STAGE_SHEET_STATUSES.get(stage, [])) for stage in PIPELINE_STAGE_OPTIONS]

FORECAST_PROPOSAL_FACTOR = 0.35
FORECAST_MEETING_FACTOR = 0.15
GOAL_GROWTH_FACTOR = 1.15
DEFAULT_SELLER_GOAL = 20000.0


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _format_money(value: float, short: bool = False) -> str:
    number = float(value or 0)
    if short and number >= 1000:
        formatted = f"{number / 1000:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {formatted}k"
    formatted = f"{number:,.0f}".replace(",", ".")
    return f"R$ {formatted}"


def _format_money_decimal(value: float) -> str:
    number = float(value or 0)
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _format_percent(value: float, decimals: int = 1) -> str:
    formatted = f"{value:,.{decimals}f}".replace(".", ",")
    return f"{formatted}%"


def _month_label(month: int, year: int) -> str:
    return f"{MONTHS_PT[month - 1]}/{year}"


def _month_bounds(month: int, year: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    end = end.fromordinal(end.toordinal() - 1)
    return start, end


def _previous_month(month: int, year: int) -> tuple[int, int]:
    if month == 1:
        return 12, year - 1
    return month - 1, year


def _filter_month(df: pd.DataFrame, month: int, year: int) -> pd.DataFrame:
    if df.empty:
        return df
    start, end = _month_bounds(month, year)
    return apply_period_filter(df.copy(), "_data_chamado", (start, end))


def _count_statuses(df: pd.DataFrame, names: list[str]) -> int:
    return sum(count_dashboard_status(df, name) for name in names)


def _sum_capital_by_status(df: pd.DataFrame, statuses: set[str]) -> float:
    if df.empty:
        return 0.0
    total = 0.0
    for _, row in df.iterrows():
        grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
        if grouped in statuses:
            total += float(row.get("_capital_num") or 0)
    return total


def _realized_value(df: pd.DataFrame) -> float:
    return _sum_capital_by_status(df, {"Fechado"})


def _seller_goal(realized_prev: float, realized_current: float) -> float:
    if realized_prev > 0:
        return round(realized_prev * GOAL_GROWTH_FACTOR, -2)
    if realized_current > 0:
        return round(realized_current * 1.25, -2)
    return DEFAULT_SELLER_GOAL


def _resolved_seller_goal(base_df: pd.DataFrame, month: int, year: int, seller: str) -> float:
    """Só usa meta cadastrada. Sem cadastro → 0 (não inventa R$ 20.000)."""
    configured = get_monthly_goal(year, month, seller)
    if configured is not None:
        return float(configured)
    return 0.0


def _achievement_status(pct: float) -> tuple[str, str]:
    if pct >= 100:
        return "Acima da meta", "above"
    if pct >= 80:
        return "Em linha", "inline"
    if pct >= 60:
        return "Atenção", "warning"
    return "Abaixo da meta", "below"


def _trend_vs_previous(current: float, previous: float, is_points: bool = False) -> dict:
    if is_points:
        delta = round(current - previous, 1)
        return {
            "trend_label": f"+ {abs(delta):,.1f} p.p. vs mês anterior".replace(",", "."),
            "trend_up": delta >= 0,
            "trend_flat": delta == 0,
        }
    if previous == 0:
        pct = 100 if current > 0 else 0
    else:
        pct = round(((current - previous) / previous) * 100)
    sign = "+" if pct >= 0 else "-"
    return {
        "trend_label": f"{sign} {abs(pct)}% vs mês anterior",
        "trend_up": pct >= 0,
        "trend_flat": pct == 0,
    }


def _commission_rate(year: int, month: int, seller: str) -> float:
    return get_monthly_goal_commission_rate(year, month, seller) / 100


def _seller_rows(base_df: pd.DataFrame, month: int, year: int) -> list[dict]:
    prev_month, prev_year = _previous_month(month, year)
    sellers = sorted(
        s for s in base_df["_vendedor"].dropna().astype(str).unique().tolist()
        if s and s != "Sem vendedor"
    )
    if not sellers:
        sellers = ["Sem vendedor"]

    rows = []
    for seller in sellers:
        seller_df = base_df[base_df["_vendedor"] == seller].copy()
        month_df = _filter_month(seller_df, month, year)
        prev_df = _filter_month(seller_df, prev_month, prev_year)

        realized = _realized_value(month_df)
        goal = _resolved_seller_goal(base_df, month, year, seller)
        proposals = _count_statuses(month_df, ["Proposta"])
        closed = count_dashboard_status(month_df, "Fechado")
        total_leads = len(month_df)
        conversion = round((closed / total_leads) * 100, 1) if total_leads else 0.0
        commission = realized * _commission_rate(year, month, seller)
        achievement = round((realized / goal) * 100, 1) if goal else 0.0
        status_label, status_class = _achievement_status(achievement) if goal else ("Sem meta", "inline")

        rows.append({
            "vendedor": seller,
            "vendedor_initials": _initials(seller),
            "meta": goal,
            "meta_label": _format_money(goal) if goal else "Sem meta",
            "realizado": realized,
            "realizado_label": _format_money(realized),
            "propostas": proposals,
            "fechados": closed,
            "conversao": _format_percent(conversion),
            "conversao_num": conversion,
            "comissao": commission,
            "comissao_label": _format_money_decimal(commission),
            "atingimento": achievement,
            "atingimento_label": _format_percent(achievement, 0) if goal else "—",
            "status_label": status_label,
            "status_class": status_class,
            "has_goal": bool(goal),
        })

    rows.sort(key=lambda item: item["realizado"], reverse=True)
    return rows


def build_goals_kpi_cards(base_df: pd.DataFrame, month: int, year: int, seller: str) -> list[dict]:
    filtered_base = base_df if seller == "Todos os vendedores" else base_df[base_df["_vendedor"] == seller].copy()
    month_df = _filter_month(filtered_base, month, year)
    prev_month, prev_year = _previous_month(month, year)
    prev_df = _filter_month(filtered_base, prev_month, prev_year)

    realized = _realized_value(month_df)
    realized_prev = _realized_value(prev_df)
    if seller == TEAM_SELLER_LABEL:
        team_goal = get_monthly_goal(year, month, TEAM_SELLER_LABEL)
        goal = team_goal if team_goal is not None else sum(row["meta"] for row in _seller_rows(filtered_base, month, year))
    else:
        goal = _resolved_seller_goal(filtered_base, month, year, seller)

    forecast_base = realized
    forecast_base += _sum_capital_by_status(month_df, {"Proposta"}) * FORECAST_PROPOSAL_FACTOR
    forecast_base += _sum_capital_by_status(month_df, {"Reunião"}) * FORECAST_MEETING_FACTOR

    prev_forecast = _realized_value(prev_df)
    prev_forecast += _sum_capital_by_status(prev_df, {"Proposta"}) * FORECAST_PROPOSAL_FACTOR
    prev_forecast += _sum_capital_by_status(prev_df, {"Reunião"}) * FORECAST_MEETING_FACTOR

    commission = realized * _commission_rate(year, month, seller if seller != "Todos os vendedores" else TEAM_SELLER_LABEL)
    commission_prev = realized_prev * _commission_rate(prev_year, prev_month, seller if seller != "Todos os vendedores" else TEAM_SELLER_LABEL)

    closed = count_dashboard_status(month_df, "Fechado")
    closed_prev = count_dashboard_status(prev_df, "Fechado")
    ticket = realized / closed if closed else 0.0
    ticket_prev = realized_prev / closed_prev if closed_prev else 0.0

    total_leads = len(month_df)
    total_leads_prev = len(prev_df)
    conversion = (closed / total_leads) * 100 if total_leads else 0.0
    conversion_prev = (closed_prev / total_leads_prev) * 100 if total_leads_prev else 0.0

    cards = [
        ("Meta do mês", _format_money(goal), None, "🎯", "purple"),
        ("Faturamento realizado", _format_money(realized), _trend_vs_previous(realized, realized_prev), "📈", "green"),
        ("Previsão de fechamento", _format_money(forecast_base), _trend_vs_previous(forecast_base, prev_forecast), "📊", "blue"),
        ("Comissões", _format_money(commission), _trend_vs_previous(commission, commission_prev), "💼", "violet"),
        ("Ticket médio", _format_money(ticket), _trend_vs_previous(ticket, ticket_prev), "🧾", "pink"),
        ("Taxa de conversão", _format_percent(conversion), _trend_vs_previous(conversion, conversion_prev, is_points=True), "◎", "orange"),
    ]

    result = []
    for label, value, trend, icon, tone in cards:
        item = {"label": label, "value": value, "icon": icon, "tone": tone}
        if trend:
            item.update(trend)
        else:
            item.update({"trend_label": _month_label(month, year), "trend_up": True, "trend_flat": True, "is_period": True})
        result.append(item)
    return result


def build_financial_summary(base_df: pd.DataFrame, month: int, year: int, seller: str) -> list[dict]:
    filtered_base = base_df if seller == "Todos os vendedores" else base_df[base_df["_vendedor"] == seller].copy()
    month_df = _filter_month(filtered_base, month, year)

    recurring = _realized_value(month_df)
    implementation = _sum_capital_by_status(month_df, {"Reunião", "Proposta"}) * 0.45
    services = _sum_capital_by_status(month_df, {"Conversando", "Proposta"}) * 0.55
    discounts = _sum_capital_by_status(month_df, {"Proposta"}) * 0.05
    commission_forecast = (
        _realized_value(month_df) + _sum_capital_by_status(month_df, {"Proposta"}) * 0.2
    ) * _commission_rate(year, month, seller if seller != "Todos os vendedores" else TEAM_SELLER_LABEL)
    revenue_base = recurring + implementation + services
    margin = round(((revenue_base - discounts) / revenue_base) * 100) if revenue_base else 0

    return [
        {"label": "Receita recorrente", "value": _format_money(recurring), "icon": "🔄", "tone": "purple"},
        {"label": "Receita de implantação", "value": _format_money(implementation), "icon": "🚀", "tone": "pink"},
        {"label": "Receita de serviços", "value": _format_money(services), "icon": "💼", "tone": "blue"},
        {"label": "Descontos concedidos", "value": _format_money(discounts), "icon": "🏷", "tone": "orange"},
        {"label": "Previsão de comissão", "value": _format_money(commission_forecast), "icon": "💵", "tone": "green"},
        {"label": "Margem estimada", "value": _format_percent(margin, 0), "icon": "◔", "tone": "violet"},
    ]


def build_conversion_stages(base_df: pd.DataFrame, month: int, year: int, seller: str) -> list[dict]:
    filtered_base = base_df if seller == "Todos os vendedores" else base_df[base_df["_vendedor"] == seller].copy()
    month_df = _filter_month(filtered_base, month, year)
    counts = [_count_statuses(month_df, statuses) for _, statuses in CONVERSION_STAGES]
    base = counts[0] or max(counts) or 1

    rows = []
    for (name, _), count in zip(CONVERSION_STAGES, counts):
        pct = round((count / base) * 100) if base else 0
        width = max(12, pct)
        rows.append({
            "name": name,
            "count": count,
            "percent": pct,
            "width": width,
            "color": PIPELINE_STAGE_COLORS.get(name, "#7C3AED"),
        })
    return rows


def build_individual_goals(base_df: pd.DataFrame, month: int, year: int, seller: str) -> list[dict]:
    rows = _seller_rows(base_df, month, year)
    if seller != "Todos os vendedores":
        rows = [row for row in rows if row["vendedor"] == seller]
    for row in rows:
        width = min(100, max(8, round(row["atingimento"])))
        row["progress_width"] = width
        if row["atingimento"] >= 100:
            row["progress_tone"] = "good"
        elif row["atingimento"] >= 80:
            row["progress_tone"] = "mid"
        else:
            row["progress_tone"] = "low"
    return rows[:6]


def build_consolidated_report(base_df: pd.DataFrame, month: int, year: int, seller: str) -> dict:
    rows = _seller_rows(base_df, month, year)
    if seller != "Todos os vendedores":
        rows = [row for row in rows if row["vendedor"] == seller]

    totals = {
        "meta": sum(row["meta"] for row in rows),
        "realizado": sum(row["realizado"] for row in rows),
        "propostas": sum(row["propostas"] for row in rows),
        "fechados": sum(row["fechados"] for row in rows),
        "comissao": sum(row["comissao"] for row in rows),
    }
    total_leads = sum(row["propostas"] + row["fechados"] for row in rows) or 1
    totals["conversao"] = _format_percent((totals["fechados"] / total_leads) * 100)
    totals["meta_label"] = _format_money(totals["meta"])
    totals["realizado_label"] = _format_money(totals["realizado"])
    totals["comissao_label"] = _format_money_decimal(totals["comissao"])

    return {"rows": rows, "totals": totals}


def _empty_figure(title: str, height: int = 320) -> str:
    figure = go.Figure()
    figure.update_layout(
        title={"text": title, "x": 0.02, "font": {"size": 14, "color": "#64748B"}},
        height=height,
        margin=dict(l=12, r=12, t=36, b=8),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[{
            "text": "Sem dados para o período selecionado",
            "xref": "paper",
            "yref": "paper",
            "x": 0.5,
            "y": 0.5,
            "showarrow": False,
            "font": {"size": 13, "color": "#94A3B8"},
        }],
    )
    return figure.to_json()


def build_revenue_chart_json(base_df: pd.DataFrame, month: int, year: int, seller: str) -> str:
    filtered_base = base_df if seller == "Todos os vendedores" else base_df[base_df["_vendedor"] == seller].copy()

    labels = []
    meta_values = []
    realized_values = []

    for offset in range(5, -1, -1):
        target_month = month - offset
        target_year = year
        while target_month <= 0:
            target_month += 12
            target_year -= 1

        month_df = _filter_month(filtered_base, target_month, target_year)
        prev_month, prev_year = _previous_month(target_month, target_year)
        prev_df = _filter_month(filtered_base, prev_month, prev_year)
        realized = _realized_value(month_df)
        goal = get_monthly_goal(target_year, target_month, seller)
        if goal is None:
            goal = sum(row["meta"] for row in _seller_rows(filtered_base, target_month, target_year))
            if seller != TEAM_SELLER_LABEL:
                goal = _resolved_seller_goal(filtered_base, target_month, target_year, seller)

        labels.append(f"{MONTHS_PT[target_month - 1][:3]}/{str(target_year)[-2:]}")
        meta_values.append(goal)
        realized_values.append(realized)

    if not any(meta_values) and not any(realized_values):
        return _empty_figure("Evolução de faturamento")

    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=labels,
        y=meta_values,
        name="Meta",
        mode="lines+markers",
        line=dict(color="#A855F7", width=2, dash="dash"),
        marker=dict(size=7, color="#A855F7"),
    ))
    figure.add_trace(go.Scatter(
        x=labels,
        y=realized_values,
        name="Realizado",
        mode="lines+markers",
        line=dict(color="#7C3AED", width=3, shape="spline"),
        marker=dict(size=8, color="#FFFFFF", line=dict(width=2, color="#7C3AED")),
        fill="tozeroy",
        fillcolor="rgba(124,58,237,0.10)",
    ))
    figure.update_layout(
        height=320,
        margin=dict(l=12, r=12, t=12, b=8),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#475569"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis_title="",
        yaxis_title="",
    )
    figure.update_xaxes(showgrid=False, tickfont=dict(color="#64748B"))
    figure.update_yaxes(gridcolor="rgba(148,163,184,0.18)", tickfont=dict(color="#64748B"))
    return figure.to_json()


def build_seller_performance_chart_json(base_df: pd.DataFrame, month: int, year: int, seller: str) -> str:
    rows = build_individual_goals(base_df, month, year, seller)
    if not rows:
        return _empty_figure("Performance por vendedor", height=280)

    names = [row["vendedor"] for row in rows]
    values = [row["realizado"] for row in rows]

    figure = go.Figure(go.Bar(
        x=values,
        y=names,
        orientation="h",
        marker=dict(color="#7C3AED"),
        text=[_format_money(value) for value in values],
        textposition="outside",
    ))
    figure.update_layout(
        height=max(240, len(names) * 56),
        margin=dict(l=12, r=40, t=8, b=8),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#475569"),
        xaxis_title="",
        yaxis_title="",
    )
    figure.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.18)", tickfont=dict(color="#64748B"))
    figure.update_yaxes(autorange="reversed", tickfont=dict(color="#64748B"))
    return figure.to_json()


def build_goals_page_context(df: pd.DataFrame, columns: dict, filters: DashboardFilters, month: int, year: int, seller: str) -> dict:
    base_df = apply_dashboard_filters(df, columns, replace(filters, period_start=None, period_end=None))

    return {
        "month": month,
        "year": year,
        "month_label": _month_label(month, year),
        "seller": seller,
        "kpi_cards": build_goals_kpi_cards(base_df, month, year, seller),
        "financial_summary": build_financial_summary(base_df, month, year, seller),
        "conversion_stages": build_conversion_stages(base_df, month, year, seller),
        "individual_goals": build_individual_goals(base_df, month, year, seller),
        "consolidated_report": build_consolidated_report(base_df, month, year, seller),
        "revenue_chart_json": build_revenue_chart_json(base_df, month, year, seller),
        "seller_chart_json": build_seller_performance_chart_json(base_df, month, year, seller),
        "month_options": [{"value": index + 1, "label": name} for index, name in enumerate(MONTHS_PT)],
    }
