"""Serviço de atividades comerciais — conectado à Visão Geral."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from config.crm_options import (
    ACTION_DESCRIPTIONS,
    ACTIVITY_RESULT_OPTIONS,
    ACTIVITY_STATUS_LABELS,
    CHANNEL_CLASS,
    CHANNEL_OPTIONS,
    NO_NEXT_ACTION_RESULTS,
    OVERVIEW_ACTION_TO_PROCESS,
    PIPELINE_STAGE_OPTIONS,
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
    normalize_legacy_action,
    normalize_legacy_channel,
    normalize_legacy_result,
    normalize_legacy_stage,
    normalize_legacy_status_key,
    normalize_opportunity_status,
    resolve_display_stage,
    suggest_from_result,
    validate_activity_payload,
    validate_completion,
)
from app.services.filters import DashboardFilters, apply_dashboard_filters
from app.services.followup_service import _lead_record, buscar_leads_para_acao
from app.services.lead_actions_storage import append_interaction, save_lead_action
from app.services.legacy_core import normalize_text, safe_series

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
    allowed_next_actions = get_actions_for_stage(stage, record.get("next_action", ""))

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
        "result": result_display,
        "result_value": result,
        "result_notes": record.get("result_notes") or "",
        "proxima_acao": normalize_legacy_action(record.get("next_action")) or process_action,
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
        "next_action_description": normalize_legacy_action(next_action),
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
    next_action = normalize_legacy_action(payload.get("next_action"))
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

    suggestion = suggest_from_result(result) if result else {}
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
            lost_reason=result_notes if result in {"Sem interesse", "Lead não qualificado", "Outro"} else "",
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
    stage_options = ["Todas as etapas"] + PIPELINE_STAGE_OPTIONS
    return {
        "activities": activities,
        "table": table,
        "kpi_cards": atualizar_cards_atividades(activities, filters),
        "process_actions": PROCESS_ACTION_OPTIONS,
        "channels": CHANNEL_OPTIONS,
        "status_options": SELECTABLE_ACTIVITY_STATUS_KEYS,
        "result_options": [opt for opt in ACTIVITY_RESULT_OPTIONS if opt != "Selecione"],
        "stage_options": stage_options,
        "pipeline_stage_options": PIPELINE_STAGE_OPTIONS,
        "type_options": ["Todos os tipos"] + PROCESS_ACTION_OPTIONS,
        "channel_options": ["Todos os canais"] + CHANNEL_OPTIONS,
    }
