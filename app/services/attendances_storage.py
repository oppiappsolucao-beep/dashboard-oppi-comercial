"""Persistência de conversas/mensagens de Atendimentos em DATABASE_URL."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.services.legacy_core import normalize_text
from database.connection import SessionLocal
from database.models import AttendanceConversation, AttendanceMessage

logger = logging.getLogger(__name__)

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
    try:
        return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


@contextmanager
def _session(*, commit: bool = True):
    db = SessionLocal()
    try:
        yield db
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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


def _initials(name: str) -> str:
    parts = [p for p in normalize_text(name).split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _conversation_to_dict(row: AttendanceConversation | None) -> dict:
    if row is None:
        return {}
    try:
        tags = json.loads(row.tags_json or "[]")
    except json.JSONDecodeError:
        tags = []
    if not isinstance(tags, list):
        tags = []
    status = normalize_text(row.status) or STATUS_NOVO_LEAD
    return {
        "id": row.id,
        "phone_e164": row.phone_e164,
        "remote_jid": row.remote_jid or "",
        "contact_name": row.contact_name or "",
        "profile_pic_url": row.profile_pic_url or "",
        "sheet_row": int(row.sheet_row) if row.sheet_row else None,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "assignee": row.assignee or "",
        "ai_mode": row.ai_mode or AI_MODE_ON,
        "tags": [normalize_text(t) for t in tags if normalize_text(t)],
        "notes": row.notes or "",
        "last_message_at": row.last_message_at or "",
        "last_message_preview": row.last_message_preview or "",
        "unread_count": int(row.unread_count or 0),
        "typing": bool(row.typing),
        "sector_id": int(row.sector_id) if getattr(row, "sector_id", None) else None,
        "sector_name": getattr(row, "sector_name", None) or "",
        "created_at": row.created_at or "",
        "updated_at": row.updated_at or "",
        "initials": _initials(row.contact_name or row.phone_e164),
    }


def _message_to_dict(row: AttendanceMessage | None) -> dict:
    if row is None:
        return {}
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "direction": row.direction,
        "type": row.msg_type or "text",
        "body": row.body or "",
        "media_url": row.media_url or "",
        "media_mime": row.media_mime or "",
        "media_filename": row.media_filename or "",
        "evolution_id": row.evolution_id or "",
        "sender": row.sender or "contact",
        "created_at": row.created_at or "",
    }


def get_conversation(conversation_id: str) -> dict | None:
    with _session(commit=False) as db:
        row = db.get(AttendanceConversation, conversation_id)
        return _conversation_to_dict(row) if row else None


def get_conversation_by_phone(phone_e164: str) -> dict | None:
    phone = normalize_text(phone_e164)
    if not phone:
        return None
    with _session(commit=False) as db:
        row = (
            db.query(AttendanceConversation)
            .filter(AttendanceConversation.phone_e164 == phone)
            .order_by(AttendanceConversation.updated_at.desc())
            .first()
        )
        return _conversation_to_dict(row) if row else None


def list_conversations(
    *,
    search: str = "",
    status: str = "",
    limit: int = 100,
) -> list[dict]:
    with _session(commit=False) as db:
        q = db.query(AttendanceConversation)
        if status and status != "todos":
            q = q.filter(AttendanceConversation.status == status)
        search_norm = normalize_text(search).lower()
        if search_norm:
            like = f"%{search_norm}%"
            q = q.filter(
                or_(
                    func.lower(AttendanceConversation.contact_name).like(like),
                    AttendanceConversation.phone_e164.like(f"%{search_norm}%"),
                    func.lower(AttendanceConversation.last_message_preview).like(like),
                )
            )
        # Ordena por last_message_at quando preenchido; senão updated_at
        rows = (
            q.order_by(
                func.coalesce(
                    func.nullif(AttendanceConversation.last_message_at, ""),
                    AttendanceConversation.updated_at,
                ).desc(),
                AttendanceConversation.unread_count.desc(),
                AttendanceConversation.updated_at.desc(),
            )
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        return [_conversation_to_dict(row) for row in rows]


def upsert_conversation_by_phone(
    phone_e164: str,
    *,
    contact_name: str = "",
    profile_pic_url: str = "",
    sheet_row: int | None = None,
    status: str | None = None,
    remote_jid: str = "",
) -> dict:
    phone = normalize_text(phone_e164)
    if not phone:
        raise ValueError("Telefone obrigatório")
    now = _now_iso()
    remote_jid = normalize_text(remote_jid)

    with _lock, _session() as db:
        existing = (
            db.query(AttendanceConversation)
            .filter(AttendanceConversation.phone_e164 == phone)
            .order_by(AttendanceConversation.updated_at.desc())
            .first()
        )
        if existing:
            changed = False
            if contact_name and not (existing.contact_name or "").strip():
                existing.contact_name = normalize_text(contact_name)
                changed = True
            if profile_pic_url:
                existing.profile_pic_url = normalize_text(profile_pic_url)
                changed = True
            if sheet_row and not existing.sheet_row:
                existing.sheet_row = int(sheet_row)
                changed = True
            if status:
                existing.status = status
                changed = True
            if remote_jid and remote_jid != (existing.remote_jid or ""):
                existing.remote_jid = remote_jid
                changed = True
            if changed:
                existing.updated_at = now
            conversation_id = existing.id
            result = _conversation_to_dict(existing)
            if changed:
                _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
            return result

        conversation_id = _new_id("c_")
        row = AttendanceConversation(
            id=conversation_id,
            phone_e164=phone,
            contact_name=normalize_text(contact_name),
            profile_pic_url=normalize_text(profile_pic_url),
            sheet_row=int(sheet_row) if sheet_row else None,
            status=status or STATUS_NOVO_LEAD,
            assignee="",
            ai_mode=AI_MODE_ON,
            tags_json="[]",
            notes="",
            last_message_at="",
            last_message_preview="",
            unread_count=0,
            typing=False,
            remote_jid=remote_jid,
            sector_id=None,
            sector_name="",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        result = _conversation_to_dict(row)

    _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
    return result or {}


def _update_conversation(conversation_id: str, fields: dict) -> None:
    if not fields:
        return
    allowed = {
        "contact_name",
        "profile_pic_url",
        "sheet_row",
        "status",
        "assignee",
        "ai_mode",
        "tags_json",
        "notes",
        "last_message_at",
        "last_message_preview",
        "unread_count",
        "typing",
        "updated_at",
        "remote_jid",
        "phone_e164",
        "sector_id",
        "sector_name",
    }
    with _lock, _session() as db:
        row = db.get(AttendanceConversation, conversation_id)
        if not row:
            return
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "typing":
                setattr(row, key, bool(value))
            elif key == "sector_id":
                try:
                    setattr(row, key, int(value) if value not in (None, "") else None)
                except (TypeError, ValueError):
                    setattr(row, key, None)
            else:
                setattr(row, key, value)


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
    _update_conversation(
        conversation_id, {"typing": bool(typing), "updated_at": _now_iso()}
    )
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
    evolution_id = normalize_text(evolution_id)
    if evolution_id:
        with _session(commit=False) as db:
            existing = (
                db.query(AttendanceMessage)
                .filter(AttendanceMessage.evolution_id == evolution_id)
                .first()
            )
            if existing:
                return _message_to_dict(existing)

    message_id = _new_id("m_")
    created = created_at or _now_iso()
    preview = normalize_text(body)
    if not preview and msg_type != "text":
        preview = f"[{msg_type}]"
    preview = preview[:180]

    with _lock, _session() as db:
        if evolution_id:
            dup = (
                db.query(AttendanceMessage)
                .filter(AttendanceMessage.evolution_id == evolution_id)
                .first()
            )
            if dup:
                return _message_to_dict(dup)

        msg = AttendanceMessage(
            id=message_id,
            conversation_id=conversation_id,
            direction=direction,
            msg_type=msg_type or "text",
            body=body or "",
            media_url=media_url or "",
            media_mime=media_mime or "",
            media_filename=media_filename or "",
            evolution_id=evolution_id,
            sender=sender,
            created_at=created,
        )
        db.add(msg)

        conv = db.get(AttendanceConversation, conversation_id)
        if conv:
            conv.last_message_at = created
            conv.last_message_preview = preview
            conv.updated_at = created
            conv.typing = False
            if bump_unread:
                conv.unread_count = int(conv.unread_count or 0) + 1

        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            # Corrida: outro worker inseriu o mesmo evolution_id
            if evolution_id:
                existing = (
                    db.query(AttendanceMessage)
                    .filter(AttendanceMessage.evolution_id == evolution_id)
                    .first()
                )
                if existing:
                    return _message_to_dict(existing)
            raise

        result = _message_to_dict(msg)

    _notify(
        {
            "type": "message",
            "conversation_id": conversation_id,
            "message_id": message_id,
            "direction": direction,
        }
    )
    return result


def get_message(message_id: str) -> dict | None:
    with _session(commit=False) as db:
        row = db.get(AttendanceMessage, message_id)
        return _message_to_dict(row) if row else None


def list_messages(conversation_id: str, *, limit: int = 200) -> list[dict]:
    with _session(commit=False) as db:
        rows = (
            db.query(AttendanceMessage)
            .filter(AttendanceMessage.conversation_id == conversation_id)
            .order_by(AttendanceMessage.created_at.asc(), AttendanceMessage.id.asc())
            .limit(max(1, min(int(limit or 200), 1000)))
            .all()
        )
        return [_message_to_dict(row) for row in rows]


def mark_conversation_read(conversation_id: str) -> None:
    _update_conversation(conversation_id, {"unread_count": 0, "updated_at": _now_iso()})
    _notify({"type": "conversation_read", "conversation_id": conversation_id})


def count_unread() -> int:
    with _session(commit=False) as db:
        total = db.query(func.coalesce(func.sum(AttendanceConversation.unread_count), 0)).scalar()
        return int(total or 0)


def get_sync_snapshot(conversation_id: str = "") -> dict:
    """Snapshot do inbox — usado pelo poll da UI."""
    conversation_id = normalize_text(conversation_id)
    with _session(commit=False) as db:
        unread = int(
            db.query(func.coalesce(func.sum(AttendanceConversation.unread_count), 0)).scalar()
            or 0
        )
        last_msg = (
            db.query(func.max(AttendanceConversation.last_message_at)).scalar() or ""
        )
        last_upd = db.query(func.max(AttendanceConversation.updated_at)).scalar() or ""
        msg_count = int(db.query(func.count(AttendanceMessage.id)).scalar() or 0)
        msg_max_id = db.query(func.max(AttendanceMessage.id)).scalar() or ""

        conv_token = ""
        if conversation_id:
            crow = (
                db.query(
                    func.count(AttendanceMessage.id),
                    func.max(AttendanceMessage.created_at),
                    func.max(AttendanceMessage.id),
                )
                .filter(AttendanceMessage.conversation_id == conversation_id)
                .one()
            )
            typing_row = db.get(AttendanceConversation, conversation_id)
            typing = 1 if typing_row and typing_row.typing else 0
            conv_token = f"{crow[0]}|{crow[1] or ''}|{crow[2] or ''}|{typing}"

    inbox_token = f"{unread}|{last_msg}|{last_upd}|{msg_count}|{msg_max_id}"
    return {
        "unread": unread,
        "inbox_token": inbox_token,
        "conversation_id": conversation_id or None,
        "conversation_token": conv_token or None,
    }
