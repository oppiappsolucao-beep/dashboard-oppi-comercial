"""Persistência de atividades comerciais por tenant — arquivo local + aba Atividades."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import settings as runtime_settings

from app.config import settings

from app.services.legacy_core import normalize_text
from app.services.sheet_crm_storage import CRM_STORAGE_TABS, get_worksheet, header_indexes
from app.services.storage_paths import get_storage_dir

DEFAULT_TENANT_ID = "default"
ACTIVITIES_WORKSHEET = "Atividades"
ACTIVITIES_HEADERS = CRM_STORAGE_TABS[ACTIVITIES_WORKSHEET]

_lock = threading.Lock()
_cache: dict | None = None


def _now() -> datetime:
    return datetime.now(ZoneInfo(runtime_settings.timezone)).replace(tzinfo=None)


def _storage_path():
    return get_storage_dir() / "activities.json"


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


def _activity_row(activity_id: str, record: dict) -> list[str]:
    return [
        activity_id,
        normalize_text(record.get("tenant_id")) or DEFAULT_TENANT_ID,
        str(int(record.get("sheet_row") or 0) or ""),
        normalize_text(record.get("empresa")),
        normalize_text(record.get("status")),
        normalize_text(record.get("stage")),
        normalize_text(record.get("process_action") or record.get("title")),
        normalize_text(record.get("assigned_user_id")),
        normalize_text(record.get("scheduled_at")),
        "1" if record.get("deleted") else "0",
        json.dumps(record, ensure_ascii=False, default=str),
    ]


def _parse_activity_row(row: list[str], indexes: dict[str, int]) -> tuple[str, dict] | None:
    if len(row) <= max(indexes.values()):
        return None
    activity_id = normalize_text(row[indexes["Id"]])
    payload_text = row[indexes["Dados"]]
    if not activity_id:
        return None
    record: dict
    if payload_text:
        try:
            parsed = json.loads(payload_text)
            record = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            record = {}
    else:
        record = {}
    record.setdefault("tenant_id", normalize_text(row[indexes["TenantId"]]) or DEFAULT_TENANT_ID)
    record.setdefault("sheet_row", int(normalize_text(row[indexes["SheetRow"]]) or 0) or 0)
    record.setdefault("empresa", normalize_text(row[indexes["Empresa"]]))
    record.setdefault("status", normalize_text(row[indexes["Status"]]))
    record.setdefault("stage", normalize_text(row[indexes["Etapa"]]))
    record.setdefault("process_action", normalize_text(row[indexes["Acao"]]))
    record.setdefault("assigned_user_id", normalize_text(row[indexes["Responsavel"]]))
    record.setdefault("scheduled_at", normalize_text(row[indexes["AgendadoEm"]]))
    record["deleted"] = normalize_text(row[indexes["Excluido"]]).lower() in {"1", "true", "sim", "yes"}
    return activity_id, record


def _load_from_sheet(force_refresh: bool = False) -> dict | None:
    if not settings.sheets_configured:
        return None
    worksheet = get_worksheet(ACTIVITIES_WORKSHEET)
    if worksheet is None:
        return None

    from app.services.sheet_read_cache import get_cached_worksheet_values

    try:
        rows = get_cached_worksheet_values(
            ACTIVITIES_WORKSHEET,
            worksheet.get_all_values,
            force_refresh=force_refresh,
        )
    except Exception:
        return None
    if rows is None:
        return None
    if len(rows) < 2:
        return _empty_store()

    indexes = header_indexes(rows[0], ACTIVITIES_HEADERS)
    if indexes is None:
        return None

    store = _empty_store()
    for row in rows[1:]:
        parsed = _parse_activity_row(row, indexes)
        if not parsed:
            continue
        activity_id, record = parsed
        tenant = normalize_text(record.get("tenant_id")) or DEFAULT_TENANT_ID
        bucket = store.setdefault(tenant, {})
        activities = bucket.setdefault("activities", {})
        activities[activity_id] = record
    return store


def _save_to_sheet(data: dict) -> bool:
    if not settings.sheets_configured:
        return False
    worksheet = get_worksheet(ACTIVITIES_WORKSHEET)
    if worksheet is None:
        return False
    try:
        rows = [ACTIVITIES_HEADERS]
        for tenant, bucket in sorted(data.items(), key=lambda item: str(item[0])):
            if not isinstance(bucket, dict):
                continue
            activities = bucket.get("activities")
            if not isinstance(activities, dict):
                continue
            for activity_id in sorted(activities.keys()):
                record = activities.get(activity_id)
                if isinstance(record, dict):
                    rows.append(_activity_row(activity_id, record))
        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")
        return True
    except Exception:
        return False


def _record_timestamp(record: dict | None) -> str:
    if not isinstance(record, dict):
        return ""
    return normalize_text(record.get("updated_at")) or normalize_text(record.get("created_at"))


def _pick_newer_record(left: dict | None, right: dict | None) -> dict | None:
    if not isinstance(left, dict) or not left:
        return right if isinstance(right, dict) else None
    if not isinstance(right, dict) or not right:
        return left

    left_at = _record_timestamp(left)
    right_at = _record_timestamp(right)
    if left_at and right_at:
        return left if left_at >= right_at else right
    if left_at:
        return left
    if right_at:
        return right
    return left


def _merge_stores(left: dict, right: dict | None) -> dict:
    if not right:
        return left
    if not left:
        return right

    merged = _empty_store()
    tenants = set(left.keys()) | set(right.keys())
    for tenant in tenants:
        left_bucket = left.get(tenant, {})
        right_bucket = right.get(tenant, {})
        if not isinstance(left_bucket, dict):
            left_bucket = {}
        if not isinstance(right_bucket, dict):
            right_bucket = {}
        left_activities = left_bucket.get("activities", {}) if isinstance(left_bucket, dict) else {}
        right_activities = right_bucket.get("activities", {}) if isinstance(right_bucket, dict) else {}
        if not isinstance(left_activities, dict):
            left_activities = {}
        if not isinstance(right_activities, dict):
            right_activities = {}
        keys = set(left_activities.keys()) | set(right_activities.keys())
        combined = {}
        for key in keys:
            picked = _pick_newer_record(left_activities.get(key), right_activities.get(key))
            if isinstance(picked, dict):
                combined[key] = picked
        merged[tenant] = {"activities": combined}
    return merged


def _persist_store(data: dict) -> None:
    from app.services.crm_local_db import save_activities_store
    from app.services.sheet_read_cache import invalidate_worksheet_cache

    _save_to_file(data)
    try:
        save_activities_store(data)
    except Exception:
        pass
    if _save_to_sheet(data):
        invalidate_worksheet_cache(ACTIVITIES_WORKSHEET)


def _load_store(force_refresh: bool = False) -> dict:
    global _cache
    with _lock:
        if not force_refresh and _cache is not None:
            return json.loads(json.dumps(_cache, default=str))

        from app.services.crm_local_db import load_activities_store

        file_store = _load_from_file()
        sheet_store = _load_from_sheet(force_refresh=force_refresh)
        db_store = load_activities_store()

        merged = _merge_stores(file_store, sheet_store)
        merged = _merge_stores(merged, db_store)

        if merged != file_store:
            _save_to_file(merged)
        if sheet_store is not None and not sheet_store and merged:
            _save_to_sheet(merged)

        _cache = merged
        return json.loads(json.dumps(merged, default=str))


def invalidate_activities_cache() -> None:
    global _cache
    from app.services.sheet_read_cache import invalidate_worksheet_cache

    invalidate_worksheet_cache(ACTIVITIES_WORKSHEET)
    with _lock:
        _cache = None


def reload_activities_store(force_refresh: bool = True) -> None:
    _load_store(force_refresh=force_refresh)


def _tenant_activities(tenant_id: str | None = None) -> dict:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    data = _load_store()
    bucket = data.setdefault(tenant, {})
    activities = bucket.setdefault("activities", {})
    return activities if isinstance(activities, dict) else {}


def list_activities(tenant_id: str | None = None, include_deleted: bool = False) -> list[dict]:
    activities = _tenant_activities(tenant_id)
    rows = []
    for activity_id, record in activities.items():
        if not isinstance(record, dict):
            continue
        if record.get("deleted") and not include_deleted:
            continue
        item = dict(record)
        item["id"] = activity_id
        rows.append(item)
    return rows


def get_activity(tenant_id: str | None, activity_id: str) -> dict | None:
    if not activity_id:
        return None
    record = _tenant_activities(tenant_id).get(activity_id)
    if not isinstance(record, dict) or record.get("deleted"):
        return None
    return {"id": activity_id, **record}


def save_activity(tenant_id: str | None, activity_id: str | None, payload: dict) -> dict:
    global _cache
    from app.services.crm_local_db import upsert_activity

    with _lock:
        cached = json.loads(json.dumps(_cache, default=str)) if _cache is not None else None
    data = cached if cached is not None else _load_store(force_refresh=False)

    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    bucket = data.setdefault(tenant, {})
    activities = bucket.setdefault("activities", {})
    activity_id = activity_id or payload.get("id") or f"act_{uuid.uuid4().hex[:12]}"
    current = activities.get(activity_id, {})
    if not isinstance(current, dict):
        current = {}
    current.update(payload)
    current["updated_at"] = _now().isoformat(timespec="seconds")
    if "created_at" not in current:
        current["created_at"] = current["updated_at"]
    activities[activity_id] = current
    bucket["activities"] = activities
    data[tenant] = bucket
    _persist_store(data)
    try:
        upsert_activity(tenant, activity_id, current)
    except Exception:
        pass
    stage_changed = any(key in payload for key in ("stage", "move_stage", "stage_entered_at"))
    sheet_row = int(current.get("sheet_row") or 0)
    stage = normalize_text(current.get("stage") or current.get("move_stage"))
    if sheet_row and stage and stage_changed:
        try:
            from app.services.legacy_core import sync_pipeline_stage_to_sheet

            sync_pipeline_stage_to_sheet(sheet_row, stage)
        except Exception:
            pass
    with _lock:
        _cache = data
    return {"id": activity_id, **current}


def soft_delete_activity(tenant_id: str | None, activity_id: str) -> bool:
    record = get_activity(tenant_id, activity_id)
    if not record:
        return False
    save_activity(tenant_id, activity_id, {"deleted": True, "deleted_at": _now().isoformat(timespec="seconds")})
    return True


def activity_exists(tenant_id: str | None, activity_id: str) -> bool:
    return get_activity(tenant_id, activity_id) is not None
