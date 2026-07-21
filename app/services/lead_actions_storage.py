"""Persistência de próximas ações, atividades e histórico por lead (tenant + sheet_row)."""
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings
from app.services.legacy_core import normalize_text

DEFAULT_TENANT_ID = "default"
STORAGE_PATH = Path(__file__).resolve().parents[2] / "storage" / "lead_actions.json"


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None)


def _load_all() -> dict:
    if not STORAGE_PATH.exists():
        return {}
    try:
        with STORAGE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_all(data: dict) -> None:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STORAGE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, default=str)


def _tenant_bucket(tenant_id: str | None = None) -> dict:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    data = _load_all()
    bucket = data.setdefault(tenant, {})
    return bucket if isinstance(bucket, dict) else {}


def get_lead_action(tenant_id: str | None, sheet_row: int) -> dict | None:
    if not sheet_row:
        return None
    bucket = _tenant_bucket(tenant_id)
    record = bucket.get(str(sheet_row))
    return record if isinstance(record, dict) else None


def save_lead_action(tenant_id: str | None, sheet_row: int, payload: dict) -> dict:
    if not sheet_row:
        raise ValueError("sheet_row é obrigatório")

    data = _load_all()
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    bucket = data.setdefault(tenant, {})
    current = bucket.get(str(sheet_row), {})
    if not isinstance(current, dict):
        current = {}

    current.update(payload)
    current["updated_at"] = _now().isoformat(timespec="seconds")
    bucket[str(sheet_row)] = current
    data[tenant] = bucket
    _save_all(data)
    return current


def append_interaction(
    tenant_id: str | None,
    sheet_row: int,
    *,
    interaction_type: str,
    description: str,
    user: str,
    previous_stage: str = "",
    new_stage: str = "",
    note: str = "",
) -> dict:
    record = get_lead_action(tenant_id, sheet_row) or {}
    history = record.get("interactions")
    if not isinstance(history, list):
        history = []

    history.append({
        "at": _now().isoformat(timespec="seconds"),
        "type": interaction_type,
        "description": description,
        "user": user,
        "previous_stage": previous_stage,
        "new_stage": new_stage,
        "note": note,
    })
    record["interactions"] = history[-200:]
    return save_lead_action(tenant_id, sheet_row, record)


def complete_activity(
    tenant_id: str | None,
    sheet_row: int,
    *,
    result: str,
    note: str,
    user: str,
    next_action_date: date | None = None,
    next_action_time: str = "",
    next_action_type: str = "",
    next_action_description: str = "",
    move_stage: str = "",
    opportunity_status: str = "",
    lost_reason: str = "",
) -> dict:
    from app.services.crm_validation_service import (
        normalize_legacy_action,
        normalize_legacy_result,
        normalize_legacy_stage,
        normalize_opportunity_status,
    )

    result = normalize_legacy_result(result)
    next_action_description = normalize_legacy_action(next_action_description)
    move_stage = normalize_legacy_stage(move_stage)
    opportunity_status = normalize_opportunity_status(opportunity_status) if opportunity_status else ""

    record = get_lead_action(tenant_id, sheet_row) or {}
    completed = record.get("completed_activities")
    if not isinstance(completed, list):
        completed = []

    completed.append({
        "completed_at": _now().isoformat(timespec="seconds"),
        "result": result,
        "note": note,
        "user": user,
    })
    record["completed_activities"] = completed[-100:]
    record["last_completed_at"] = _now().isoformat(timespec="seconds")

    if next_action_date:
        record["next_action_date"] = next_action_date.isoformat()
        record["next_action_time"] = normalize_text(next_action_time) or "09:00"
        record["next_action_type"] = normalize_text(next_action_type) or "tarefa"
        record["next_action_description"] = normalize_text(next_action_description) or "Acompanhar lead"
        record["next_action_completed"] = False
    else:
        record.pop("next_action_date", None)
        record.pop("next_action_time", None)
        record.pop("next_action_type", None)
        record.pop("next_action_description", None)
        record["next_action_completed"] = True

    if move_stage:
        record["stage_override"] = move_stage

    if opportunity_status:
        record["opportunity_status"] = opportunity_status
        if opportunity_status in {"Fechada ganha", "Fechada perdida", "Encerrada"}:
            record["closed_at"] = _now().isoformat(timespec="seconds")
    if lost_reason:
        record["lost_reason"] = lost_reason
        record["result_notes"] = lost_reason

    record["interactions"] = record.get("interactions") if isinstance(record.get("interactions"), list) else []
    record["interactions"].append({
        "at": _now().isoformat(timespec="seconds"),
        "type": "atividade_concluida",
        "description": f"Atividade concluída: {result}",
        "user": user,
        "previous_stage": "",
        "new_stage": move_stage,
        "note": note,
    })
    record["interactions"] = record["interactions"][-200:]
    return save_lead_action(tenant_id, sheet_row, record)


def delete_lead_action(tenant_id: str | None, sheet_row: int) -> None:
    if not sheet_row:
        return

    data = _load_all()
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    bucket = data.get(tenant)
    if not isinstance(bucket, dict):
        return

    bucket.pop(str(sheet_row), None)
    data[tenant] = bucket
    _save_all(data)


def mark_next_action_completed(tenant_id: str | None, sheet_row: int, user: str) -> dict | None:
    record = get_lead_action(tenant_id, sheet_row)
    if not record:
        return None
    record["next_action_completed"] = True
    append_interaction(
        tenant_id,
        sheet_row,
        interaction_type="retorno_concluido",
        description="Retorno agendado concluído",
        user=user,
    )
    return save_lead_action(tenant_id, sheet_row, record)
