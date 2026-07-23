"""Persistência local de conversas e mensagens de Atendimentos."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.crm_local_db import _connect, init_crm_local_db
from app.services.legacy_core import normalize_text

STATUS_NOVO_LEAD = "novo_lead"
STATUS_EM_ATENDIMENTO = "em_atendimento"
STATUS_FINALIZADO = "finalizado"
STATUS_OPTIONS = [
    (STATUS_NOVO_LEAD, "Novo Lead"),
    (STATUS_EM_ATENDIMENTO, "Em Atendimento"),
    (STATUS_FINALIZADO, "Finalizado"),
]
STATUS_LABELS = dict(STATUS_OPTIONS)

AI_MODE_ON = "on"
AI_MODE_PAUSED = "paused"
AI_MODE_OFF = "off"

_lock = threading.Lock()
_event_seq = 0
_event_listeners: list = []


def _now() -> datetime:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def _notify(event: dict) -> None:
    global _event_seq
    with _lock:
        _event_seq += 1
        payload = {**event, "seq": _event_seq, "at": _now_iso()}
        listeners = list(_event_listeners)
    for queue in listeners:
        try:
            queue.put_nowait(payload)
        except Exception:
            pass


def subscribe_events():
    import queue

    q: queue.Queue = queue.Queue(maxsize=100)
    with _lock:
        _event_listeners.append(q)
    return q


def unsubscribe_events(q) -> None:
    with _lock:
        if q in _event_listeners:
            _event_listeners.remove(q)


def _row_to_conversation(row) -> dict:
    if row is None:
        return {}
    tags_raw = row["tags_json"] if "tags_json" in row.keys() else "[]"
    try:
        tags = json.loads(tags_raw) if tags_raw else []
    except json.JSONDecodeError:
        tags = []
    if not isinstance(tags, list):
        tags = []
    status = normalize_text(row["status"]) or STATUS_NOVO_LEAD
    return {
        "id": row["id"],
        "phone_e164": row["phone_e164"],
        "remote_jid": (row["remote_jid"] if "remote_jid" in row.keys() else "") or "",
        "contact_name": row["contact_name"] or "",
        "profile_pic_url": row["profile_pic_url"] or "",
        "sheet_row": int(row["sheet_row"]) if row["sheet_row"] else None,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "assignee": row["assignee"] or "",
        "ai_mode": row["ai_mode"] or AI_MODE_ON,
        "tags": [normalize_text(t) for t in tags if normalize_text(t)],
        "notes": row["notes"] or "",
        "last_message_at": row["last_message_at"] or "",
        "last_message_preview": row["last_message_preview"] or "",
        "unread_count": int(row["unread_count"] or 0),
        "typing": bool(row["typing"]),
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        "initials": _initials(row["contact_name"] or row["phone_e164"]),
    }


def _row_to_message(row) -> dict:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "direction": row["direction"],
        "type": row["msg_type"] or "text",
        "body": row["body"] or "",
        "media_url": row["media_url"] or "",
        "media_mime": row["media_mime"] or "",
        "media_filename": row["media_filename"] or "",
        "evolution_id": row["evolution_id"] or "",
        "sender": row["sender"] or "contact",
        "created_at": row["created_at"] or "",
    }


def _initials(name: str) -> str:
    parts = [p for p in normalize_text(name).split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def get_conversation(conversation_id: str) -> dict | None:
    init_crm_local_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM attendance_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return _row_to_conversation(row) if row else None


def get_conversation_by_phone(phone_e164: str) -> dict | None:
    init_crm_local_db()
    phone = normalize_text(phone_e164)
    if not phone:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM attendance_conversations WHERE phone_e164 = ? ORDER BY updated_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
    return _row_to_conversation(row) if row else None


def list_conversations(
    *,
    search: str = "",
    status: str = "",
    limit: int = 100,
) -> list[dict]:
    init_crm_local_db()
    clauses: list[str] = []
    params: list = []
    if status and status != "todos":
        clauses.append("status = ?")
        params.append(status)
    search_norm = normalize_text(search).lower()
    if search_norm:
        clauses.append(
            "(LOWER(contact_name) LIKE ? OR phone_e164 LIKE ? OR LOWER(last_message_preview) LIKE ?)"
        )
        like = f"%{search_norm}%"
        params.extend([like, f"%{search_norm}%", like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit or 100), 500)))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM attendance_conversations
            {where}
            ORDER BY
              CASE
                WHEN last_message_at IS NOT NULL AND last_message_at != '' THEN last_message_at
                ELSE updated_at
              END DESC,
              unread_count DESC,
              updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_conversation(row) for row in rows]


def upsert_conversation_by_phone(
    phone_e164: str,
    *,
    contact_name: str = "",
    profile_pic_url: str = "",
    sheet_row: int | None = None,
    status: str | None = None,
    remote_jid: str = "",
) -> dict:
    init_crm_local_db()
    phone = normalize_text(phone_e164)
    if not phone:
        raise ValueError("Telefone obrigatório")
    now = _now_iso()
    remote_jid = normalize_text(remote_jid)
    existing = get_conversation_by_phone(phone)
    if existing:
        updates: dict = {"updated_at": now}
        if contact_name and not existing.get("contact_name"):
            updates["contact_name"] = normalize_text(contact_name)
        if profile_pic_url:
            updates["profile_pic_url"] = normalize_text(profile_pic_url)
        if sheet_row and not existing.get("sheet_row"):
            updates["sheet_row"] = int(sheet_row)
        if status:
            updates["status"] = status
        if remote_jid and remote_jid != existing.get("remote_jid"):
            updates["remote_jid"] = remote_jid
        if len(updates) > 1:
            _update_conversation(existing["id"], updates)
            return get_conversation(existing["id"]) or existing
        return existing

    conversation_id = _new_id("c_")
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO attendance_conversations (
                id, phone_e164, contact_name, profile_pic_url, sheet_row, status,
                assignee, ai_mode, tags_json, notes, last_message_at, last_message_preview,
                unread_count, typing, created_at, updated_at, remote_jid
            ) VALUES (?, ?, ?, ?, ?, ?, '', ?, '[]', '', '', '', 0, 0, ?, ?, ?)
            """,
            (
                conversation_id,
                phone,
                normalize_text(contact_name),
                normalize_text(profile_pic_url),
                int(sheet_row) if sheet_row else None,
                status or STATUS_NOVO_LEAD,
                AI_MODE_ON,
                now,
                now,
                remote_jid,
            ),
        )
        conn.commit()
    conversation = get_conversation(conversation_id)
    _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
    return conversation or {}


