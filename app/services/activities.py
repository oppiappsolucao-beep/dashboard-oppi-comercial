"""Atividades — KPIs, filtros e tabela derivados da planilha comercial."""
from datetime import date, datetime, timedelta

import pandas as pd

from app.services.legacy_core import as_python_date, as_python_datetime, safe_series, status_group

ACTIVITY_TYPES = ["Ligação", "WhatsApp", "Reunião", "E-mail", "Tarefa"]

TYPE_MAP = {
    "Chamado Whats": ("WhatsApp", "whatsapp", "💬"),
    "Conversando": ("WhatsApp", "whatsapp", "💬"),
    "Ligação": ("Ligação", "ligacao", "☎"),
    "Ligação - Conversando Whats": ("Ligação", "ligacao", "☎"),
    "Ligação retornar": ("Ligação", "ligacao", "☎"),
    "Retornar": ("Ligação", "ligacao", "☎"),
    "Reunião": ("Reunião", "reuniao", "📅"),
    "Proposta": ("E-mail", "email", "✉"),
    "Sem Resposta": ("E-mail", "email", "✉"),
    "Novo Lead": ("Tarefa", "tarefa", "📋"),
    "Fechado": ("Tarefa", "tarefa", "✓"),
    "Sem interesse": ("Tarefa", "tarefa", "—"),
}

TITLE_MAP = {
    "Novo Lead": ("Qualificar novo lead", "Analisar perfil e definir próximo passo comercial"),
    "Chamado Whats": ("Contato via WhatsApp", "Iniciar conversa e apresentar a solução"),
    "Conversando": ("Acompanhar conversa no WhatsApp", "Manter engajamento e entender necessidades"),
    "Ligação": ("Ligar para decisor", "Entender necessidade e avaliar interesse"),
    "Ligação - Conversando Whats": ("Retornar ligação", "Continuar conversa iniciada pelo WhatsApp"),
    "Ligação retornar": ("Retornar ligação agendada", "Contato pendente conforme combinado"),
    "Retornar": ("Retornar contato", "Follow-up pendente com o lead"),
    "Reunião": ("Reunião de apresentação", "Apresentar proposta e alinhar expectativas"),
    "Proposta": ("Enviar proposta comercial", "Formalizar valores e condições negociadas"),
    "Fechado": ("Negócio fechado", "Contrato ou acordo concluído com sucesso"),
    "Sem Resposta": ("Reativar contato", "Lead sem retorno — tentar novo canal"),
    "Sem interesse": ("Lead encerrado", "Sem interesse confirmado pelo cliente"),
}

NEXT_ACTION_MAP = {
    "Retornar": "Ligar agora",
    "Ligação retornar": "Ligar agora",
    "Sem Resposta": "Ligar agora",
    "Proposta": "Aguardar retorno",
    "Reunião": "Confirmar presença",
    "Conversando": "Enviar material",
    "Chamado Whats": "Aguardar resposta",
    "Ligação": "Registrar outcome",
    "Ligação - Conversando Whats": "Agendar reunião",
    "Novo Lead": "Primeiro contato",
    "Fechado": "—",
    "Sem interesse": "—",
}

COMPLETED_STATUSES = {"Fechado", "Sem interesse"}


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _as_date(value) -> date | None:
    return as_python_date(value)


def _as_datetime(value) -> datetime | None:
    return as_python_datetime(value)


def _activity_type(status: str) -> tuple[str, str, str]:
    grouped = status_group(status)
    return TYPE_MAP.get(grouped, ("Tarefa", "tarefa", "📋"))


def _activity_status(grouped: str, activity_date: date | None, today: date) -> str:
    if grouped in COMPLETED_STATUSES:
        return "concluida"
    if activity_date and activity_date < today:
        return "atrasada"
    return "pendente"


def _format_activity_datetime(value) -> tuple[str, str]:
    dt = _as_datetime(value)
    if not dt:
        return "—", "—"
    today = date.today()
    d = dt.date()
    time_part = dt.strftime("%H:%M")
    date_part = dt.strftime("%d/%m/%Y")
    if d == today:
        return f"Hoje, {time_part}", date_part
    yesterday = today - timedelta(days=1)
    if d == yesterday:
        return f"Ontem, {time_part}", date_part
    return dt.strftime("%d/%m/%Y, %H:%M"), date_part


def _contact_name(row, columns: dict) -> str:
    for key in ("socio_1", "socio_2", "socio_3"):
        column = columns.get(key)
        if column:
            value = safe_series(pd.DataFrame([row]), column).iloc[0]
            if value and str(value).strip():
                return str(value).strip()
    return "—"


