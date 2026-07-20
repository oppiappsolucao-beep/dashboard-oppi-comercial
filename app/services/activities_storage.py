"""Persistência de atividades comerciais por tenant."""
import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings
from app.services.legacy_core import normalize_text

DEFAULT_TENANT_ID = "default"
STORAGE_PATH = Path(__file__).resolve().parents[2] / "storage" / "activities.json"


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


def _tenant_activities(tenant_id: str | None = None) -> dict:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    data = _load_all()
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
    data = _load_all()
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
    _save_all(data)
    return {"id": activity_id, **current}


def soft_delete_activity(tenant_id: str | None, activity_id: str) -> bool:
    record = get_activity(tenant_id, activity_id)
    if not record:
        return False
    save_activity(tenant_id, activity_id, {"deleted": True, "deleted_at": _now().isoformat(timespec="seconds")})
    return True


def activity_exists(tenant_id: str | None, activity_id: str) -> bool:
    return get_activity(tenant_id, activity_id) is not None
