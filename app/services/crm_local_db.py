"""Persistência local em SQLite para atividades e ações de lead (fonte confiável além da planilha)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.services.storage_paths import get_storage_dir

DEFAULT_TENANT_ID = "default"

_lock = threading.Lock()
_initialized = False


def _normalize_text(value) -> str:
    return str(value or "").strip()


def _db_path() -> Path:
    return get_storage_dir() / "crm_local.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_crm_local_db() -> None:
    global _initialized
    with _lock:
        if _initialized:
            return
        with _connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS activities (
                    activity_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (activity_id, tenant_id)
                );
                CREATE INDEX IF NOT EXISTS idx_activities_tenant ON activities(tenant_id);

                CREATE TABLE IF NOT EXISTS lead_actions (
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    sheet_row TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, sheet_row)
                );
                """
            )
            conn.commit()
        _initialized = True


def _record_timestamp(record: dict | None) -> str:
    if not isinstance(record, dict):
        return ""
    return _normalize_text(record.get("updated_at")) or _normalize_text(record.get("created_at"))


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


def load_activities_store() -> dict:
    init_crm_local_db()
    store: dict = {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT tenant_id, activity_id, payload_json FROM activities ORDER BY updated_at"
        ).fetchall()
    for row in rows:
        tenant = _normalize_text(row["tenant_id"]) or DEFAULT_TENANT_ID
        try:
            record = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        bucket = store.setdefault(tenant, {})
        activities = bucket.setdefault("activities", {})
        activities[row["activity_id"]] = record
    return store


def save_activities_store(data: dict) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM activities")
        for tenant, bucket in data.items():
            if not isinstance(bucket, dict):
                continue
            activities = bucket.get("activities")
            if not isinstance(activities, dict):
                continue
            for activity_id, record in activities.items():
                if not isinstance(record, dict):
                    continue
                updated_at = _record_timestamp(record) or ""
                conn.execute(
                    """
                    INSERT INTO activities (activity_id, tenant_id, payload_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(activity_id),
                        _normalize_text(tenant) or DEFAULT_TENANT_ID,
                        json.dumps(record, ensure_ascii=False, default=str),
                        updated_at,
                    ),
                )
        conn.commit()


def upsert_activity(tenant_id: str | None, activity_id: str, record: dict) -> None:
    init_crm_local_db()
    tenant = _normalize_text(tenant_id) or DEFAULT_TENANT_ID
    updated_at = _record_timestamp(record) or ""
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO activities (activity_id, tenant_id, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(activity_id, tenant_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                str(activity_id),
                tenant,
                json.dumps(record, ensure_ascii=False, default=str),
                updated_at,
            ),
        )
        conn.commit()


def load_lead_actions_store() -> dict:
    init_crm_local_db()
    store: dict = {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT tenant_id, sheet_row, payload_json FROM lead_actions ORDER BY updated_at"
        ).fetchall()
    for row in rows:
        tenant = _normalize_text(row["tenant_id"]) or DEFAULT_TENANT_ID
        try:
            record = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        bucket = store.setdefault(tenant, {})
        bucket[str(row["sheet_row"])] = record
    return store


def save_lead_actions_store(data: dict) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM lead_actions")
        for tenant, bucket in data.items():
            if not isinstance(bucket, dict):
                continue
            for sheet_row, record in bucket.items():
                if not isinstance(record, dict):
                    continue
                updated_at = _record_timestamp(record) or _normalize_text(record.get("AtualizadoEm")) or ""
                conn.execute(
                    """
                    INSERT INTO lead_actions (tenant_id, sheet_row, payload_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        _normalize_text(tenant) or DEFAULT_TENANT_ID,
                        str(sheet_row),
                        json.dumps(record, ensure_ascii=False, default=str),
                        updated_at,
                    ),
                )
        conn.commit()


def upsert_lead_action(tenant_id: str | None, sheet_row: int, record: dict) -> None:
    init_crm_local_db()
    tenant = _normalize_text(tenant_id) or DEFAULT_TENANT_ID
    updated_at = _record_timestamp(record) or ""
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO lead_actions (tenant_id, sheet_row, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tenant_id, sheet_row) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                tenant,
                str(int(sheet_row)),
                json.dumps(record, ensure_ascii=False, default=str),
                updated_at,
            ),
        )
        conn.commit()