def _update_conversation(conversation_id: str, fields: dict) -> None:
    if not fields:
        return
    allowed = {
        "contact_name", "profile_pic_url", "sheet_row", "status", "assignee", "ai_mode",
        "tags_json", "notes", "last_message_at", "last_message_preview", "unread_count",
        "typing", "updated_at", "remote_jid", "phone_e164",
    }
    cols = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        cols.append(f"{key} = ?")
        values.append(value)
    if not cols:
        return
    values.append(conversation_id)
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute(
            f"UPDATE attendance_conversations SET {', '.join(cols)} WHERE id = ?",
            values,
        )
        conn.commit()


def update_conversation(conversation_id: str, **fields) -> dict | None:
    payload = dict(fields)
    if "tags" in payload:
        tags = payload.pop("tags") or []
        payload["tags_json"] = json.dumps(
            [normalize_text(t) for t in tags if normalize_text(t)],
            ensure_ascii=False,
        )
    payload["updated_at"] = _now_iso()
    _update_conversation(conversation_id, payload)
    conversation = get_conversation(conversation_id)
    if conversation:
        _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
    return conversation


def set_typing(conversation_id: str, typing: bool) -> None:
    _update_conversation(conversation_id, {"typing": 1 if typing else 0, "updated_at": _now_iso()})
    _notify({"type": "typing", "conversation_id": conversation_id, "typing": bool(typing)})


