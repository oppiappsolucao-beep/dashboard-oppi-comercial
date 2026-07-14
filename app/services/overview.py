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
    status_group,
)

FUNNEL_STAGES = [
    ("Novo Lead", ["Novo Lead"]),
    ("Primeiro Contato", ["Chamado Whats", "Conversando", "Ligação - Conversando Whats"]),
    ("Reunião", ["Reunião"]),
    ("Proposta", ["Proposta"]),
    ("Fechado", ["Fechado"]),
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


def build_kpi_cards(filtered_df: pd.DataFrame) -> list[dict]:
    novos = count_dashboard_status(filtered_df, "Novo Lead")
    contato = _count_statuses(filtered_df, ["Chamado Whats", "Conversando", "Ligação - Conversando Whats"])
    propostas = count_dashboard_status(filtered_df, "Proposta")
    fechados = count_dashboard_status(filtered_df, "Fechado")
    total = max(len(filtered_df), 1)
    conversao = round((fechados / total) * 100)

    return [
        {"label": "Novos Leads", "value": novos, "note": "no período filtrado", "icon": "✦", "tone": "pink"},
        {"label": "Em Contato", "value": contato, "note": "WhatsApp e ligação", "icon": "☎", "tone": "purple"},
        {"label": "Propostas Enviadas", "value": propostas, "note": "em negociação", "icon": "▤", "tone": "violet"},
        {"label": "Fechados", "value": fechados, "note": "convertidos", "icon": "✓", "tone": "green"},
        {"label": "Taxa de Conversão", "value": f"{conversao}%", "note": "sobre a base filtrada", "icon": "%", "tone": "blue"},
    ]


def build_funnel_steps(filtered_df: pd.DataFrame) -> list[dict]:
    counts = [_count_statuses(filtered_df, statuses) for _, statuses in FUNNEL_STAGES]
    max_count = max(counts) or 1
    steps = []

    for index, ((name, _), count) in enumerate(zip(FUNNEL_STAGES, counts)):
        width = max(34, round((count / max_count) * 100))
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


def build_action_items(filtered_df: pd.DataFrame) -> list[dict]:
    items = []
    for label, statuses, tone in ACTION_STATUS_MAP:
        count = _count_statuses(filtered_df, statuses)
        if count:
            items.append({"label": label, "count": count, "tone": tone})
    return items[:5]


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
