"""Validação e normalização do processo comercial centralizado."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from config.crm_options import (
    ACTIVITY_RESULT_OPTIONS,
    ACTIVITY_STATUS_KEYS,
    CHANNEL_LABEL_TO_KEY,
    CHANNEL_OPTIONS,
    NEXT_ACTION_BY_STAGE,
    NEXT_ACTION_OPTIONS,
    NO_NEXT_ACTION_RESULTS,
    OPEN_OPPORTUNITY_STATUSES,
    PIPELINE_STAGE_OPTIONS,
    PROCESS_ACTION_OPTIONS,
    PROCESS_ACTIONS_BY_STAGE,
    RESULT_SUGGESTIONS,
    SHEET_STATUS_TO_PIPELINE_STAGE,
    VALIDATION_ERROR_MESSAGE,
)
from config.legacy_option_maps import (
    LEGACY_ACTION_MAP,
    LEGACY_CHANNEL_MAP,
    LEGACY_NEXT_ACTION_MAP,
    LEGACY_OPPORTUNITY_STATUS_MAP,
    LEGACY_RESULT_MAP,
    LEGACY_STAGE_MAP,
    LEGACY_STATUS_MAP,
)
from app.services.legacy_core import normalize_text


def _clean(value: str | None) -> str:
    return normalize_text(value)


def normalize_legacy_stage(value: str | None) -> str:
    text = _clean(value)
    if not text:
        return ""
    if text in PIPELINE_STAGE_OPTIONS:
        return text
    return LEGACY_STAGE_MAP.get(text, SHEET_STATUS_TO_PIPELINE_STAGE.get(text, ""))


def normalize_legacy_result(value: str | None) -> str:
    text = _clean(value)
    if not text or text == "Selecione":
        return ""
    if text in ACTIVITY_RESULT_OPTIONS:
        return text
    return LEGACY_RESULT_MAP.get(text, "")


def normalize_legacy_action(value: str | None) -> str:
    text = _clean(value)
    if not text:
        return ""
    if text in PROCESS_ACTION_OPTIONS:
        return text
    return LEGACY_ACTION_MAP.get(text, "")


def normalize_legacy_next_action(value: str | None) -> str:
    text = _clean(value)
    if not text:
        return ""
    if text in NEXT_ACTION_OPTIONS:
        return text
    mapped = LEGACY_NEXT_ACTION_MAP.get(text, "")
    if mapped in NEXT_ACTION_OPTIONS:
        return mapped
    process_mapped = normalize_legacy_action(text)
    if process_mapped in NEXT_ACTION_OPTIONS:
        return process_mapped
    return LEGACY_NEXT_ACTION_MAP.get(process_mapped, "")


def normalize_legacy_channel(value: str | None) -> str:
    text = _clean(value)
    if not text:
        return ""
    if text in CHANNEL_OPTIONS:
        return text
    mapped = LEGACY_CHANNEL_MAP.get(text)
    if mapped:
        return mapped
    return CHANNEL_OPTIONS[0]


def normalize_legacy_status_key(value: str | None) -> str:
    text = _clean(value).lower()
    if not text:
        return "pendente"
    if text in ACTIVITY_STATUS_KEYS.values():
        return text
    label = LEGACY_STATUS_MAP.get(text, "")
    return ACTIVITY_STATUS_KEYS.get(label, text if text in ACTIVITY_STATUS_KEYS.values() else "pendente")


def normalize_opportunity_status(value: str | None) -> str:
    text = _clean(value)
    if not text:
        return "Aberta"
    if text in {"Aberta", "Fechada ganha", "Fechada perdida", "Encerrada"}:
        return text
    return LEGACY_OPPORTUNITY_STATUS_MAP.get(text, "Aberta")


def resolve_pipeline_stage(grouped_status: str, stored: dict | None = None) -> str:
    stored = stored or {}
    override = normalize_legacy_stage(stored.get("stage_override"))
    if override:
        return override

    opportunity_status = normalize_opportunity_status(stored.get("opportunity_status"))
    if opportunity_status == "Fechada ganha":
        return "Fechado"

    mapped = normalize_legacy_stage(grouped_status)
    if mapped:
        return mapped

    legacy = LEGACY_STAGE_MAP.get(_clean(grouped_status), "")
    if legacy:
        return legacy

    return "Contato" if grouped_status else "Novo Lead"


def resolve_display_stage(raw_stage: str, stored: dict | None = None) -> tuple[str, bool]:
    """Retorna (etapa_oficial, is_legacy_sem_mapeamento)."""
    stored = stored or {}
    text = _clean(raw_stage)
    if not text:
        return resolve_pipeline_stage("", stored), False

    normalized = normalize_legacy_stage(text)
    if normalized:
        return normalized, normalized != text and text not in PIPELINE_STAGE_OPTIONS

    if text in PIPELINE_STAGE_OPTIONS:
        return text, False

    return text, True


def stage_for_next_action(next_action: str) -> str:
    normalized = normalize_legacy_next_action(next_action)
    if not normalized:
        return ""
    for stage, action in NEXT_ACTION_BY_STAGE.items():
        if action == normalized:
            return stage
    return ""


def get_actions_for_stage(stage: str, include_current: str = "") -> list[str]:
    normalized = normalize_legacy_stage(stage) or "Novo Lead"
    actions = list(PROCESS_ACTIONS_BY_STAGE.get(normalized, PROCESS_ACTION_OPTIONS))
    current = normalize_legacy_action(include_current)
    if current and current not in actions:
        actions.insert(0, current)
    return actions


def get_next_action_options(include_current: str = "") -> list[str]:
    options = list(NEXT_ACTION_OPTIONS)
    current = normalize_legacy_next_action(include_current)
    if current and current not in options:
        options.insert(0, current)
    raw = _clean(include_current)
    if raw and raw not in options and raw != current:
        options.insert(0, raw)
    return options


def is_open_opportunity(stored: dict | None, grouped_status: str = "") -> bool:
    stored = stored or {}
    opportunity_status = normalize_opportunity_status(stored.get("opportunity_status"))
    if opportunity_status not in OPEN_OPPORTUNITY_STATUSES:
        return False
    grouped = _clean(grouped_status)
    if grouped in {"Fechado", "Sem interesse"} and opportunity_status == "Aberta":
        return grouped != "Fechado"
    return True


def suggest_from_result(result: str, current_stage: str = "") -> dict:
    normalized = normalize_legacy_result(result)
    item = RESULT_SUGGESTIONS.get(normalized, {
        "stage": "",
        "next_action": "Retornar contato",
        "days": 1,
        "channel": "WhatsApp",
    })
    today = date.today()
    suggested_date = today + timedelta(days=int(item.get("days", 1)))
    move_stage = item.get("stage") or item.get("keep_stage", "")
    next_action = item.get("next_action", "Retornar contato")

    if item.get("advance_stage"):
        from_stage = normalize_legacy_stage(current_stage)
        if from_stage in PIPELINE_STAGE_OPTIONS:
            stage_index = PIPELINE_STAGE_OPTIONS.index(from_stage)
            if stage_index < len(PIPELINE_STAGE_OPTIONS) - 1:
                move_stage = PIPELINE_STAGE_OPTIONS[stage_index + 1]
                next_action = NEXT_ACTION_BY_STAGE.get(move_stage, next_action)

    return {
        "next_action": next_action,
        "next_action_date": suggested_date.isoformat(),
        "next_action_time": "10:00",
        "channel": item.get("channel", "WhatsApp"),
        "move_stage": move_stage,
        "require_schedule": bool(item.get("require_schedule")),
        "require_reason": bool(item.get("require_reason")),
        "require_close_fields": bool(item.get("require_close_fields")),
        "opportunity_status": item.get("opportunity_status", ""),
        "activity_stage": item.get("stage") or move_stage,
        "advance_stage": bool(item.get("advance_stage")),
    }


def validate_pipeline_stage(stage: str) -> bool:
    return normalize_legacy_stage(stage) in PIPELINE_STAGE_OPTIONS


def validate_activity_result(result: str) -> bool:
    if not result or result == "Selecione":
        return True
    return normalize_legacy_result(result) in ACTIVITY_RESULT_OPTIONS


def validate_channel(channel: str) -> bool:
    return normalize_legacy_channel(channel) in CHANNEL_OPTIONS


def validate_next_action(action: str) -> bool:
    return normalize_legacy_next_action(action) in NEXT_ACTION_OPTIONS


def validate_process_action(action: str, stage: str = "") -> bool:
    normalized = normalize_legacy_action(action)
    if normalized not in PROCESS_ACTION_OPTIONS:
        return False
    if not stage:
        return True
    stage_normalized = normalize_legacy_stage(stage) or "Novo Lead"
    allowed = PROCESS_ACTIONS_BY_STAGE.get(stage_normalized, PROCESS_ACTION_OPTIONS)
    return normalized in allowed


def validate_status_key(status: str, allow_manual_overdue: bool = False) -> bool:
    key = normalize_legacy_status_key(status)
    if key == "atrasada" and not allow_manual_overdue:
        return False
    return key in ACTIVITY_STATUS_KEYS.values()


def validate_activity_payload(payload: dict) -> str | None:
    stage = _clean(payload.get("stage")) or _clean(payload.get("move_stage"))
    if stage and not validate_pipeline_stage(stage):
        return VALIDATION_ERROR_MESSAGE

    move_stage = _clean(payload.get("move_stage"))
    if move_stage and not validate_pipeline_stage(move_stage):
        return VALIDATION_ERROR_MESSAGE

    status = _clean(payload.get("status"))
    if status and not validate_status_key(status):
        return VALIDATION_ERROR_MESSAGE

    result = _clean(payload.get("result"))
    if result and result != "Selecione" and not validate_activity_result(result):
        return VALIDATION_ERROR_MESSAGE

    channel = _clean(payload.get("channel"))
    if channel and not validate_channel(channel):
        return VALIDATION_ERROR_MESSAGE

    next_channel = _clean(payload.get("next_action_channel"))
    if next_channel and not validate_channel(next_channel):
        return VALIDATION_ERROR_MESSAGE

    next_action = _clean(payload.get("next_action"))
    current_stage = normalize_legacy_stage(_clean(payload.get("stage"))) or normalize_legacy_stage(move_stage)
    if next_action:
        normalized_next = normalize_legacy_next_action(next_action)
        if normalized_next not in NEXT_ACTION_OPTIONS:
            return VALIDATION_ERROR_MESSAGE

    process_action = _clean(payload.get("process_action"))
    if process_action:
        normalized_process = normalize_legacy_action(process_action)
        if normalized_process not in PROCESS_ACTION_OPTIONS:
            return VALIDATION_ERROR_MESSAGE

    return None


def validate_completion(status: str, result: str, next_action: str, current_stage: str = "") -> str | None:
    status_key = normalize_legacy_status_key(status)
    if status_key != "concluida":
        return None

    normalized_result = normalize_legacy_result(result)
    if not normalized_result or normalized_result == "Selecione":
        return "Selecione o resultado para concluir a atividade."

    if normalized_result in NO_NEXT_ACTION_RESULTS:
        return None

    if normalize_legacy_stage(current_stage) == "Fechado":
        return None

    if not normalize_legacy_next_action(next_action):
        return "Defina a próxima ação para concluir esta atividade."

    return None


def calcular_status_atraso(record: dict, now: datetime | None = None) -> str:
    now = now or datetime.now()
    status = normalize_legacy_status_key(record.get("status"))
    if status in {"concluida", "cancelada"}:
        return status

    scheduled_at = record.get("scheduled_at")
    if not scheduled_at:
        return status

    try:
        dt = datetime.fromisoformat(str(scheduled_at).replace("Z", ""))
    except ValueError:
        return status

    if dt < now and status in {"pendente", "em_andamento", "reagendada"}:
        return "atrasada"
    return status


def channel_label_to_key(channel: str) -> str:
    normalized = normalize_legacy_channel(channel)
    return CHANNEL_LABEL_TO_KEY.get(normalized, "whatsapp")


def format_legacy_value_label(value: str, mapped: str) -> str:
    if not value:
        return ""
    if mapped and mapped != value:
        return mapped
    if value not in PIPELINE_STAGE_OPTIONS:
        return f"{value} (Valor legado)"
    return value
