"""Persistência de próximas ações, atividades e histórico por lead — arquivo local + aba LeadAcoes."""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config.settings import settings as runtime_settings

from app.config import settings

from app.services.legacy_core import normalize_text
from app.services.sheet_crm_storage import CRM_STORAGE_TABS, get_worksheet, header_indexes
from app.services.storage_paths import get_storage_dir

DEFAULT_TENANT_ID = "default"
LEAD_ACTIONS_WORKSHEET = "LeadAcoes"
LEAD_ACTIONS_HEADERS = CRM_STORAGE_TABS[LEAD_ACTIONS_WORKSHEET]

_lock = threading.Lock()
_cache: dict | None = None


def _now() -> datetime:
    return datetime.now(ZoneInfo(runtime_settings.timezone)).replace(tzinfo=None)


def _storage_path():
    return get_storage_dir() / "lead_actions.json"


def _empty_store() -> dict:
    return {}


def _load_from_file() -> dict:
    path = _storage_path()
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    return data if isinstance(data, dict) else _empty_store()


def _save_to_file(data: dict) -> None:
    path = _storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _lead_action_row(tenant: str, sheet_row: int, record: dict) -> list[str]:
    return [
        tenant,
        str(int(sheet_row)),
        normalize_text(record.get("updated_at")) or _now().isoformat(timespec="seconds"),
        json.dumps(record, ensure_ascii=False, default=str),
    ]


def _parse_lead_action_row(row: list[str], indexes: dict[str, int]) -> tuple[str, str, dict] | None:
    if len(row) <= max(indexes.values()):
        return None
    tenant = normalize_text(row[indexes["TenantId"]]) or DEFAULT_TENANT_ID
    sheet_row = normalize_text(row[indexes["SheetRow"]])
    if not sheet_row:
        return None
    payload_text = row[indexes["Dados"]]
    if not payload_text:
        return None
    try:
        record = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    record.setdefault("updated_at", normalize_text(row[indexes["AtualizadoEm"]]))
    return tenant, sheet_row, record


def _load_from_sheet(force_refresh: bool = False) -> dict | None:
    if not settings.sheets_configured:
        return None
    worksheet = get_worksheet(LEAD_ACTIONS_WORKSHEET)
    if worksheet is None:
        return None

    from app.services.sheet_read_cache import get_cached_worksheet_values

    try:
        rows = get_cached_worksheet_values(
            LEAD_ACTIONS_WORKSHEET,
            worksheet.get_all_values,
            force_refresh=force_refresh,
        )
    except Exception:
        return None
    if rows is None:
        return None
    if len(rows) < 2:
        return _empty_store()

    indexes = header_indexes(rows[0], LEAD_ACTIONS_HEADERS)
    if indexes is None:
        return None

    store = _empty_store()
    for row in rows[1:]:
        parsed = _parse_lead_action_row(row, indexes)
        if not parsed:
            continue
        tenant, sheet_row, record = parsed
        bucket = store.setdefault(tenant, {})
        bucket[str(sheet_row)] = record
    return store


def _save_to_sheet(data: dict) -> bool:
    if not settings.sheets_configured:
        return False
    worksheet = get_worksheet(LEAD_ACTIONS_WORKSHEET)
    if worksheet is None:
        return False
    try:
        rows = [LEAD_ACTIONS_HEADERS]
        for tenant in sorted(data.keys(), key=str):
            bucket = data.get(tenant)
            if not isinstance(bucket, dict):
                continue
            for sheet_row in sorted(bucket.keys(), key=lambda value: int(value) if str(value).isdigit() else 0):
                record = bucket.get(sheet_row)
                if isinstance(record, dict):
                    rows.append(_lead_action_row(tenant, int(sheet_row), record))
        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")
        return True
    except Exception:
        return False


def _merge_stores(file_store: dict, sheet_store: dict | None) -> dict:
    if sheet_store is None:
        return file_store

    merged = _empty_store()
    tenants = set(file_store.keys()) | set(sheet_store.keys())
    for tenant in tenants:
        file_bucket = file_store.get(tenant, {})
        sheet_bucket = sheet_store.get(tenant, {})
        if not isinstance(file_bucket, dict):
            file_bucket = {}
        if not isinstance(sheet_bucket, dict):
            sheet_bucket = {}
        merged[tenant] = {**file_bucket, **sheet_bucket}
    return merged


def _persist_store(data: dict) -> None:
    _save_to_file(data)
    _save_to_sheet(data)


def _load_store(force_refresh: bool = False) -> dict:
    global _cache
    with _lock:
        if not force_refresh and _cache is not None:
            return json.loads(json.dumps(_cache, default=str))

        file_store = _load_from_file()
        sheet_store = _load_from_sheet(force_refresh=force_refresh)
        merged = _merge_stores(file_store, sheet_store)

        if merged != file_store:
            _save_to_file(merged)
        if sheet_store is not None and (sheet_store == _empty_store() or not sheet_store) and merged:
            _save_to_sheet(merged)
        elif sheet_store is not None and merged != sheet_store:
            _save_to_sheet(merged)

        _cache = merged
        return json.loads(json.dumps(merged, default=str))


def invalidate_lead_actions_cache() -> None:
    global _cache
    from app.services.sheet_read_cache import invalidate_worksheet_cache

    invalidate_worksheet_cache(LEAD_ACTIONS_WORKSHEET)
    with _lock:
        _cache = None


def reload_lead_actions_store(force_refresh: bool = True) -> None:
    _load_store(force_refresh=force_refresh)


def _tenant_bucket(tenant_id: str | None = None) -> dict:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    data = _load_store()
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

    data = _load_store(force_refresh=True)
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    bucket = data.setdefault(tenant, {})
    current = bucket.get(str(sheet_row), {})
    if not isinstance(current, dict):
        current = {}

    current.update(payload)
    current["updated_at"] = _now().isoformat(timespec="seconds")
    bucket[str(sheet_row)] = current
    data[tenant] = bucket
    _persist_store(data)
    with _lock:
        global _cache
        _cache = data
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

    data = _load_store(force_refresh=True)
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    bucket = data.get(tenant)
    if not isinstance(bucket, dict):
        return

    bucket.pop(str(sheet_row), None)
    data[tenant] = bucket
    _persist_store(data)
    with _lock:
        global _cache
        _cache = data


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
