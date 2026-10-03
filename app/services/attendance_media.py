"""Persistência local de mídia de Atendimentos (todas as conversas)."""
from __future__ import annotations

import base64
import logging
import threading
import uuid
from pathlib import Path

from app.services import attendances_storage as store
from app.services import evolution_client
from app.services.legacy_core import normalize_text
from app.services.storage_paths import get_storage_dir

logger = logging.getLogger(__name__)

_MEDIA_TYPES = {"audio", "image", "video", "document"}
_HYDRATE_GUARD: set[str] = set()
_HYDRATE_LOCK = threading.Lock()


def media_dir() -> Path:
    path = get_storage_dir() / "attendance_media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_local_media_url(url: str) -> bool:
    return normalize_text(url).startswith("/atendimentos/media/")


def _ext_for_mime(mime: str, msg_type: str = "audio") -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    mapping = {
        "audio/ogg": "ogg",
        "audio/opus": "ogg",
        "audio/webm": "webm",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/aac": "aac",
        "audio/wav": "wav",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "application/pdf": "pdf",
    }
    if mime in mapping:
        return mapping[mime]
    if msg_type == "audio":
        return "ogg"
    if msg_type == "image":
        return "jpg"
    if msg_type == "video":
        return "mp4"
    return "bin"


def persist_media_bytes(
    raw: bytes,
    *,
    mimetype: str = "",
    filename: str = "",
    msg_type: str = "audio",
) -> tuple[str, str, str]:
    """Grava bytes em disco e devolve (local_url, mime, filename)."""
    if not raw:
        raise ValueError("Arquivo de mídia vazio.")
    mime = (mimetype or "").split(";")[0].strip() or {
        "audio": "audio/ogg",
        "image": "image/jpeg",
        "video": "video/mp4",
    }.get(msg_type, "application/octet-stream")
    base_name = Path(normalize_text(filename) or f"{msg_type}.{_ext_for_mime(mime, msg_type)}").name
    if "." not in base_name:
        base_name = f"{base_name}.{_ext_for_mime(mime, msg_type)}"
    safe_name = f"{uuid.uuid4().hex}_{base_name}"
    dest = media_dir() / safe_name
    dest.write_bytes(raw)
    return f"/atendimentos/media/{safe_name}", mime, base_name


def persist_media_base64(
    b64: str,
    *,
    mimetype: str = "",
    filename: str = "",
    msg_type: str = "audio",
) -> tuple[str, str, str]:
    payload = normalize_text(b64)
    if payload.startswith("data:") and "," in payload:
        header, payload = payload.split(",", 1)
        if not mimetype and ";base64" in header and ":" in header:
            mimetype = header.split(":", 1)[1].split(";", 1)[0].strip()
    raw = base64.b64decode(payload, validate=False)
    return persist_media_bytes(raw, mimetype=mimetype, filename=filename, msg_type=msg_type)


def hydrate_message_media(message: dict, *, conversation: dict | None = None) -> dict:
    """Garante URL local reproduzível para uma mensagem de mídia (qualquer conversa)."""
    if not isinstance(message, dict):
        return message
    msg_type = normalize_text(message.get("type") or message.get("msg_type") or "text")
    if msg_type not in _MEDIA_TYPES:
        return message
    url = normalize_text(message.get("media_url") or "")
    if is_local_media_url(url):
        # Confere se o arquivo ainda existe
        name = Path(url).name
        if (media_dir() / name).is_file():
            return message

    evolution_id = normalize_text(message.get("evolution_id") or "")
    message_id = normalize_text(message.get("id") or "")
    if not evolution_id or not message_id:
        return message

    with _HYDRATE_LOCK:
        if message_id in _HYDRATE_GUARD:
            return message
        _HYDRATE_GUARD.add(message_id)

    try:
        conv = conversation
        if not conv:
            conv = store.get_conversation(message.get("conversation_id") or "") or {}
        from_me = normalize_text(message.get("direction") or "") == "out"
        data = evolution_client.get_base64_from_media_message(
            evolution_id,
            remote_jid=normalize_text(conv.get("remote_jid") or ""),
            from_me=from_me,
            instance=normalize_text(conv.get("evolution_instance") or ""),
        )
        local_url, mime, filename = persist_media_base64(
            data.get("base64") or "",
            mimetype=data.get("mimetype") or message.get("media_mime") or "",
            filename=data.get("filename") or message.get("media_filename") or "",
            msg_type=msg_type,
        )
        updated = store.update_message_media(
            message_id,
            media_url=local_url,
            media_mime=mime,
            media_filename=filename,
        )
        return updated or {**message, "media_url": local_url, "media_mime": mime, "media_filename": filename}
    except Exception as error:
        logger.info(
            "hydrate media skip msg=%s evo=%s: %s",
            message_id,
            evolution_id,
            error,
        )
        return message
    finally:
        with _HYDRATE_LOCK:
            _HYDRATE_GUARD.discard(message_id)


def hydrate_messages_media(
    messages: list[dict],
    *,
    conversation: dict | None = None,
    limit: int = 12,
) -> list[dict]:
    """Hidrata mídias sem URL local (áudio primeiro) em qualquer conversa."""
    if not messages:
        return messages
    pending = [
        m
        for m in messages
        if normalize_text(m.get("type") or "") in _MEDIA_TYPES
        and not is_local_media_url(m.get("media_url") or "")
        and normalize_text(m.get("evolution_id") or "")
    ]
    # Prioriza áudio — é o que quebra o player
    pending.sort(key=lambda m: 0 if normalize_text(m.get("type") or "") == "audio" else 1)
    by_id = {normalize_text(m.get("id") or ""): m for m in messages}
    for item in pending[: max(0, int(limit or 12))]:
        updated = hydrate_message_media(item, conversation=conversation)
        mid = normalize_text(updated.get("id") or item.get("id") or "")
        if mid:
            by_id[mid] = updated
    return [by_id.get(normalize_text(m.get("id") or ""), m) for m in messages]


def schedule_hydrate_message(message_id: str, conversation_id: str = "") -> None:
    """Baixa mídia em background após webhook (não trava o inbox)."""

    def _run() -> None:
        try:
            msg = store.get_message(message_id)
            if not msg:
                return
            conv = store.get_conversation(conversation_id or msg.get("conversation_id") or "")
            hydrate_message_media(msg, conversation=conv)
        except Exception:
            logger.exception("schedule_hydrate_message falhou id=%s", message_id)

    threading.Thread(target=_run, daemon=True).start()


def backfill_all_conversations_media(*, limit: int = 80) -> int:
    """Tenta localizar áudio/mídia antiga em TODAS as conversas."""
    fixed = 0
    for item in store.list_messages_needing_media(limit=limit):
        before = normalize_text(item.get("media_url") or "")
        updated = hydrate_message_media(item)
        after = normalize_text(updated.get("media_url") or "")
        if after and after != before and is_local_media_url(after):
            fixed += 1
    return fixed