def _rows_from_dataframe(filtered_df: pd.DataFrame, columns: dict) -> list[dict]:
    today = date.today()
    activities = []

    for _, row in filtered_df.iterrows():
        status = row.get("_status_grupo") or row.get("_status_original", "")
        grouped = status_group(status)
        tipo, tipo_class, icon = _activity_type(status)
        title, description = TITLE_MAP.get(grouped, ("Acompanhar lead", "Interação comercial pendente"))
        activity_dt = row.get("_ultima_atualizacao") or row.get("_data_chamado")
        activity_date = _as_date(activity_dt)
        act_status = _activity_status(grouped, activity_date, today)
        when_label, when_date = _format_activity_datetime(activity_dt)
        empresa = row.get("_empresa") or "—"
        vendedor = row.get("_vendedor") or "Sem vendedor"

        activities.append({
            "title": title,
            "description": description,
            "icon": icon,
            "empresa": empresa,
            "contato": _contact_name(row, columns),
            "tipo": tipo,
            "tipo_class": tipo_class,
            "vendedor": vendedor,
            "vendedor_initials": _initials(vendedor),
            "when_label": when_label,
            "when_date": when_date,
            "status": act_status,
            "status_label": {"concluida": "Concluída", "pendente": "Pendente", "atrasada": "Atrasada"}[act_status],
            "proxima_acao": NEXT_ACTION_MAP.get(grouped, "Acompanhar contato"),
            "activity_date": activity_date or date.min,
            "activity_dt": _as_datetime(activity_dt) or datetime.min,
            "sheet_row": int(row.get("_sheet_row", 0) or 0),
        })

    activities.sort(key=lambda item: item["activity_dt"], reverse=True)
    return activities


def build_activities_list(filtered_df: pd.DataFrame, columns: dict) -> list[dict]:
    return _rows_from_dataframe(filtered_df, columns)


def build_activities_kpi_cards(activities: list[dict]) -> list[dict]:
    today = date.today()
    start_month = today.replace(day=1)
    end_week = today + timedelta(days=7)

    today_items = [a for a in activities if a["activity_date"] == today]
    today_done = sum(1 for a in today_items if a["status"] == "concluida")
    pending = [a for a in activities if a["status"] == "pendente"]
    pending_week = [
        a for a in pending
        if a["activity_date"] != date.min and today <= a["activity_date"] <= end_week
    ]
    calls_month = [
        a for a in activities
        if a["tipo"] == "Ligação"
        and a["activity_date"] != date.min
        and a["activity_date"] >= start_month
    ]
    done_month = [
        a for a in activities
        if a["status"] == "concluida"
        and a["activity_date"] != date.min
        and a["activity_date"] >= start_month
    ]
    overdue = [a for a in activities if a["status"] == "atrasada"]

    return [
        {
            "label": "Atividades hoje",
            "value": len(today_items),
            "note": f"{today_done} concluída{'s' if today_done != 1 else ''}",
            "icon": "📋",
            "tone": "purple",
        },
        {
            "label": "Pendentes",
            "value": len(pending),
            "note": "próximos 7 dias" if pending_week else "em aberto",
            "icon": "⏳",
            "tone": "orange",
        },
        {
            "label": "Ligações realizadas",
            "value": len(calls_month),
            "note": "este mês",
            "icon": "☎",
            "tone": "blue",
        },
        {
            "label": "Concluídas",
            "value": len(done_month),
            "note": "este mês",
            "icon": "✓",
            "tone": "green",
        },
        {
            "label": "Atrasadas",
            "value": len(overdue),
            "note": "precisam de atenção",
            "icon": "⚠",
            "tone": "rose",
        },
    ]


def apply_activities_view(
    activities: list[dict],
    tab: str,
    activity_type: str,
    responsible: str,
) -> list[dict]:
    result = activities

    if tab == "pendentes":
        result = [a for a in result if a["status"] == "pendente"]
    elif tab == "concluidas":
        result = [a for a in result if a["status"] == "concluida"]
    elif tab == "atrasadas":
        result = [a for a in result if a["status"] == "atrasada"]

    if activity_type and activity_type != "Todos os tipos":
        result = [a for a in result if a["tipo"] == activity_type]

    if responsible and responsible != "Todos os responsáveis":
        result = [a for a in result if a["vendedor"] == responsible]

    return result


def build_activities_table(
    filtered_df: pd.DataFrame,
    columns: dict,
    tab: str = "todas",
    activity_type: str = "Todos os tipos",
    responsible: str = "Todos os responsáveis",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    activities = _rows_from_dataframe(filtered_df, columns)
    view = apply_activities_view(activities, tab, activity_type, responsible)
    total = len(view)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    rows = view[start : start + per_page]

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "from_record": start + 1 if total else 0,
        "to_record": min(start + per_page, total),
    }
