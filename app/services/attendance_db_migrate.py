"""Migração one-shot: conversas/mensagens do SQLite local → DATABASE_URL."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from app.services.crm_local_db import _db_path
from database.connection import SessionLocal
from database.models import AppMeta, AttendanceConversation, AttendanceMessage

logger = logging.getLogger(__name__)

MIGRATION_KEY = "attendance_sqlite_migrated"


def _sqlite_has_attendance_tables(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('attendance_conversations', 'attendance_messages')"
    ).fetchall()
    names = {r[0] for r in rows}
    return "attendance_conversations" in names and "attendance_messages" in names


def migrate_attendance_from_sqlite_if_needed() -> dict:
    """
    Copia attendance_* do crm_local.db para DATABASE_URL se o destino estiver vazio.
    Idempotente: marca app_meta e não sobrescreve dados existentes.
    """
    result = {
        "ran": False,
        "skipped": True,
        "reason": "",
        "conversations": 0,
        "messages": 0,
    }

    db = SessionLocal()
    try:
        meta = db.get(AppMeta, MIGRATION_KEY)
        if meta and (meta.value or "").strip() in {"1", "true", "yes"}:
            result["reason"] = "already_migrated"
            return result

        existing = db.query(AttendanceConversation).count()
        if existing > 0:
            db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
            db.commit()
            result["reason"] = "destination_already_has_data"
            return result

        path: Path = _db_path()
        if not path.exists():
            db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
            db.commit()
            result["reason"] = "sqlite_missing"
            return result

        conn = sqlite3.connect(str(path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            if not _sqlite_has_attendance_tables(conn):
                db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
                db.commit()
                result["reason"] = "sqlite_no_attendance_tables"
                return result

            conv_rows = conn.execute(
                "SELECT * FROM attendance_conversations"
            ).fetchall()
            msg_rows = conn.execute("SELECT * FROM attendance_messages").fetchall()
            if not conv_rows and not msg_rows:
                db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
                db.commit()
                result["reason"] = "sqlite_empty"
                return result

            for row in conv_rows:
                keys = row.keys()
                db.merge(
                    AttendanceConversation(
                        id=row["id"],
                        phone_e164=row["phone_e164"] or "",
                        contact_name=(row["contact_name"] or "") if "contact_name" in keys else "",
                        profile_pic_url=(row["profile_pic_url"] or "")
                        if "profile_pic_url" in keys
                        else "",
                        sheet_row=row["sheet_row"] if "sheet_row" in keys else None,
                        status=(row["status"] or "novo_lead") if "status" in keys else "novo_lead",
                        assignee=(row["assignee"] or "") if "assignee" in keys else "",
                        ai_mode=(row["ai_mode"] or "on") if "ai_mode" in keys else "on",
                        tags_json=(row["tags_json"] or "[]") if "tags_json" in keys else "[]",
                        notes=(row["notes"] or "") if "notes" in keys else "",
                        last_message_at=(row["last_message_at"] or "")
                        if "last_message_at" in keys
                        else "",
                        last_message_preview=(row["last_message_preview"] or "")
                        if "last_message_preview" in keys
                        else "",
                        unread_count=int(row["unread_count"] or 0)
                        if "unread_count" in keys
                        else 0,
                        typing=bool(row["typing"]) if "typing" in keys else False,
                        remote_jid=(row["remote_jid"] or "") if "remote_jid" in keys else "",
                        created_at=(row["created_at"] or "") if "created_at" in keys else "",
                        updated_at=(row["updated_at"] or "") if "updated_at" in keys else "",
                    )
                )
                result["conversations"] += 1

            for row in msg_rows:
                keys = row.keys()
                db.merge(
                    AttendanceMessage(
                        id=row["id"],
                        conversation_id=row["conversation_id"],
                        direction=row["direction"],
                        msg_type=(row["msg_type"] or "text") if "msg_type" in keys else "text",
                        body=(row["body"] or "") if "body" in keys else "",
                        media_url=(row["media_url"] or "") if "media_url" in keys else "",
                        media_mime=(row["media_mime"] or "") if "media_mime" in keys else "",
                        media_filename=(row["media_filename"] or "")
                        if "media_filename" in keys
                        else "",
                        evolution_id=(row["evolution_id"] or "")
                        if "evolution_id" in keys
                        else "",
                        sender=(row["sender"] or "contact") if "sender" in keys else "contact",
                        created_at=(row["created_at"] or "") if "created_at" in keys else "",
                    )
                )
                result["messages"] += 1

            db.merge(AppMeta(key=MIGRATION_KEY, value="1"))
            db.commit()
            result["ran"] = True
            result["skipped"] = False
            result["reason"] = "migrated"
            logger.info(
                "Migração attendance SQLite→DB: %s conversas, %s mensagens",
                result["conversations"],
                result["messages"],
            )
            return result
        finally:
            conn.close()
    except Exception:
        db.rollback()
        logger.exception("Falha na migração attendance SQLite→DATABASE_URL")
        raise
    finally:
        db.close()