def add_message(
    conversation_id: str,
    *,
    direction: str,
    body: str = "",
    msg_type: str = "text",
    media_url: str = "",
    media_mime: str = "",
    media_filename: str = "",
    evolution_id: str = "",
    sender: str = "contact",
    created_at: str | None = None,
    bump_unread: bool = False,
) -> dict | None:
    init_crm_local_db()
    evolution_id = normalize_text(evolution_id)
    if evolution_id:
        with _connect() as conn:
            existing = conn.execute(
                "SELECT id FROM attendance_messages WHERE evolution_id = ?",
                (evolution_id,),
            ).fetchone()
        if existing:
            return get_message(existing["id"])

    message_id = _new_id("m_")
    created = created_at or _now_iso()
    preview = normalize_text(body)
    if not preview and msg_type != "text":
        preview = f"[{msg_type}]"
    preview = preview[:180]

    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO attendance_messages (
                id, conversation_id, direction, msg_type, body, media_url, media_mime,
                media_filename, evolution_id, sender, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                conversation_id,
                direction,
                msg_type or "text",
                body or "",
                media_url or "",
                media_mime or "",
                media_filename or "",
                evolution_id,
                sender,
                created,
            ),
        )
        unread_expr = "unread_count + 1" if bump_unread else "unread_count"
        conn.execute(
            f"""
            UPDATE attendance_conversations
            SET last_message_at = ?, last_message_preview = ?, updated_at = ?,
                unread_count = {unread_expr}, typing = 0
            WHERE id = ?
            """,
            (created, preview, created, conversation_id),
        )
        conn.commit()

    message = get_message(message_id)
    _notify({
        "type": "message",
        "conversation_id": conversation_id,
        "message_id": message_id,
        "direction": direction,
    })
    return message


def get_message(message_id: str) -> dict | None:
    init_crm_local_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM attendance_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    return _row_to_message(row) if row else None


def list_messages(conversation_id: str, *, limit: int = 200) -> list[dict]:
    init_crm_local_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM attendance_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT ?
            """,
            (conversation_id, max(1, min(int(limit or 200), 1000))),
        ).fetchall()
    return [_row_to_message(row) for row in rows]


def mark_conversation_read(conversation_id: str) -> None:
    _update_conversation(conversation_id, {"unread_count": 0, "updated_at": _now_iso()})
    _notify({"type": "conversation_read", "conversation_id": conversation_id})


def count_unread() -> int:
    init_crm_local_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(unread_count), 0) AS total FROM attendance_conversations"
        ).fetchone()
    return int(row["total"] or 0) if row else 0


def get_sync_snapshot(conversation_id: str = "") -> dict:
    """Snapshot do inbox no SQLite — usado pelo poll da UI (mais confiável que SSE atrás de proxy)."""
    init_crm_local_db()
    conversation_id = normalize_text(conversation_id)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
              COALESCE((SELECT SUM(unread_count) FROM attendance_conversations), 0) AS unread,
              COALESCE((SELECT MAX(last_message_at) FROM attendance_conversations), '') AS last_msg,
              COALESCE((SELECT MAX(updated_at) FROM attendance_conversations), '') AS last_upd,
              COALESCE((SELECT COUNT(*) FROM attendance_messages), 0) AS msg_count,
              COALESCE((SELECT MAX(rowid) FROM attendance_messages), 0) AS msg_rowid
            """
        ).fetchone()
        conv_token = ""
        if conversation_id:
            crow = conn.execute(
                """
                SELECT
                  COALESCE(COUNT(*), 0) AS c,
                  COALESCE(MAX(created_at), '') AS last_at,
                  COALESCE(MAX(rowid), 0) AS last_row
                FROM attendance_messages
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            typing_row = conn.execute(
                "SELECT COALESCE(typing, 0) AS typing FROM attendance_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            typing = int(typing_row["typing"] or 0) if typing_row else 0
            conv_token = f"{crow['c']}|{crow['last_at']}|{crow['last_row']}|{typing}"

    unread = int(row["unread"] or 0)
    inbox_token = (
        f"{unread}|{row['last_msg']}|{row['last_upd']}|{row['msg_count']}|{row['msg_rowid']}"
    )
    return {
        "unread": unread,
        "inbox_token": inbox_token,
        "conversation_id": conversation_id or None,
        "conversation_token": conv_token or None,
    }
