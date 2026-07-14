"""Métricas, gráficos e componentes da Visão Geral."""
import html
import json

import pandas as pd
import plotly.express as px

from app.services.legacy_core import (
    DASHBOARD_STATUS_OPTIONS,
    STATUS_COLORS,
    count_dashboard_status,
    row_matches_dashboard_card,
    safe_series,
)


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
        line=dict(color="#E14BFF", width=4, shape="spline"),
        marker=dict(size=9, color="#FFFFFF", line=dict(width=3, color="#D74BFF")),
        fill="tozeroy",
        fillcolor="rgba(224,67,255,0.34)",
        hovertemplate="Semana: %{customdata[0]}<br>Chamados: %{y}<extra></extra>",
    )
    figure.update_layout(
        height=370,
        margin=dict(l=20, r=20, t=8, b=8),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FFFFFF"),
        xaxis_title="",
        yaxis_title="",
    )
    figure.update_xaxes(
        showgrid=False,
        tickmode="array",
        tickvals=chart_df["InicioSemana"].tolist(),
        ticktext=chart_df["Semana"].tolist(),
    )
    figure.update_yaxes(gridcolor="rgba(255,255,255,0.08)")
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
        return []
    else:
        selected_df = filtered_df[
            filtered_df.apply(lambda row: row_matches_dashboard_card(row, selected_status), axis=1)
        ].copy()

    selected_df = selected_df.sort_values(["_data_chamado", "_empresa"], ascending=[False, True])

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
            "sheet_row": int(row.get("_sheet_row", 0)),
        })
    return rows
