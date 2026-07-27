"""Mensagens rápidas (atalhos /nome) para Atendimentos."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.services.legacy_core import normalize_text
from app.services.storage_paths import get_storage_dir
from database.connection import SessionLocal
from database.models import CrmQuickReply

MEDIA_TYPES = ("text", "image", "audio", "video")
MEDIA_TYPE_LABELS = {
    "text": "Texto",
    "image": "Imagem",
    "audio": "Áudio",
    "video": "Vídeo",
}
_SHORTCUT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None).isoformat(timespec="seconds")


def media_dir() -> Path:
    path = get_storage_dir() / "quick_replies"
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_shortcut(raw: str) -> str:
    clean = normalize_text(raw).lower().lstrip("/")
    clean = re.sub(r"\s+", "", clean)
    clean = re.sub(r"[^a-z0-9_-]+", "", clean)
    return clean


def _row_to_dict(row: CrmQuickReply) -> dict:
    media_type = (row.media_type or "text").strip().lower()
    if media_type not in MEDIA_TYPES:
        media_type = "text"
    shortcut = normalize_shortcut(row.shortcut)
    return {
        "id": row.id,
        "shortcut": shortcut,
        "command": f"/{shortcut}" if shortcut else "",
        "title": normalize_text(row.title) or shortcut,
        "body": row.body or "",
        "media_type": media_type,
        "media_type_label": MEDIA_TYPE_LABELS.get(media_type, media_type),
        "media_filename": row.media_filename or "",
        "media_mime": row.media_mime or "",
        "media_stored_name": row.media_stored_name or "",
        "has_media": bool(row.media_stored_name) and media_type != "text",
        "active": bool(row.active),
        "sort_order": int(row.sort_order or 0),
        "status_label": "Ativo" if row.active else "Inativo",
        "status_class": "active" if row.active else "inactive",
        "preview": _preview(row.body or "", media_type, row.media_filename or ""),
    }


def _preview(body: str, media_type: str, filename: str) -> str:
    text = normalize_text(body)
    if media_type == "text":
        return (text[:80] + "…") if len(text) > 80 else text
    label = MEDIA_TYPE_LABELS.get(media_type, media_type)
    name = normalize_text(filename) or "arquivo"
    if text:
        return f"{label}: {name} · {text[:40]}"
    return f"{label}: {name}"


def media_abs_path(item: dict | CrmQuickReply) -> Path | None:
    if isinstance(item, dict):
        stored = item.get("media_stored_name") or ""
    else:
        stored = item.media_stored_name or ""
    stored = Path(str(stored)).name
    if not stored:
        return None
    path = media_dir() / stored
    return path if path.is_file() else None


def list_quick_replies(*, active_only: bool = True) -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(CrmQuickReply)
        if active_only:
            q = q.filter(CrmQuickReply.active.is_(True))
        rows = q.order_by(CrmQuickReply.sort_order.asc(), CrmQuickReply.shortcut.asc()).all()
        return [_row_to_dict(row) for row in rows]
    finally:
        db.close()


def list_quick_reply_options() -> list[dict]:
    """Lista enxuta para o composer (autocomplete)."""
    return [
        {
            "id": row["id"],
            "shortcut": row["shortcut"],
            "command": row["command"],
            "title": row["title"],
            "media_type": row["media_type"],
            "media_type_label": row["media_type_label"],
            "preview": row["preview"],
        }
        for row in list_quick_replies(active_only=True)
    ]


def get_by_shortcut(shortcut: str) -> dict | None:
    key = normalize_shortcut(shortcut)
    if not key:
        return None
    db = SessionLocal()
    try:
        row = (
            db.query(CrmQuickReply)
            .filter(CrmQuickReply.shortcut == key, CrmQuickReply.active.is_(True))
            .first()
        )
        return _row_to_dict(row) if row else None
    finally:
        db.close()


def get_by_id(reply_id: int) -> dict | None:
    db = SessionLocal()
    try:
        row = db.query(CrmQuickReply).filter(CrmQuickReply.id == int(reply_id)).first()
        return _row_to_dict(row) if row else None
    finally:
        db.close()


def add_quick_reply(
    *,
    shortcut: str,
    title: str = "",
    body: str = "",
    media_type: str = "text",
    media_bytes: bytes | None = None,
    media_filename: str = "",
    media_mime: str = "",
) -> dict:
    key = normalize_shortcut(shortcut)
    if not key or not _SHORTCUT_RE.match(key):
        raise ValueError("Atalho inválido. Use letras/números (ex.: posvenda).")
    kind = normalize_text(media_type).lower() or "text"
    if kind not in MEDIA_TYPES:
        raise ValueError("Tipo inválido. Use texto, imagem, áudio ou vídeo.")
    text = str(body or "").strip()
    if kind == "text" and not text:
        raise ValueError("Informe o texto da mensagem rápida.")
    if kind != "text" and not media_bytes:
        raise ValueError("Envie um arquivo de mídia para este tipo.")

    stored_name = ""
    safe_filename = ""
    mime = normalize_text(media_mime)
    if kind != "text" and media_bytes:
        safe_filename = Path(normalize_text(media_filename) or f"arquivo.{kind}").name
        stored_name = f"{uuid.uuid4().hex}_{safe_filename}"
        dest = media_dir() / stored_name
        dest.write_bytes(media_bytes)
        if not mime:
            mime = {
                "image": "image/jpeg",
                "audio": "audio/ogg",
                "video": "video/mp4",
            }.get(kind, "application/octet-stream")

    db = SessionLocal()
    try:
        existing = db.query(CrmQuickReply).filter(CrmQuickReply.shortcut == key).first()
        if existing:
            if existing.media_stored_name:
                old = media_dir() / Path(existing.media_stored_name).name
                if old.is_file() and stored_name:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            existing.title = normalize_text(title) or key
            existing.body = text
            existing.media_type = kind
            existing.active = True
            if stored_name:
                existing.media_filename = safe_filename
                existing.media_mime = mime
                existing.media_stored_name = stored_name
            elif kind == "text":
                existing.media_filename = ""
                existing.media_mime = ""
                existing.media_stored_name = ""
            db.commit()
            db.refresh(existing)
            return _row_to_dict(existing)

        max_order = db.query(CrmQuickReply).count()
        row = CrmQuickReply(
            shortcut=key,
            title=normalize_text(title) or key,
            body=text,
            media_type=kind,
            media_filename=safe_filename,
            media_mime=mime,
            media_stored_name=stored_name,
            active=True,
            sort_order=int(max_order),
            created_at=_now_iso(),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise ValueError("Já existe um atalho com esse nome.")
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        db.close()


def remove_quick_reply(shortcut: str) -> None:
    key = normalize_shortcut(shortcut)
    db = SessionLocal()
    try:
        row = db.query(CrmQuickReply).filter(CrmQuickReply.shortcut == key).first()
        if not row:
            raise ValueError("Mensagem rápida não encontrada.")
        stored = Path(row.media_stored_name or "").name
        db.delete(row)
        db.commit()
        if stored:
            path = media_dir() / stored
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
    finally:
        db.close()
