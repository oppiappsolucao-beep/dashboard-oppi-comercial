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
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
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

                CREATE TABLE IF NOT EXISTS pending_companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    empresa TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    headers_json TEXT NOT NULL,
                    row_values_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    synced_sheet_row INTEGER,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS sheet_meta (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS attendance_conversations (
                    id TEXT PRIMARY KEY,
                    phone_e164 TEXT NOT NULL,
                    contact_name TEXT NOT NULL DEFAULT '',
                    profile_pic_url TEXT NOT NULL DEFAULT '',
                    sheet_row INTEGER,
                    status TEXT NOT NULL DEFAULT 'novo_lead',
                    assignee TEXT NOT NULL DEFAULT '',
                    ai_mode TEXT NOT NULL DEFAULT 'on',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    last_message_at TEXT NOT NULL DEFAULT '',
                    last_message_preview TEXT NOT NULL DEFAULT '',
                    unread_count INTEGER NOT NULL DEFAULT 0,
                    typing INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_attendance_phone
                    ON attendance_conversations(phone_e164);
                CREATE INDEX IF NOT EXISTS idx_attendance_last_msg
                    ON attendance_conversations(last_message_at);

                CREATE TABLE IF NOT EXISTS attendance_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    msg_type TEXT NOT NULL DEFAULT 'text',
                    body TEXT NOT NULL DEFAULT '',
                    media_url TEXT NOT NULL DEFAULT '',
                    media_mime TEXT NOT NULL DEFAULT '',
                    media_filename TEXT NOT NULL DEFAULT '',
                    evolution_id TEXT NOT NULL DEFAULT '',
                    sender TEXT NOT NULL DEFAULT 'contact',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES attendance_conversations(id)
                );
                CREATE INDEX IF NOT EXISTS idx_attendance_msg_conv
                    ON attendance_messages(conversation_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_msg_evolution
                    ON attendance_messages(evolution_id)
                    WHERE evolution_id != '';
                """
            )
            conn.commit()
        _initialized = True


def save_sheet_headers(headers: list[str]) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO sheet_meta (key, value_json) VALUES ('headers', ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            (json.dumps(headers, ensure_ascii=False),),
        )
        conn.commit()


def load_sheet_headers() -> list[str] | None:
    init_crm_local_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value_json FROM sheet_meta WHERE key = 'headers'"
        ).fetchone()
    if not row:
        return None
    try:
        headers = json.loads(row["value_json"])
    except json.JSONDecodeError:
        return None
    return headers if isinstance(headers, list) and headers else None


def enqueue_pending_company(
    *,
    empresa: str,
    payload: dict,
    headers: list[str],
    row_values: list[str],
    last_error: str = "",
) -> int:
    init_crm_local_db()
    from datetime import datetime

    created_at = datetime.now().isoformat(timespec="seconds")
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO pending_companies
                (empresa, payload_json, headers_json, row_values_json, status, created_at, last_error)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                _normalize_text(empresa) or "Sem nome",
                json.dumps(payload, ensure_ascii=False, default=str),
                json.dumps(headers, ensure_ascii=False),
                json.dumps(row_values, ensure_ascii=False, default=str),
                created_at,
                _normalize_text(last_error)[:500],
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_pending_companies(status: str = "pending") -> list[dict]:
    init_crm_local_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, empresa, payload_json, headers_json, row_values_json, status,
                   created_at, synced_sheet_row, last_error
            FROM pending_companies
            WHERE status = ?
            ORDER BY id
            """,
            (status,),
        ).fetchall()
    items = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
            headers = json.loads(row["headers_json"])
            row_values = json.loads(row["row_values_json"])
        except json.JSONDecodeError:
            continue
        items.append({
            "id": int(row["id"]),
            "empresa": row["empresa"],
            "payload": payload if isinstance(payload, dict) else {},
            "headers": headers if isinstance(headers, list) else [],
            "row_values": row_values if isinstance(row_values, list) else [],
            "status": row["status"],
            "created_at": row["created_at"],
            "synced_sheet_row": row["synced_sheet_row"],
            "last_error": row["last_error"] or "",
            "local_sheet_row": -int(row["id"]),
        })
    return items


def mark_pending_company_synced(pending_id: int, sheet_row: int) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE pending_companies
            SET status = 'synced', synced_sheet_row = ?, last_error = ''
            WHERE id = ?
            """,
            (int(sheet_row), int(pending_id)),
        )
        conn.commit()


def mark_pending_company_error(pending_id: int, error: str) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE pending_companies
            SET last_error = ?
            WHERE id = ?
            """,
            (_normalize_text(error)[:500], int(pending_id)),
        )
        conn.commit()


def get_pending_company(pending_id: int) -> dict | None:
    init_crm_local_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, empresa, payload_json, headers_json, row_values_json, status,
                   created_at, synced_sheet_row, last_error
            FROM pending_companies
            WHERE id = ?
            """,
            (int(pending_id),),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
        headers = json.loads(row["headers_json"])
        row_values = json.loads(row["row_values_json"])
    except json.JSONDecodeError:
        return None
    return {
        "id": int(row["id"]),
        "empresa": row["empresa"],
        "payload": payload if isinstance(payload, dict) else {},
        "headers": headers if isinstance(headers, list) else [],
        "row_values": row_values if isinstance(row_values, list) else [],
        "status": row["status"],
        "created_at": row["created_at"],
        "synced_sheet_row": row["synced_sheet_row"],
        "last_error": row["last_error"] or "",
        "local_sheet_row": -int(row["id"]),
    }


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
