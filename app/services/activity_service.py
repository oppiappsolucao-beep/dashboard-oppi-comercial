"""Serviço de atividades comerciais — conectado à Visão Geral."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from app.services.activities_storage import (
    DEFAULT_TENANT_ID,
    activity_exists,
    get_activity,
    list_activities,
    save_activity,
    soft_delete_activity,
)
from app.services.filters import DashboardFilters, apply_dashboard_filters
from app.services.followup_service import (
    STAGE_MAP,
    _lead_record,
    buscar_leads_para_acao,
)
from app.services.lead_actions_storage import append_interaction, save_lead_action
from app.services.legacy_core import as_python_date, as_python_datetime, normalize_text, safe_series, status_group

PROCESS_ACTIONS = [
    "Fazer primeiro contato",
    "Qualificar lead",
    "Retornar contato",
    "Agendar reunião",
    "Confirmar reunião",
    "Realizar reunião",
    "Criar proposta",
    "Enviar proposta",
    "Fazer follow-up",
    "Negociar condições",
    "Enviar contrato",
    "Confirmar assinatura",
    "Confirmar pagamento",
    "Iniciar implantação",
    "Encerrar oportunidade",
]

CHANNELS = [
    "WhatsApp",
    "Ligação",
    "E-mail",
    "Reunião online",
    "Reunião presencial",
    "Mensagem interna",
    "Outro",
]

ACTIVITY_STATUSES = [
    ("pendente", "Pendente"),
    ("em_andamento", "Em andamento"),
    ("concluida", "Concluída"),
    ("atrasada", "Atrasada"),
    ("reagendada", "Reagendada"),
    ("cancelada", "Cancelada"),
]

RESULT_OPTIONS = [
    "",
    "Cliente respondeu",
    "Cliente não respondeu",
    "Pediu retorno",
    "Interesse confirmado",
    "Lead qualificado",
    "Reunião agendada",
    "Reunião realizada",
    "Proposta solicitada",
    "Proposta enviada",
    "Em negociação",
    "Venda fechada",
    "Sem interesse",
    "Número incorreto",
    "Sem WhatsApp",
    "Contato inválido",
    "Outro",
]

CLOSED_RESULTS = {"Venda fechada", "Sem interesse", "Contato inválido"}
NO_NEXT_ACTION_RESULTS = CLOSED_RESULTS | {"Encerrar oportunidade"}

ACTION_DESCRIPTIONS = {
    "Fazer primeiro contato": "Entrar em contato inicial com o lead.",
    "Qualificar lead": "Entender perfil, necessidade e fit comercial.",
    "Retornar contato": "Retomar conversa conforme combinado.",
    "Agendar reunião": "Marcar reunião comercial com o decisor.",
    "Confirmar reunião": "Confirmar presença antes da reunião.",
    "Realizar reunião": "Conduzir apresentação ou alinhamento.",
    "Criar proposta": "Montar proposta comercial.",
    "Enviar proposta": "Enviar proposta formal ao cliente.",
    "Fazer follow-up": "Acompanhar retorno da proposta enviada.",
    "Negociar condições": "Tratar objeções e condições comerciais.",
    "Enviar contrato": "Enviar documentação para assinatura.",
    "Confirmar assinatura": "Validar assinatura do contrato.",
    "Confirmar pagamento": "Confirmar recebimento ou pagamento.",
    "Iniciar implantação": "Iniciar onboarding do cliente.",
    "Encerrar oportunidade": "Encerrar oportunidade comercial.",
}

OVERVIEW_TO_PROCESS = {
    "Fazer primeiro contato": "Fazer primeiro contato",
    "Definir próximo passo": "Retornar contato",
    "Definir próxima ação": "Retornar contato",
    "Retomar qualificação": "Qualificar lead",
    "Retomar negociação": "Negociar condições",
    "Realizar follow-up da proposta": "Fazer follow-up",
    "Criar proposta": "Criar proposta",
    "Confirmar reunião": "Confirmar reunião",
}

CHANNEL_KEY_TO_LABEL = {
    "whatsapp": "WhatsApp",
    "ligacao": "Ligação",
    "email": "E-mail",
    "reuniao": "Reunião online",
    "tarefa": "Mensagem interna",
}

CHANNEL_CLASS = {
    "WhatsApp": "whatsapp",
    "Ligação": "ligacao",
    "E-mail": "email",
    "Reunião online": "reuniao",
    "Reunião presencial": "reuniao",
    "Mensagem interna": "tarefa",
    "Outro": "tarefa",
}

STATUS_CLASS = {
    "pendente": "pendente",
    "em_andamento": "pendente",
    "concluida": "concluida",
    "atrasada": "atrasada",
    "reagendada": "reagendada",
    "cancelada": "cancelada",
}


@dataclass
class ActivitiesViewParams:
    tab: str = "todas"
    activity_type: str = "Todos os tipos"
    channel: str = "Todos os canais"
    responsible: str = "Todos os responsáveis"
    stage: str = "Todas as etapas"
    page: int = 1
    per_page: int = 10


def _now() -> datetime:
    return datetime.now()


def _contact_name(row, columns: dict) -> str:
    for key in ("socio_1", "socio_2", "socio_3"):
        column = columns.get(key)
        if column:
            value = safe_series(pd.DataFrame([row]), column).iloc[0]
            if value and str(value).strip():
                return str(value).strip()
    return "—"


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _parse_datetime(value: str | None, fallback_date: date | None = None, fallback_time: str = "09:00") -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", ""))
        except ValueError:
            pass
    base = fallback_date or date.today()
    try:
        hour, minute = fallback_time.split(":")
        return datetime.combine(base, datetime.strptime(f"{hour}:{minute}", "%H:%M").time())
    except ValueError:
        return datetime.combine(base, datetime.min.time())


def _format_when(scheduled_at: str | None) -> tuple[str, str, str]:
    dt = _parse_datetime(scheduled_at) if scheduled_at else None
    if not dt:
        return "—", "—", "09:00"
    today = date.today()
    d = dt.date()
    time_part = dt.strftime("%H:%M")
    if d == today:
        return f"Hoje, {time_part}", d.strftime("%d/%m/%Y"), time_part
    yesterday = today - timedelta(days=1)
    if d == yesterday:
        return f"Ontem, {time_part}", d.strftime("%d/%m/%Y"), time_part
    return dt.strftime("%d/%m/%Y, %H:%M"), d.strftime("%d/%m/%Y"), time_part


def sugerir_proxima_acao(result: str) -> dict:
    today = date.today()
    suggestions = {
        "Cliente não respondeu": {"next_action": "Retornar contato", "days": 1, "channel": "WhatsApp"},
        "Pediu retorno": {"next_action": "Retornar contato", "days": 0, "channel": "WhatsApp", "require_schedule": True},
        "Interesse confirmado": {"next_action": "Qualificar lead", "days": 1, "channel": "WhatsApp"},
        "Lead qualificado": {"next_action": "Agendar reunião", "days": 2, "channel": "Reunião online"},
        "Reunião agendada": {"next_action": "Confirmar reunião", "days": 1, "channel": "Ligação"},
        "Reunião realizada": {"next_action": "Criar proposta", "days": 1, "channel": "E-mail"},
        "Proposta solicitada": {"next_action": "Criar proposta", "days": 1, "channel": "E-mail"},
        "Proposta enviada": {"next_action": "Fazer follow-up", "days": 2, "channel": "WhatsApp"},
        "Em negociação": {"next_action": "Negociar condições", "days": 2, "channel": "WhatsApp"},
        "Venda fechada": {"next_action": "Enviar contrato", "days": 1, "channel": "E-mail"},
        "Sem interesse": {"next_action": "Encerrar oportunidade", "days": 0, "channel": "Mensagem interna"},
        "Número incorreto": {"next_action": "Retornar contato", "days": 2, "channel": "Ligação"},
        "Sem WhatsApp": {"next_action": "Retornar contato", "days": 2, "channel": "Ligação"},
        "Contato inválido": {"next_action": "Encerrar oportunidade", "days": 0, "channel": "Mensagem interna"},
        "Cliente respondeu": {"next_action": "Qualificar lead", "days": 1, "channel": "WhatsApp"},
    }
    item = suggestions.get(result, {"next_action": "Retornar contato", "days": 1, "channel": "WhatsApp"})
    suggested_date = today + timedelta(days=item.get("days", 1))
    return {
        "next_action": item["next_action"],
        "next_action_date": suggested_date.isoformat(),
        "next_action_time": "10:00",
        "channel": item.get("channel", "WhatsApp"),
        "move_stage": _suggest_stage(result),
    }


def _suggest_stage(result: str) -> str:
    mapping = {
        "Lead qualificado": "Qualificação",
        "Reunião agendada": "Reunião",
        "Reunião realizada": "Reunião",
        "Proposta solicitada": "Proposta",
        "Proposta enviada": "Proposta",
        "Em negociação": "Negociação",
        "Venda fechada": "Fechado",
        "Sem interesse": "Fechado",
    }
    return mapping.get(result, "")


def calcular_status_atraso(record: dict) -> str:
    status = normalize_text(record.get("status")) or "pendente"
    if status in {"concluida", "cancelada"}:
        return status
    scheduled_at = record.get("scheduled_at")
    if not scheduled_at:
        return status
    dt = _parse_datetime(scheduled_at)
    if dt < _now() and status in {"pendente", "em_andamento", "reagendada", "atrasada"}:
        return "atrasada"
    return status


def _channel_from_overview(channel_key: str) -> str:
    return CHANNEL_KEY_TO_LABEL.get(normalize_text(channel_key).lower(), "WhatsApp")


def _process_from_overview(next_action: str) -> str:
    return OVERVIEW_TO_PROCESS.get(next_action, next_action if next_action in PROCESS_ACTIONS else "Retornar contato")


def sync_auto_activities(
    filtered_df: pd.DataFrame,
    columns: dict,
    tenant_id: str | None = None,
) -> None:
    queue = buscar_leads_para_acao(filtered_df, columns, tenant_id=tenant_id)
    for item in queue:
        activity_id = f"auto_{item['sheet_row']}_{item['rule_code']}"
        if activity_exists(tenant_id, activity_id):
            continue

        row_match = filtered_df[filtered_df["_sheet_row"] == item["sheet_row"]]
        if row_match.empty:
            continue
        row = row_match.iloc[0]
        lead = _lead_record(row, columns, tenant_id)
        process_action = _process_from_overview(item["next_action"])
        channel = _channel_from_overview(lead.get("next_action_type", "whatsapp"))

        scheduled_date = lead.get("next_action_date") or date.today()
        scheduled_time = lead.get("next_action_time") or "09:00"
        scheduled_at = datetime.combine(
            scheduled_date,
            datetime.strptime(scheduled_time, "%H:%M").time(),
        ).isoformat(timespec="seconds")

        save_activity(tenant_id, activity_id, {
            "tenant_id": tenant_id or DEFAULT_TENANT_ID,
            "sheet_row": item["sheet_row"],
            "lead_id": str(item["sheet_row"]),
            "company_id": str(item["sheet_row"]),
            "empresa": item["empresa"],
            "contato": _contact_name(row, columns),
            "assigned_user_id": item["owner"],
            "created_by_user_id": "system",
            "title": process_action,
            "process_action": process_action,
            "description": ACTION_DESCRIPTIONS.get(process_action, item["next_action"]),
            "channel": channel,
            "stage": item["stage"],
            "result": "",
            "result_notes": "",
            "scheduled_at": scheduled_at,
            "scheduled_date": scheduled_date.isoformat(),
            "scheduled_time": scheduled_time,
            "completed_at": None,
            "status": "atrasada" if item["priority_key"] in {"atrasado", "critico"} else "pendente",
            "priority": item["priority_score"],
            "origin_activity_id": None,
            "next_action": "",
            "next_action_date": "",
            "next_action_time": "09:00",
            "next_action_channel": "WhatsApp",
            "note": "",
            "rule_code": item["rule_code"],
            "deleted": False,
        })


def _serialize_activity(record: dict) -> dict:
    status = calcular_status_atraso(record)
    when_label, when_date, when_time = _format_when(record.get("scheduled_at"))
    channel = normalize_text(record.get("channel")) or "WhatsApp"
    process_action = normalize_text(record.get("process_action")) or normalize_text(record.get("title"))
    vendedor = normalize_text(record.get("assigned_user_id")) or "Sem vendedor"
    status_label = dict(ACTIVITY_STATUSES).get(status, status.title())

    return {
        "id": record["id"],
        "title": process_action,
        "description": normalize_text(record.get("description")) or ACTION_DESCRIPTIONS.get(process_action, ""),
        "icon": "💬" if channel == "WhatsApp" else "☎" if channel == "Ligação" else "📅" if "Reunião" in channel else "✉" if channel == "E-mail" else "📋",
        "empresa": record.get("empresa", "—"),
        "contato": record.get("contato", "—"),
        "stage": record.get("stage", "—"),
        "channel": channel,
        "channel_class": CHANNEL_CLASS.get(channel, "tarefa"),
        "vendedor": vendedor,
        "vendedor_initials": _initials(vendedor),
        "when_label": when_label,
        "when_date": when_date,
        "when_time": when_time,
        "scheduled_at": record.get("scheduled_at"),
        "scheduled_date": (record.get("scheduled_date") or "")[:10],
        "scheduled_time": record.get("scheduled_time") or when_time,
        "status": status,
        "status_label": status_label,
        "status_class": STATUS_CLASS.get(status, "pendente"),
        "result": record.get("result") or "",
        "result_notes": record.get("result_notes") or "",
        "proxima_acao": record.get("next_action") or process_action,
        "next_action_date": (record.get("next_action_date") or "")[:10],
        "next_action_time": record.get("next_action_time") or "09:00",
        "next_action_channel": record.get("next_action_channel") or channel,
        "note": record.get("note") or "",
        "move_stage": record.get("move_stage") or "",
        "sheet_row": int(record.get("sheet_row") or 0),
        "priority": int(record.get("priority") or 20),
        "show_extra": status == "concluida" or normalize_text(record.get("result")),
        "activity_dt": _parse_datetime(record.get("scheduled_at")),
    }


def buscar_atividades(
    filtered_df: pd.DataFrame,
    columns: dict,
    tenant_id: str | None = None,
    search: str = "",
) -> list[dict]:
    sync_auto_activities(filtered_df, columns, tenant_id)
    rows = []
    allowed_rows = set(filtered_df["_sheet_row"].astype(int).tolist()) if not filtered_df.empty else set()

    for record in list_activities(tenant_id):
        sheet_row = int(record.get("sheet_row") or 0)
        if allowed_rows and sheet_row not in allowed_rows:
            continue
        serialized = _serialize_activity(record)
        if search:
            blob = " | ".join([
                serialized["title"],
                serialized["empresa"],
                serialized["contato"],
                serialized["description"],
                serialized["vendedor"],
            ]).lower()
            if normalize_text(search).lower() not in blob and search.lower() not in blob:
                continue
        rows.append(serialized)

    rows.sort(key=lambda item: (item["status"] == "concluida", -item["priority"], item["activity_dt"]))
    return rows


def apply_activities_view(activities: list[dict], params: ActivitiesViewParams) -> list[dict]:
    result = activities
    if params.tab == "pendentes":
        result = [a for a in result if a["status"] in {"pendente", "em_andamento", "reagendada"}]
    elif params.tab == "concluidas":
        result = [a for a in result if a["status"] == "concluida"]
    elif params.tab == "atrasadas":
        result = [a for a in result if a["status"] == "atrasada"]

    if params.activity_type != "Todos os tipos":
        result = [a for a in result if a["title"] == params.activity_type]

    if params.channel != "Todos os canais":
        result = [a for a in result if a["channel"] == params.channel]

    if params.responsible != "Todos os responsáveis":
        result = [a for a in result if a["vendedor"] == params.responsible]

    if params.stage != "Todas as etapas":
        result = [a for a in result if a["stage"] == params.stage]

    return result


def build_activities_table(
    activities: list[dict],
    params: ActivitiesViewParams,
) -> dict:
    view = apply_activities_view(activities, params)
    total = len(view)
    total_pages = max(1, (total + params.per_page - 1) // params.per_page)
    page = max(1, min(params.page, total_pages))
    start = (page - 1) * params.per_page
    rows = view[start : start + params.per_page]
    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": params.per_page,
        "total_pages": total_pages,
        "from_record": start + 1 if total else 0,
        "to_record": min(start + params.per_page, total),
    }


def atualizar_cards_atividades(activities: list[dict], filters: DashboardFilters) -> list[dict]:
    today_items = [a for a in activities if str(a.get("when_label", "")).startswith("Hoje")]
    today_done = sum(1 for a in today_items if a["status"] == "concluida")
    pending = [a for a in activities if a["status"] in {"pendente", "em_andamento", "reagendada"}]
    calls_done = [a for a in activities if a["channel"] == "Ligação" and a["status"] == "concluida"]
    done_all = [a for a in activities if a["status"] == "concluida"]
    overdue = [a for a in activities if a["status"] == "atrasada"]

    return [
        {"label": "Atividades hoje", "value": len(today_items), "note": f"{today_done} concluída{'s' if today_done != 1 else ''}", "icon": "📋", "tone": "purple"},
        {"label": "Pendentes", "value": len(pending), "note": "em aberto", "icon": "⏳", "tone": "orange"},
        {"label": "Ligações realizadas", "value": len(calls_done), "note": "concluídas", "icon": "☎", "tone": "blue"},
        {"label": "Concluídas", "value": len(done_all), "note": "no período", "icon": "✓", "tone": "green"},
        {"label": "Atrasadas", "value": len(overdue), "note": "precisam de atenção", "icon": "⚠", "tone": "rose"},
    ]


def validar_conclusao(status: str, result: str, next_action: str) -> str | None:
    if status != "concluida":
        return None
    if not result or result == "Selecione":
        return "Selecione o resultado para concluir a atividade."
    if result in NO_NEXT_ACTION_RESULTS or result == "Outro":
        return None
    if not next_action:
        return "Defina a próxima ação para concluir esta atividade."
    return None


def verificar_duplicidade(tenant_id: str | None, origin_id: str, next_action: str, scheduled_date: str) -> bool:
    for record in list_activities(tenant_id):
        if record.get("origin_activity_id") != origin_id:
            continue
        if record.get("process_action") == next_action and (record.get("scheduled_date") or "")[:10] == scheduled_date[:10]:
            if record.get("status") in {"pendente", "em_andamento", "atrasada", "reagendada"}:
                return True
    return False


def criar_proxima_atividade(
    tenant_id: str | None,
    origin: dict,
    *,
    process_action: str,
    channel: str,
    assigned_user: str,
    scheduled_date: str,
    scheduled_time: str,
    sheet_row: int,
    empresa: str,
    contato: str,
    stage: str,
) -> dict | None:
    if verificar_duplicidade(tenant_id, origin.get("id"), process_action, scheduled_date):
        return None

    scheduled_at = _parse_datetime(None, date.fromisoformat(scheduled_date[:10]), scheduled_time).isoformat(timespec="seconds")
    return save_activity(tenant_id, None, {
        "tenant_id": tenant_id or DEFAULT_TENANT_ID,
        "sheet_row": sheet_row,
        "lead_id": str(sheet_row),
        "company_id": str(sheet_row),
        "empresa": empresa,
        "contato": contato,
        "assigned_user_id": assigned_user,
        "created_by_user_id": origin.get("updated_by", "system"),
        "title": process_action,
        "process_action": process_action,
        "description": ACTION_DESCRIPTIONS.get(process_action, ""),
        "channel": channel,
        "stage": stage,
        "result": "",
        "scheduled_at": scheduled_at,
        "scheduled_date": scheduled_date[:10],
        "scheduled_time": scheduled_time,
        "status": "pendente",
        "priority": origin.get("priority", 20),
        "origin_activity_id": origin.get("id"),
        "deleted": False,
    })


def registrar_historico(
    tenant_id: str | None,
    sheet_row: int,
    *,
    user: str,
    description: str,
    previous_status: str,
    new_status: str,
    result: str = "",
    note: str = "",
    move_stage: str = "",
) -> None:
    append_interaction(
        tenant_id,
        sheet_row,
        interaction_type="atividade_atualizada",
        description=description,
        user=user,
        previous_stage=previous_status,
        new_stage=move_stage or new_status,
        note=f"{result}. {note}".strip(". "),
    )


def atualizar_lead_pela_atividade(
    tenant_id: str | None,
    sheet_row: int,
    *,
    next_action: str,
    next_action_date: str,
    next_action_time: str,
    channel: str,
    move_stage: str,
    user: str,
) -> None:
    channel_key = {
        "WhatsApp": "whatsapp",
        "Ligação": "ligacao",
        "E-mail": "email",
        "Reunião online": "reuniao",
        "Reunião presencial": "reuniao",
    }.get(channel, "tarefa")

    payload = {
        "next_action_date": next_action_date[:10],
        "next_action_time": next_action_time,
        "next_action_type": channel_key,
        "next_action_description": next_action,
        "next_action_completed": False,
        "last_contact_at": _now().isoformat(timespec="seconds"),
    }
    if move_stage:
        payload["stage_override"] = move_stage
    save_lead_action(tenant_id, sheet_row, payload)


def atualizar_atividade_inline(
    tenant_id: str | None,
    activity_id: str,
    payload: dict,
    user: str,
) -> tuple[dict | None, str | None]:
    current = get_activity(tenant_id, activity_id)
    if not current:
        return None, "Atividade não encontrada."

    status = normalize_text(payload.get("status")) or current.get("status", "pendente")
    result = normalize_text(payload.get("result"))
    if result == "Selecione":
        result = ""
    next_action = normalize_text(payload.get("next_action"))
    next_action_date = normalize_text(payload.get("next_action_date"))
    next_action_time = normalize_text(payload.get("next_action_time")) or "09:00"
    next_action_channel = normalize_text(payload.get("next_action_channel")) or normalize_text(current.get("channel"))
    note = normalize_text(payload.get("note"))
    result_notes = normalize_text(payload.get("result_notes"))
    assigned_user = normalize_text(payload.get("assigned_user_id")) or current.get("assigned_user_id")
    move_stage = normalize_text(payload.get("move_stage"))
    channel = normalize_text(payload.get("channel")) or current.get("channel")

    if status == "reagendada":
        scheduled_date = normalize_text(payload.get("scheduled_date")) or next_action_date
        scheduled_time = normalize_text(payload.get("scheduled_time")) or next_action_time
        if not scheduled_date:
            return None, "Informe a nova data para reagendar."

    validation_error = validar_conclusao(status, result, next_action)
    if validation_error:
        return None, validation_error

    if result and not next_action and status != "concluida":
        suggestion = sugerir_proxima_acao(result)
        next_action = next_action or suggestion["next_action"]
        next_action_date = next_action_date or suggestion["next_action_date"]
        next_action_time = next_action_time or suggestion["next_action_time"]
        next_action_channel = next_action_channel or suggestion["channel"]
        move_stage = move_stage or suggestion.get("move_stage", "")

    updates = {
        "status": status,
        "result": result,
        "result_notes": result_notes,
        "note": note,
        "channel": channel,
        "assigned_user_id": assigned_user,
        "next_action": next_action,
        "next_action_date": next_action_date,
        "next_action_time": next_action_time,
        "next_action_channel": next_action_channel,
        "move_stage": move_stage,
        "updated_by": user,
    }

    if status == "concluida":
        updates["completed_at"] = _now().isoformat(timespec="seconds")
    if status == "reagendada":
        sd = normalize_text(payload.get("scheduled_date")) or next_action_date
        st = normalize_text(payload.get("scheduled_time")) or next_action_time
        updates["scheduled_date"] = sd[:10]
        updates["scheduled_time"] = st
        updates["scheduled_at"] = _parse_datetime(None, date.fromisoformat(sd[:10]), st).isoformat(timespec="seconds")

    updates["status"] = calcular_status_atraso({**current, **updates})
    saved = save_activity(tenant_id, activity_id, updates)

    sheet_row = int(current.get("sheet_row") or 0)
    registrar_historico(
        tenant_id,
        sheet_row,
        user=user,
        description=f"Atividade atualizada: {current.get('process_action')}",
        previous_status=current.get("status", ""),
        new_status=updates["status"],
        result=result,
        note=note or result_notes,
        move_stage=move_stage,
    )

    if status == "concluida" and next_action and next_action_date and result not in NO_NEXT_ACTION_RESULTS:
        criar_proxima_atividade(
            tenant_id,
            {"id": activity_id, "priority": current.get("priority", 20), "updated_by": user},
            process_action=next_action,
            channel=next_action_channel,
            assigned_user=assigned_user,
            scheduled_date=next_action_date,
            scheduled_time=next_action_time,
            sheet_row=sheet_row,
            empresa=current.get("empresa", "—"),
            contato=current.get("contato", "—"),
            stage=move_stage or current.get("stage", "—"),
        )
        atualizar_lead_pela_atividade(
            tenant_id,
            sheet_row,
            next_action=next_action,
            next_action_date=next_action_date,
            next_action_time=next_action_time,
            channel=next_action_channel,
            move_stage=move_stage,
            user=user,
        )

    return _serialize_activity({"id": activity_id, **saved}), None


def cancelar_atividade(tenant_id: str | None, activity_id: str, user: str, reason: str = "") -> tuple[bool, str | None]:
    current = get_activity(tenant_id, activity_id)
    if not current:
        return False, "Atividade não encontrada."
    save_activity(tenant_id, activity_id, {"status": "cancelada", "note": reason, "updated_by": user})
    registrar_historico(
        tenant_id,
        int(current.get("sheet_row") or 0),
        user=user,
        description="Atividade cancelada",
        previous_status=current.get("status", ""),
        new_status="cancelada",
        note=reason,
    )
    return True, None


def build_activity_page_context(
    df: pd.DataFrame,
    columns: dict,
    filters: DashboardFilters,
    params: ActivitiesViewParams,
    tenant_id: str | None = None,
) -> dict:
    filtered_df = apply_dashboard_filters(df, columns, filters)
    activities = buscar_atividades(filtered_df, columns, tenant_id, search=filters.search)
    table = build_activities_table(activities, params)
    stage_options = ["Todas as etapas"] + [stage for stage in STAGE_MAP.keys()]
    return {
        "activities": activities,
        "table": table,
        "kpi_cards": atualizar_cards_atividades(activities, filters),
        "process_actions": PROCESS_ACTIONS,
        "channels": CHANNELS,
        "status_options": ACTIVITY_STATUSES,
        "result_options": RESULT_OPTIONS,
        "stage_options": stage_options,
        "type_options": ["Todos os tipos"] + PROCESS_ACTIONS,
        "channel_options": ["Todos os canais"] + CHANNELS,
    }
