"""Serviço de atividades comerciais — conectado à Visão Geral."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from config.crm_options import (
    ACTION_DESCRIPTIONS,
    ACTIVITY_RESULT_OPTIONS,
    ACTIVITY_STATUS_LABELS,
    ACTIVITY_TYPE_DEFAULT_ACTION,
    ACTIVITY_TYPE_OPTIONS,
    ACTIVITY_TYPE_STAGE_HINT,
    CHANNEL_CLASS,
    CHANNEL_OPTIONS,
    LOST_REASON_OPTIONS,
    NEW_ACTIVITY_STATUS_KEYS,
    NEXT_ACTION_BY_STAGE,
    NEXT_ACTION_OPTIONS,
    NO_NEXT_ACTION_RESULTS,
    OVERVIEW_ACTION_TO_PROCESS,
    PIPELINE_STAGE_OPTIONS,
    PIPELINE_STAGE_SLA,
    PRIORITY_OPTIONS,
    PRIORITY_SCORE_VALUES,
    PROCESS_ACTION_OPTIONS,
    SELECTABLE_ACTIVITY_STATUS_KEYS,
)
from app.services.activities_storage import (
    DEFAULT_TENANT_ID,
    activity_exists,
    get_activity,
    list_activities,
    save_activity,
)
from app.services.crm_validation_service import (
    calcular_status_atraso,
    channel_label_to_key,
    get_actions_for_stage,
    get_next_action_options,
    normalize_legacy_action,
    normalize_legacy_channel,
    normalize_legacy_next_action,
    normalize_legacy_result,
    normalize_legacy_stage,
    normalize_legacy_status_key,
    normalize_opportunity_status,
    resolve_display_stage,
    resolve_pipeline_stage,
    suggest_from_result,
    validate_activity_payload,
    validate_completion,
)
from app.services.filters import DashboardFilters, apply_dashboard_filters
from app.services.followup_service import _lead_record, buscar_leads_para_acao, _minutes_since
from app.services.lead_actions_storage import append_interaction, get_lead_action, save_lead_action
from app.services.legacy_core import as_python_datetime, normalize_digits, normalize_text, safe_series

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


def calcular_sla_atividade(record: dict, status: str, now: datetime | None = None) -> tuple[str, str, str]:
    """Retorna (sla_key, sla_label, sla_class) para o card do kanban."""
    if status == "concluida":
        return "concluido", "Concluído", "concluido"
    if status == "cancelada":
        return "cancelada", "Cancelada", "cancelada"

    now = now or _now()
    scheduled_at = record.get("scheduled_at")
    if not scheduled_at:
        return "no_prazo", "No prazo", "no_prazo"

    dt = _parse_datetime(scheduled_at)
    today = now.date()
    scheduled_day = dt.date()
    priority = int(record.get("priority") or 0)

    if status == "atrasada" or dt < now:
        days_late = max(0, (today - scheduled_day).days)
        hours_late = max(0, (now - dt).total_seconds() / 3600)
        stage = normalize_legacy_stage(record.get("stage")) or "Novo Lead"
        sla = PIPELINE_STAGE_SLA.get(stage, {})
        critical_days = max(3, int(sla.get("max_days", 2) or 2) + 1)
        critical_hours = max(24, int(sla.get("max_hours", 24) or 24) * 2)

        if priority >= 100 or days_late >= critical_days or hours_late >= critical_hours:
            return "critico", "Crítico", "critico"
        return "atrasado", "Atrasado", "atrasado"

    if scheduled_day == today:
        return "vence_hoje", "Vence hoje", "vence_hoje"

    return "no_prazo", "No prazo", "no_prazo"


def _sla_sort_key(item: dict) -> tuple:
    order = {
        "critico": 0,
        "atrasado": 1,
        "vence_hoje": 2,
        "no_prazo": 3,
        "concluido": 4,
        "cancelada": 5,
    }
    return (
        order.get(item.get("sla_key", "no_prazo"), 9),
        item.get("activity_dt") or datetime.max,
    )


def sugerir_proxima_acao(result: str) -> dict:
    return suggest_from_result(result)


def _channel_from_overview(channel_key: str) -> str:
    from config.crm_options import CHANNEL_KEY_TO_LABEL

    return CHANNEL_KEY_TO_LABEL.get(normalize_text(channel_key).lower(), "WhatsApp")


def _process_from_overview(next_action: str) -> str:
    mapped = OVERVIEW_ACTION_TO_PROCESS.get(next_action, next_action)
    return normalize_legacy_action(mapped) or "Retornar contato"


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
        stage = normalize_legacy_stage(item.get("stage")) or lead.get("stage", "Novo Lead")

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
            "stage": stage,
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
    channel = normalize_legacy_channel(record.get("channel"))
    process_action = normalize_legacy_action(record.get("process_action") or record.get("title"))
    stage_raw = record.get("stage", "")
    stage, stage_legacy = resolve_display_stage(stage_raw)
    result = normalize_legacy_result(record.get("result")) or record.get("result") or ""
    if result and result not in ACTIVITY_RESULT_OPTIONS and result != "Selecione":
        result_display = f"{result} (Valor legado)"
    else:
        result_display = result
    vendedor = normalize_text(record.get("assigned_user_id")) or "Sem vendedor"
    status_label = ACTIVITY_STATUS_LABELS.get(status, status.title())
    sla_key, sla_label, sla_class = calcular_sla_atividade(record, status)
    allowed_next_actions = get_next_action_options(record.get("next_action", ""))
    next_action_value = normalize_legacy_next_action(record.get("next_action")) or ""
    next_action_raw = normalize_text(record.get("next_action"))
    if next_action_raw and not next_action_value:
        proxima_acao = f"{next_action_raw} (Valor legado)"
    else:
        proxima_acao = next_action_value or normalize_legacy_next_action(process_action) or process_action

    return {
        "id": record["id"],
        "title": process_action or record.get("title", "—"),
        "description": normalize_text(record.get("description")) or ACTION_DESCRIPTIONS.get(process_action, ""),
        "icon": "💬" if channel == "WhatsApp" else "☎" if channel == "Ligação" else "📅" if "Reunião" in channel else "✉" if channel == "E-mail" else "📋",
        "empresa": record.get("empresa", "—"),
        "contato": record.get("contato", "—"),
        "stage": stage,
        "stage_legacy": stage_legacy,
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
        "sla_key": sla_key,
        "sla_label": sla_label,
        "sla_class": sla_class,
        "result": result_display,
        "result_value": result,
        "result_notes": record.get("result_notes") or "",
        "proxima_acao": proxima_acao,
        "proxima_acao_value": next_action_value or next_action_raw,
        "allowed_next_actions": allowed_next_actions,
        "next_action_date": (record.get("next_action_date") or "")[:10],
        "next_action_time": record.get("next_action_time") or "09:00",
        "next_action_channel": normalize_legacy_channel(record.get("next_action_channel")) or channel,
        "note": record.get("note") or "",
        "move_stage": normalize_legacy_stage(record.get("move_stage")) or "",
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


def build_activities_kanban(
    activities: list[dict],
    params: ActivitiesViewParams,
) -> list[dict]:
    view = apply_activities_view(activities, params)
    columns: list[dict] = []
    for index, stage in enumerate(PIPELINE_STAGE_OPTIONS, start=1):
        cards = [item for item in view if item.get("stage") == stage]
        cards.sort(key=_sla_sort_key)
        columns.append({
            "index": index,
            "stage": stage,
            "label": f"{index}. {stage}",
            "count": len(cards),
            "cards": cards,
        })
    return columns


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
    return validate_completion(status, result, next_action)


def verificar_duplicidade(tenant_id: str | None, origin_id: str, next_action: str, scheduled_date: str) -> bool:
    normalized_action = normalize_legacy_action(next_action)
    for record in list_activities(tenant_id):
        if record.get("origin_activity_id") != origin_id:
            continue
        if normalize_legacy_action(record.get("process_action")) == normalized_action and (record.get("scheduled_date") or "")[:10] == scheduled_date[:10]:
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
    process_action = normalize_legacy_action(process_action)
    channel = normalize_legacy_channel(channel)
    stage = normalize_legacy_stage(stage) or "Novo Lead"

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
    interaction_type: str = "atividade_atualizada",
) -> None:
    if not sheet_row:
        return
    meta_note = f"{result}. {note}".strip(". ")
    append_interaction(
        tenant_id,
        sheet_row,
        interaction_type=interaction_type,
        description=description,
        user=user,
        previous_stage=previous_status,
        new_stage=move_stage or new_status,
        note=meta_note,
    )


def _append_activity_timeline(
    tenant_id: str | None,
    activity_id: str,
    *,
    label: str,
    user: str,
    note: str = "",
) -> None:
    from app.services.activities_storage import get_activity, save_activity

    record = get_activity(tenant_id, activity_id)
    if not record:
        return
    timeline = record.get("timeline")
    if not isinstance(timeline, list):
        timeline = []
    timeline.append({
        "at": _now().isoformat(timespec="seconds"),
        "label": label,
        "user": user,
        "note": note,
    })
    save_activity(tenant_id, activity_id, {"timeline": timeline[-100:]})


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
    opportunity_status: str = "",
    lost_reason: str = "",
    close_value: str = "",
    close_payment: str = "",
) -> None:
    channel_key = channel_label_to_key(channel)
    payload = {
        "next_action_date": next_action_date[:10],
        "next_action_time": next_action_time,
        "next_action_type": channel_key,
        "next_action_description": normalize_legacy_next_action(next_action),
        "next_action_completed": False,
        "last_contact_at": _now().isoformat(timespec="seconds"),
    }
    if move_stage:
        payload["stage_override"] = normalize_legacy_stage(move_stage)
    if opportunity_status:
        payload["opportunity_status"] = normalize_opportunity_status(opportunity_status)
        if opportunity_status in {"Fechada ganha", "Fechada perdida", "Encerrada"}:
            payload["closed_at"] = _now().isoformat(timespec="seconds")
    if lost_reason:
        payload["lost_reason"] = lost_reason
    if close_value:
        payload["close_value"] = close_value
    if close_payment:
        payload["close_payment_method"] = close_payment
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

    status = normalize_legacy_status_key(payload.get("status") or current.get("status", "pendente"))
    if status == "atrasada":
        status = normalize_legacy_status_key(current.get("status", "pendente"))

    result = normalize_legacy_result(payload.get("result"))
    next_action = normalize_legacy_next_action(payload.get("next_action"))
    next_action_date = normalize_text(payload.get("next_action_date"))
    next_action_time = normalize_text(payload.get("next_action_time")) or "09:00"
    next_action_channel = normalize_legacy_channel(payload.get("next_action_channel") or current.get("channel"))
    note = normalize_text(payload.get("note"))
    result_notes = normalize_text(payload.get("result_notes"))
    assigned_user = normalize_text(payload.get("assigned_user_id")) or current.get("assigned_user_id")
    move_stage = normalize_legacy_stage(payload.get("move_stage"))
    channel = normalize_legacy_channel(payload.get("channel") or current.get("channel"))
    current_stage = normalize_legacy_stage(current.get("stage")) or "Novo Lead"

    validation_payload = {
        "stage": current_stage,
        "move_stage": move_stage,
        "status": status,
        "result": result,
        "channel": channel,
        "next_action_channel": next_action_channel,
        "next_action": next_action,
        "process_action": current.get("process_action"),
    }
    validation_error = validate_activity_payload(validation_payload)
    if validation_error:
        return None, validation_error

    if status == "cancelada" and not note and not result_notes:
        return None, "Informe o motivo do cancelamento."

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
        "stage": move_stage or current_stage,
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
    process_action = normalize_text(current.get("process_action")) or "Atividade"
    if status == "concluida":
        history_label = f"Atividade concluída: {process_action}"
        if result:
            history_label = f"{history_label} — {result}"
        interaction_type = "atividade_concluida"
    elif status == "reagendada":
        sd = normalize_text(updates.get("scheduled_date") or payload.get("scheduled_date") or "")
        st = normalize_text(updates.get("scheduled_time") or payload.get("scheduled_time") or "")
        history_label = f"Atividade reagendada para {sd} às {st}".strip()
        interaction_type = "atividade_reagendada"
    else:
        history_label = f"Atividade atualizada: {process_action}"
        interaction_type = "atividade_atualizada"

    registrar_historico(
        tenant_id,
        sheet_row,
        user=user,
        description=history_label,
        previous_status=current.get("status", ""),
        new_status=updates["status"],
        result=result,
        note=note or result_notes,
        move_stage=move_stage,
        interaction_type=interaction_type,
    )
    if not sheet_row:
        _append_activity_timeline(
            tenant_id,
            activity_id,
            label=history_label,
            user=user,
            note=note or result_notes,
        )

    suggestion = suggest_from_result(result, current_stage) if result else {}
    opportunity_status = suggestion.get("opportunity_status", "")
    next_activity_stage = suggestion.get("activity_stage") or move_stage or current_stage

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
            stage=next_activity_stage,
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
            opportunity_status=opportunity_status,
            lost_reason=result_notes if result == "Sem interesse" else "",
        )
    elif status == "concluida" and result in NO_NEXT_ACTION_RESULTS:
        atualizar_lead_pela_atividade(
            tenant_id,
            sheet_row,
            next_action="Encerrar processo comercial",
            next_action_date="",
            next_action_time="",
            channel=next_action_channel,
            move_stage=move_stage,
            user=user,
            opportunity_status=opportunity_status or ("Fechada perdida" if result == "Sem interesse" else "Encerrada"),
            lost_reason=result_notes or result,
        )

    return _serialize_activity({"id": activity_id, **saved}), None


def cancelar_atividade(tenant_id: str | None, activity_id: str, user: str, reason: str = "") -> tuple[bool, str | None]:
    current = get_activity(tenant_id, activity_id)
    if not current:
        return False, "Atividade não encontrada."
    if not reason:
        return False, "Informe o motivo do cancelamento."
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


def _format_timeline_at(value) -> str:
    dt = as_python_datetime(value) if value else None
    if not dt and value:
        try:
            dt = _parse_datetime(str(value))
        except (TypeError, ValueError):
            dt = None
    if not dt:
        return "—"
    return dt.strftime("%d/%m/%Y %H:%M")


def _timeline_sort_value(value) -> datetime:
    dt = as_python_datetime(value)
    if dt:
        return dt
    try:
        return _parse_datetime(str(value))
    except (TypeError, ValueError):
        return datetime.min


def _timeline_meta(user: str = "", note: str = "") -> str:
    parts = [normalize_text(user), normalize_text(note)]
    return " · ".join(part for part in parts if part)


def _interaction_to_timeline_step(interaction: dict) -> dict:
    description = normalize_text(interaction.get("description")) or "Ação registrada"
    return {
        "label": description,
        "at": _format_timeline_at(interaction.get("at")),
        "meta": _timeline_meta(interaction.get("user", ""), interaction.get("note", "")),
        "state": "done",
        "sort_at": interaction.get("at"),
    }


def _build_activity_timeline(
    tenant_id: str | None,
    record: dict,
    activity: dict,
    lead_created_at,
) -> list[dict]:
    steps: list[dict] = []
    activity_created = record.get("created_at")
    sheet_row = int(activity.get("sheet_row") or 0)

    if lead_created_at or activity_created:
        steps.append({
            "label": "Lead entrou",
            "at": _format_timeline_at(lead_created_at or activity_created),
            "meta": "",
            "state": "done",
            "sort_at": lead_created_at or activity_created,
        })

    process_action = normalize_text(activity.get("title")) or "Atividade"
    steps.append({
        "label": f"Atividade criada: {process_action}",
        "at": _format_timeline_at(activity_created),
        "meta": normalize_text(activity.get("vendedor")),
        "state": "done",
        "sort_at": activity_created,
    })

    if sheet_row:
        stored = get_lead_action(tenant_id, sheet_row) or {}
        interactions = stored.get("interactions") if isinstance(stored.get("interactions"), list) else []
        for interaction in interactions:
            if not isinstance(interaction, dict):
                continue
            if interaction.get("type") == "atividade_criada":
                continue
            steps.append(_interaction_to_timeline_step(interaction))
    else:
        activity_timeline = record.get("timeline") if isinstance(record.get("timeline"), list) else []
        for item in activity_timeline:
            if not isinstance(item, dict):
                continue
            steps.append({
                "label": normalize_text(item.get("label")) or "Ação registrada",
                "at": _format_timeline_at(item.get("at")),
                "meta": _timeline_meta(item.get("user", ""), item.get("note", "")),
                "state": "done",
                "sort_at": item.get("at"),
            })

    steps.sort(key=lambda step: _timeline_sort_value(step.get("sort_at")))

    if activity.get("status") != "concluida" and steps:
        for step in steps:
            step["state"] = "done"
        steps[-1]["state"] = "current"

    for step in steps:
        step.pop("sort_at", None)

    return steps


def build_activity_timeline_for_activity(
    tenant_id: str | None,
    activity_id: str,
    df: pd.DataFrame,
    columns: dict,
) -> list[dict]:
    record = get_activity(tenant_id, activity_id)
    if not record:
        return []
    activity = _serialize_activity(record)
    lead_created_at = None
    sheet_row = activity.get("sheet_row")
    if sheet_row and not df.empty:
        row_match = df[df["_sheet_row"] == sheet_row]
        if not row_match.empty:
            lead = _lead_record(row_match.iloc[0], columns, tenant_id)
            lead_created_at = lead.get("created_at")
    return _build_activity_timeline(tenant_id, record, activity, lead_created_at)


def build_activity_detail_panel(
    tenant_id: str | None,
    activity_id: str,
    df: pd.DataFrame,
    columns: dict,
) -> dict | None:
    record = get_activity(tenant_id, activity_id)
    if not record:
        return None

    activity = _serialize_activity(record)
    stage = activity["stage"]
    sla_deadline = PIPELINE_STAGE_SLA.get(stage, {}).get("label", "—")
    next_action_display = (
        activity["proxima_acao"]
        if activity["proxima_acao"] and activity["proxima_acao"] != "—"
        else NEXT_ACTION_BY_STAGE.get(stage, activity["title"])
    )
    observation = normalize_text(activity["note"]) or normalize_text(activity["description"]) or "—"

    lead_created_at = None
    sheet_row = activity["sheet_row"]
    if sheet_row and not df.empty:
        row_match = df[df["_sheet_row"] == sheet_row]
        if not row_match.empty:
            lead = _lead_record(row_match.iloc[0], columns, tenant_id)
            lead_created_at = lead.get("created_at")

    suggestion = suggest_from_result("Cliente respondeu", stage)
    next_action_current = normalize_legacy_next_action(next_action_display) or next_action_display
    next_action_options = get_next_action_options(activity.get("proxima_acao_value") or next_action_current)
    timeline = _build_activity_timeline(tenant_id, record, activity, lead_created_at)

    return {
        "activity": activity,
        "sla_deadline": sla_deadline,
        "next_action_display": next_action_display,
        "observation": observation,
        "timeline": timeline,
        "lead_href": f"/cadastro/todos/{sheet_row}" if sheet_row else "/cadastro/todos",
        "result_options": [opt for opt in ACTIVITY_RESULT_OPTIONS if opt != "Selecione"],
        "default_next_action": NEXT_ACTION_BY_STAGE.get(stage, activity["title"]),
        "default_next_action_date": suggestion.get("next_action_date", date.today().isoformat()),
        "default_next_action_time": suggestion.get("next_action_time", "10:00"),
        "next_action_current": next_action_current,
        "next_action_options": next_action_options,
        "next_action_options_json": json.dumps(next_action_options, ensure_ascii=False),
        "channels": CHANNEL_OPTIONS,
    }


def atualizar_proxima_acao_atividade(
    tenant_id: str | None,
    activity_id: str,
    next_action: str,
    user: str,
) -> tuple[str | None, str | None]:
    from app.services.activities_storage import get_activity, save_activity
    from app.services.leads import atualizar_proxima_acao_lead

    record = get_activity(tenant_id, activity_id)
    if not record:
        return None, "Atividade não encontrada."

    normalized = normalize_legacy_next_action(next_action) or normalize_text(next_action)
    if not normalized:
        return None, "Próxima ação inválida."

    save_activity(tenant_id, activity_id, {"next_action": normalized})

    sheet_row = int(record.get("sheet_row") or 0)
    history_label = f"Próxima ação alterada para: {normalized}"
    if sheet_row:
        atualizar_proxima_acao_lead(tenant_id, sheet_row, normalized, user)
    elif not sheet_row:
        _append_activity_timeline(
            tenant_id,
            activity_id,
            label=history_label,
            user=user,
        )

    return normalized, None


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
    kanban = build_activities_kanban(activities, params)
    stage_options = ["Todas as etapas"] + PIPELINE_STAGE_OPTIONS
    return {
        "activities": activities,
        "table": table,
        "kanban": kanban,
        "kpi_cards": atualizar_cards_atividades(activities, filters),
        "process_actions": PROCESS_ACTION_OPTIONS,
        "channels": CHANNEL_OPTIONS,
        "status_options": SELECTABLE_ACTIVITY_STATUS_KEYS,
        "result_options": [opt for opt in ACTIVITY_RESULT_OPTIONS if opt != "Selecione"],
        "next_action_options": NEXT_ACTION_OPTIONS,
        "stage_options": stage_options,
        "pipeline_stage_options": PIPELINE_STAGE_OPTIONS,
        "type_options": ["Todos os tipos"] + PROCESS_ACTION_OPTIONS,
        "channel_options": ["Todos os canais"] + CHANNEL_OPTIONS,
    }


def buscar_acoes_por_etapa(stage: str) -> list[str]:
    return get_actions_for_stage(normalize_legacy_stage(stage) or "Novo Lead")


def buscar_responsaveis_permitidos(seller_options: list[str], current_user: str, is_admin_user: bool) -> list[str]:
    sellers = [normalize_text(item) for item in seller_options if normalize_text(item)]
    if is_admin_user:
        return sellers
    username = normalize_text(current_user)
    if not username:
        return sellers
    allowed = [seller for seller in sellers if seller.lower() == username.lower()]
    return allowed or ([username] if username else sellers)


def _lead_search_blob(row, columns: dict, lead: dict) -> str:
    contato = _contact_name(row, columns)
    parts = [
        lead.get("empresa", ""),
        contato,
        lead.get("phone", ""),
        lead.get("email", ""),
        normalize_text(row.get("_cnpj", "")),
        normalize_digits(lead.get("phone", "")),
    ]
    return " | ".join(normalize_text(part).lower() for part in parts if normalize_text(part))


def _lead_is_accessible(row, current_user: str, is_admin_user: bool) -> bool:
    if is_admin_user:
        return True
    username = normalize_text(current_user).lower()
    vendedor = normalize_text(row.get("_vendedor", "")).lower()
    return not username or not vendedor or vendedor == username


def buscar_leads_para_atividade(
    filtered_df: pd.DataFrame,
    columns: dict,
    tenant_id: str | None,
    query: str,
    current_user: str = "",
    is_admin_user: bool = False,
    limit: int = 12,
) -> list[dict]:
    term = normalize_text(query).lower()
    if not term or filtered_df.empty:
        return []

    results: list[dict] = []
    for _, row in filtered_df.iterrows():
        if not _lead_is_accessible(row, current_user, is_admin_user):
            continue
        lead = _lead_record(row, columns, tenant_id)
        if lead.get("opportunity_status") in {"Fechada ganha", "Fechada perdida", "Encerrada"}:
            grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
            if grouped in {"Fechado", "Sem interesse"}:
                continue
        blob = _lead_search_blob(row, columns, lead)
        digits = normalize_digits(term)
        if term not in blob and (not digits or digits not in blob.replace(" ", "")):
            continue
        stage = lead.get("stage", "Novo Lead")
        actions = get_actions_for_stage(stage)
        results.append({
            "sheet_row": lead["sheet_row"],
            "empresa": lead["empresa"],
            "contato": _contact_name(row, columns),
            "stage": stage,
            "vendedor": lead["vendedor"],
            "phone": lead.get("phone", "") or "—",
            "email": lead.get("email", "") or "—",
            "suggested_action": actions[0] if actions else ACTIVITY_TYPE_DEFAULT_ACTION.get("Contato", "Fazer primeiro contato"),
        })
        if len(results) >= limit:
            break
    return results


def sugerir_prioridade(stage: str, scheduled_at: datetime | None, created_at=None) -> str:
    if stage == "Novo Lead":
        minutes = _minutes_since(created_at)
        if minutes is not None and minutes >= 60:
            return "Crítica"
    if not scheduled_at:
        return "Média"
    today = date.today()
    scheduled_date = scheduled_at.date()
    if scheduled_date <= today:
        return "Alta"
    if scheduled_date == today + timedelta(days=1):
        return "Média"
    return "Baixa"


def _priority_score(label: str) -> int:
    return PRIORITY_SCORE_VALUES.get(normalize_text(label) or "Média", 20)


def _next_business_day(base: date, days: int = 1) -> date:
    target = base + timedelta(days=days)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def validar_nova_atividade(payload: dict, *, is_admin_user: bool = False, allow_past_date: bool = False) -> str | None:
    sheet_row = int(payload.get("sheet_row") or 0)
    if not sheet_row:
        return "Selecione um lead ou empresa."

    stage = normalize_legacy_stage(payload.get("stage"))
    activity_type = normalize_text(payload.get("activity_type"))
    process_action = normalize_legacy_action(payload.get("process_action"))
    channel = normalize_legacy_channel(payload.get("channel"))
    assigned_user = normalize_text(payload.get("assigned_user_id"))
    scheduled_date = normalize_text(payload.get("scheduled_date"))
    scheduled_time = normalize_text(payload.get("scheduled_time")) or "09:00"
    status = normalize_legacy_status_key(payload.get("status") or "pendente")
    result = normalize_legacy_result(payload.get("result"))
    next_action = normalize_legacy_next_action(payload.get("next_action"))

    if activity_type and activity_type not in ACTIVITY_TYPE_OPTIONS:
        return "A opção selecionada não pertence ao processo comercial atual. Atualize o campo antes de salvar."
    if not stage:
        return "Selecione a etapa do funil."
    if not process_action:
        return "Selecione uma atividade compatível com a etapa."
    if not assigned_user:
        return "Selecione o responsável."
    if not scheduled_date:
        return "Informe data e horário."

    validation_payload = {
        "stage": stage,
        "move_stage": normalize_legacy_stage(payload.get("move_stage")),
        "status": status,
        "result": result,
        "channel": channel,
        "next_action_channel": normalize_legacy_channel(payload.get("next_action_channel") or channel),
        "next_action": next_action,
        "process_action": process_action,
    }
    validation_error = validate_activity_payload(validation_payload)
    if validation_error:
        return validation_error

    if status in {"pendente", "em_andamento", "reagendada"} and not allow_past_date and not is_admin_user:
        try:
            scheduled_dt = _parse_datetime(None, date.fromisoformat(scheduled_date[:10]), scheduled_time)
            if scheduled_dt < _now().replace(second=0, microsecond=0):
                return "Informe data e horário."
        except ValueError:
            return "Informe data e horário."

    if status == "concluida":
        completion_error = validate_completion(status, result, next_action)
        if completion_error:
            return completion_error
        if result == "Sem interesse" and not normalize_text(payload.get("lost_reason")):
            return "Informe o motivo da perda."

    if channel == "Outro" and not normalize_text(payload.get("channel_other")):
        return "Descreva o canal selecionado como Outro."

    if activity_type == "Outro" and not normalize_text(payload.get("description")):
        return "Descreva a atividade quando selecionar o tipo Outro."

    return None


def movimentar_etapa_lead(
    tenant_id: str | None,
    sheet_row: int,
    *,
    from_stage: str,
    to_stage: str,
    user: str,
) -> None:
    to_stage = normalize_legacy_stage(to_stage)
    if not to_stage:
        return
    save_lead_action(tenant_id, sheet_row, {
        "stage_override": to_stage,
        "stage_entered_at": _now().isoformat(timespec="seconds"),
        "previous_stage": normalize_legacy_stage(from_stage),
    })
    append_interaction(
        tenant_id,
        sheet_row,
        interaction_type="etapa_alterada",
        description=f"Lead movido de {from_stage} para {to_stage}",
        user=user,
        previous_stage=from_stage,
        new_stage=to_stage,
    )


def mover_atividade_kanban(
    tenant_id: str | None,
    activity_id: str,
    new_stage: str,
    user: str,
) -> tuple[dict | None, str | None]:
    current = get_activity(tenant_id, activity_id)
    if not current:
        return None, "Atividade não encontrada."

    new_stage = normalize_legacy_stage(new_stage)
    if not new_stage or new_stage not in PIPELINE_STAGE_OPTIONS:
        return None, "Etapa inválida."

    old_stage = normalize_legacy_stage(current.get("stage")) or "Novo Lead"
    if old_stage == new_stage:
        return _serialize_activity({"id": activity_id, **current}), None

    saved = save_activity(tenant_id, activity_id, {
        "stage": new_stage,
        "move_stage": new_stage,
        "updated_by": user,
    })

    sheet_row = int(current.get("sheet_row") or 0)
    if sheet_row:
        movimentar_etapa_lead(
            tenant_id,
            sheet_row,
            from_stage=old_stage,
            to_stage=new_stage,
            user=user,
        )
        registrar_historico(
            tenant_id,
            sheet_row,
            user=user,
            description=f"Atividade movida para {new_stage}",
            previous_status=current.get("status", ""),
            new_status=current.get("status", ""),
            move_stage=new_stage,
        )

    return _serialize_activity({"id": activity_id, **saved}), None


def registrar_historico_atividade(
    tenant_id: str | None,
    sheet_row: int,
    *,
    user: str,
    process_action: str,
    stage: str,
    activity_type: str,
    channel: str,
    assigned_user: str,
    status: str,
    result: str = "",
    note: str = "",
    move_stage: str = "",
) -> None:
    append_interaction(
        tenant_id,
        sheet_row,
        interaction_type="atividade_criada",
        description=f"Atividade criada: {process_action}",
        user=user,
        previous_stage=stage,
        new_stage=move_stage or stage,
        note=f"Tipo: {activity_type}. Canal: {channel}. Responsável: {assigned_user}. Status: {status}. {result}. {note}".strip(),
    )


def verificar_duplicidade_atividade(
    tenant_id: str | None,
    sheet_row: int,
    process_action: str,
    scheduled_date: str,
) -> bool:
    normalized_action = normalize_legacy_action(process_action)
    for record in list_activities(tenant_id):
        if int(record.get("sheet_row") or 0) != sheet_row:
            continue
        if normalize_legacy_action(record.get("process_action")) != normalized_action:
            continue
        if (record.get("scheduled_date") or "")[:10] != scheduled_date[:10]:
            continue
        if record.get("status") in {"pendente", "em_andamento", "atrasada", "reagendada"}:
            return True
    return False


def criar_atividade(
    tenant_id: str | None,
    payload: dict,
    user: str,
    *,
    is_admin_user: bool = False,
) -> tuple[dict | None, str | None]:
    allow_past = is_admin_user or normalize_legacy_status_key(payload.get("status")) == "concluida"
    validation_error = validar_nova_atividade(payload, is_admin_user=is_admin_user, allow_past_date=allow_past)
    if validation_error:
        return None, validation_error

    sheet_row = int(payload["sheet_row"])
    stage = normalize_legacy_stage(payload.get("stage")) or "Novo Lead"
    activity_type = normalize_text(payload.get("activity_type")) or "Contato"
    process_action = normalize_legacy_action(payload.get("process_action"))
    channel = normalize_legacy_channel(payload.get("channel"))
    channel_other = normalize_text(payload.get("channel_other"))
    assigned_user = normalize_text(payload.get("assigned_user_id"))
    scheduled_date = normalize_text(payload.get("scheduled_date"))[:10]
    scheduled_time = normalize_text(payload.get("scheduled_time")) or "09:00"
    status = normalize_legacy_status_key(payload.get("status") or "pendente")
    priority_label = normalize_text(payload.get("priority")) or "Média"
    description = normalize_text(payload.get("description"))
    result = normalize_legacy_result(payload.get("result"))
    note = normalize_text(payload.get("note"))
    result_notes = normalize_text(payload.get("result_notes"))
    next_action = normalize_legacy_next_action(payload.get("next_action"))
    next_action_date = normalize_text(payload.get("next_action_date"))
    next_action_time = normalize_text(payload.get("next_action_time")) or "10:00"
    next_action_channel = normalize_legacy_channel(payload.get("next_action_channel") or channel)
    next_action_assigned = normalize_text(payload.get("next_action_assigned")) or assigned_user
    move_stage = normalize_legacy_stage(payload.get("move_stage"))
    move_stage_confirm = str(payload.get("move_stage_confirm", "")).lower() in {"1", "true", "on", "yes"}
    lost_reason = normalize_text(payload.get("lost_reason"))
    close_value = normalize_text(payload.get("close_value"))
    close_payment = normalize_text(payload.get("close_payment"))

    if verificar_duplicidade_atividade(tenant_id, sheet_row, process_action, scheduled_date):
        return None, "Já existe uma atividade semelhante para este lead na mesma data."

    scheduled_at = _parse_datetime(None, date.fromisoformat(scheduled_date), scheduled_time).isoformat(timespec="seconds")
    empresa = normalize_text(payload.get("empresa")) or "—"
    contato = normalize_text(payload.get("contato")) or "—"

    activity_record = {
        "tenant_id": tenant_id or DEFAULT_TENANT_ID,
        "sheet_row": sheet_row,
        "lead_id": str(sheet_row),
        "company_id": str(sheet_row),
        "empresa": empresa,
        "contato": contato,
        "assigned_user_id": assigned_user,
        "created_by_user_id": user,
        "activity_type": activity_type,
        "title": process_action,
        "process_action": process_action,
        "description": description or ACTION_DESCRIPTIONS.get(process_action, ""),
        "channel": channel,
        "channel_other": channel_other,
        "stage": stage,
        "result": result,
        "result_notes": result_notes or lost_reason,
        "note": note,
        "scheduled_at": scheduled_at,
        "scheduled_date": scheduled_date,
        "scheduled_time": scheduled_time,
        "status": status,
        "priority": _priority_score(priority_label),
        "priority_label": priority_label,
        "deleted": False,
    }
    if status == "concluida":
        activity_record["completed_at"] = _now().isoformat(timespec="seconds")
    activity_record["status"] = calcular_status_atraso(activity_record)
    saved = save_activity(tenant_id, None, activity_record)
    activity_id = saved["id"]

    registrar_historico_atividade(
        tenant_id,
        sheet_row,
        user=user,
        process_action=process_action,
        stage=stage,
        activity_type=activity_type,
        channel=channel,
        assigned_user=assigned_user,
        status=activity_record["status"],
        result=result,
        note=note,
        move_stage=move_stage if move_stage_confirm else "",
    )

    suggestion = suggest_from_result(result, stage) if result else {}
    opportunity_status = suggestion.get("opportunity_status", "")
    next_activity_stage = suggestion.get("activity_stage") or move_stage or stage

    if status == "concluida":
        if move_stage_confirm and move_stage:
            movimentar_etapa_lead(tenant_id, sheet_row, from_stage=stage, to_stage=move_stage, user=user)

        if next_action and next_action_date and result not in NO_NEXT_ACTION_RESULTS:
            criar_proxima_atividade(
                tenant_id,
                {"id": activity_id, "priority": _priority_score(priority_label), "updated_by": user},
                process_action=next_action,
                channel=next_action_channel,
                assigned_user=next_action_assigned,
                scheduled_date=next_action_date,
                scheduled_time=next_action_time,
                sheet_row=sheet_row,
                empresa=empresa,
                contato=contato,
                stage=next_activity_stage,
            )
            atualizar_lead_pela_atividade(
                tenant_id,
                sheet_row,
                next_action=next_action,
                next_action_date=next_action_date,
                next_action_time=next_action_time,
                channel=next_action_channel,
                move_stage=move_stage if move_stage_confirm else "",
                user=user,
                opportunity_status=opportunity_status,
                lost_reason=lost_reason or result_notes,
                close_value=close_value,
                close_payment=close_payment,
            )
        elif result in NO_NEXT_ACTION_RESULTS:
            atualizar_lead_pela_atividade(
                tenant_id,
                sheet_row,
                next_action="Encerrar processo comercial",
                next_action_date="",
                next_action_time="",
                channel=next_action_channel,
                move_stage=move_stage if move_stage_confirm else "",
                user=user,
                opportunity_status=opportunity_status or ("Fechada perdida" if result == "Sem interesse" else "Encerrada"),
                lost_reason=lost_reason or result_notes or result,
                close_value=close_value,
                close_payment=close_payment,
            )
        else:
            atualizar_lead_pela_atividade(
                tenant_id,
                sheet_row,
                next_action=process_action,
                next_action_date=scheduled_date,
                next_action_time=scheduled_time,
                channel=channel,
                move_stage="",
                user=user,
            )
    else:
        atualizar_lead_pela_atividade(
            tenant_id,
            sheet_row,
            next_action=process_action,
            next_action_date=scheduled_date,
            next_action_time=scheduled_time,
            channel=channel,
            move_stage="",
            user=user,
        )

    return _serialize_activity({"id": activity_id, **saved}), None


def build_new_activity_modal_context(
    *,
    seller_options: list[str],
    current_user: str,
    is_admin_user: bool,
    today_iso: str,
    error: str = "",
) -> dict:
    import json

    responsibles = buscar_responsaveis_permitidos(seller_options, current_user, is_admin_user)
    default_responsible = responsibles[0] if responsibles else current_user
    return {
        "pipeline_stage_options": PIPELINE_STAGE_OPTIONS,
        "activity_type_options": ACTIVITY_TYPE_OPTIONS,
        "channel_options": CHANNEL_OPTIONS,
        "status_options": NEW_ACTIVITY_STATUS_KEYS,
        "priority_options": PRIORITY_OPTIONS,
        "result_options": [opt for opt in ACTIVITY_RESULT_OPTIONS if opt != "Selecione"],
        "next_action_options": NEXT_ACTION_OPTIONS,
        "lost_reason_options": LOST_REASON_OPTIONS,
        "responsible_options": responsibles,
        "default_responsible": default_responsible,
        "today_iso": today_iso,
        "current_user": current_user,
        "is_admin": is_admin_user,
        "activity_type_defaults": ACTIVITY_TYPE_DEFAULT_ACTION,
        "activity_type_stage_hints": ACTIVITY_TYPE_STAGE_HINT,
        "activity_type_defaults_json": json.dumps(ACTIVITY_TYPE_DEFAULT_ACTION, ensure_ascii=False),
        "activity_type_stage_hints_json": json.dumps(ACTIVITY_TYPE_STAGE_HINT, ensure_ascii=False),
        "next_action_options_json": json.dumps(NEXT_ACTION_OPTIONS, ensure_ascii=False),
        "modal_error": error,
    }


def sugerir_fluxo_por_resultado(result: str, current_stage: str = "") -> dict:
    suggestion = suggest_from_result(result, current_stage)
    from_stage = normalize_legacy_stage(current_stage) or suggestion.get("keep_stage") or current_stage
    to_stage = suggestion.get("move_stage") or suggestion.get("activity_stage") or ""
    move_text = ""
    if from_stage and to_stage and from_stage != to_stage:
        move_text = f"Deseja mover o lead de {from_stage} para {to_stage}?"
    return {**suggestion, "move_text": move_text, "from_stage": from_stage, "to_stage": to_stage}
