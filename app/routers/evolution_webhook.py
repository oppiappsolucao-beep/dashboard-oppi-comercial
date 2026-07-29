"""Webhook Evolution API → Atendimentos."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services import attendances, attendances_storage as store
from app.services.evolution_client import (
    is_whatsapp_group_jid,
    message_looks_like_group,
    normalize_phone_from_jid,
    resolve_contact_identity,
)
from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

_WEBHOOK_STATS_LOCK = threading.Lock()
_WEBHOOK_STATS = {
    "received": 0,
    "authorized": 0,
    "unauthorized": 0,
    "messages_saved": 0,
    "last_at": "",
    "last_event": "",
    "last_instance": "",
    "last_messages": 0,
    "last_error": "",
}
_WEBHOOK_RECENT: deque[dict] = deque(maxlen=20)


def webhook_stats_snapshot() -> dict:
    with _WEBHOOK_STATS_LOCK:
        return {
            **dict(_WEBHOOK_STATS),
            "recent": list(_WEBHOOK_RECENT),
        }


def _record_webhook_hit(**fields) -> None:
    with _WEBHOOK_STATS_LOCK:
        for key, value in fields.items():
            if key in _WEBHOOK_STATS:
                if isinstance(_WEBHOOK_STATS[key], int) and isinstance(value, int) and key not in {
                    "last_messages",
                }:
                    _WEBHOOK_STATS[key] = int(_WEBHOOK_STATS[key]) + value
                else:
                    _WEBHOOK_STATS[key] = value
        _WEBHOOK_RECENT.appendleft(
            {
                "at": fields.get("last_at") or "",
                "event": fields.get("last_event") or "",
                "instance": fields.get("last_instance") or "",
                "messages": fields.get("last_messages") or 0,
                "error": fields.get("last_error") or "",
            }
        )


def _extract_instance_name(payload: dict) -> str:
    """Nome da instância Evolution no webhook (multi-linha)."""
    candidates = (
        payload.get("instance"),
        payload.get("instanceName"),
        _dig(payload, "data", "instance"),
        _dig(payload, "data", "instanceName"),
        _dig(payload, "instance", "instanceName"),
        _dig(payload, "instance", "name"),
    )
    for value in candidates:
        if isinstance(value, dict):
            name = normalize_text(
                value.get("instanceName") or value.get("name") or value.get("id") or ""
            )
        else:
            name = normalize_text(value or "")
        if name:
            try:
                from app.services.evolution_client import match_configured_instance

                return match_configured_instance(name)
            except Exception:
                return name
    return ""


def _token_ok(
    header_token: str | None,
    query_token: str | None,
    *,
    payload: dict | None = None,
) -> bool:
    expected = normalize_text(settings.evolution_webhook_token)
    provided = normalize_text(header_token) or normalize_text(query_token)
    if expected:
        if provided == expected:
            return True
    # Evolution costuma mandar apikey no body/header — aceita a mesma API key do CRM.
    api_key = normalize_text(settings.evolution_api_key)
    if api_key:
        body_key = ""
        if isinstance(payload, dict):
            body_key = normalize_text(payload.get("apikey") or payload.get("apiKey") or "")
        if provided == api_key or body_key == api_key:
            return True
    # Sem token configurado e sem API key exigida → libera (compat).
    if not expected:
        return True
    return False

def _dig(data: Any, *keys, default=None):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_event_name(payload: dict) -> str:
    for key in ("event", "type", "Event"):
        value = normalize_text(payload.get(key, "")).lower().replace(".", "_").replace("-", "_")
        if value:
            return value
    return ""


def _message_type_and_body(message: dict) -> tuple[str, str, str, str, str]:
    """Retorna (type, body, media_url, mime, filename)."""
    msg = message.get("message") if isinstance(message.get("message"), dict) else message
    if not isinstance(msg, dict):
        return "text", "", "", "", ""

    if msg.get("conversation"):
        return "text", normalize_text(msg.get("conversation")), "", "", ""
    if isinstance(msg.get("extendedTextMessage"), dict):
        return "text", normalize_text(msg["extendedTextMessage"].get("text")), "", "", ""

    image = msg.get("imageMessage") if isinstance(msg.get("imageMessage"), dict) else None
    if image:
        return (
            "image",
            normalize_text(image.get("caption")),
            normalize_text(image.get("url") or image.get("directPath")),
            normalize_text(image.get("mimetype")),
            "",
        )

    audio = msg.get("audioMessage") if isinstance(msg.get("audioMessage"), dict) else None
    if audio:
        return (
            "audio",
            "",
            normalize_text(audio.get("url") or audio.get("directPath")),
            normalize_text(audio.get("mimetype")),
            "",
        )

    document = msg.get("documentMessage") if isinstance(msg.get("documentMessage"), dict) else None
    if document:
        return (
            "document",
            normalize_text(document.get("caption") or document.get("title")),
            normalize_text(document.get("url") or document.get("directPath")),
            normalize_text(document.get("mimetype")),
            normalize_text(document.get("fileName") or document.get("title")),
        )

    video = msg.get("videoMessage") if isinstance(msg.get("videoMessage"), dict) else None
    if video:
        return (
            "video",
            normalize_text(video.get("caption")),
            normalize_text(video.get("url") or video.get("directPath")),
            normalize_text(video.get("mimetype")),
            "",
        )

    # stubs / outros
    for stub_key, stub_type in (
        ("stickerMessage", "image"),
        ("contactMessage", "text"),
        ("locationMessage", "text"),
    ):
        if msg.get(stub_key):
            return stub_type, f"[{stub_type}]", "", "", ""

    return "text", "", "", "", ""


def _iter_upsert_messages(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, dict):
        if "messages" in data:
            return [m for m in _as_list(data.get("messages")) if isinstance(m, dict)]
        # Alguns payloads aninham em data.message / data.key
        if "key" in data or "message" in data:
            return [data]
        nested = data.get("message")
        if isinstance(nested, dict) and (nested.get("key") or nested.get("message")):
            return [nested]
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    # formato plano
    if payload.get("key") or payload.get("message"):
        return [payload]
    return []


def ingest_evolution_message_item(
    item: dict,
    *,
    push_name: str = "",
    evolution_instance: str = "",
    allow_reopen: bool = False,
) -> bool:
    """Persiste um item no formato Evolution (webhook ou findMessages). Retorna True se salvou."""
    if not isinstance(item, dict):
        return False
    key = item.get("key") if isinstance(item.get("key"), dict) else {}
    if message_looks_like_group(key, item):
        return False
    phone, remote_jid = resolve_contact_identity(key, item)
    if not remote_jid:
        return False
    if is_whatsapp_group_jid(remote_jid) or (phone and is_whatsapp_group_jid(phone)):
        return False

    from_me = bool(key.get("fromMe") or item.get("fromMe"))
    name = normalize_text(
        push_name
        or item.get("pushName")
        or ""
    )
    msg_type, body, media_url, media_mime, media_filename = _message_type_and_body(item)
    if not body and not media_url and msg_type == "text":
        return False

    evolution_id = normalize_text(key.get("id") or item.get("id") or "")
    instance = normalize_text(evolution_instance)
    # Sync (allow_reopen=False) respeita exclusão; webhook deve usar allow_reopen=True
    suppressed = False
    try:
        suppressed = store.is_chat_suppressed(
            phone_e164=phone, remote_jid=remote_jid, evolution_instance=instance
        )
    except Exception:
        suppressed = False
    if suppressed:
        if not (allow_reopen and not from_me):
            return False
        try:
            store.clear_chat_suppression(
                phone_e164=phone, remote_jid=remote_jid, evolution_instance=instance
            )
        except Exception:
            pass
    ignore_suppression = bool(allow_reopen and not from_me)
    conversation = None
    if phone:
        conversation = store.upsert_conversation_by_phone(
            phone,
            contact_name=name,
            remote_jid=remote_jid,
            evolution_instance=instance,
            ignore_suppression=ignore_suppression,
        )
    if not conversation and remote_jid:
        conversation = store.upsert_conversation_by_remote_jid(
            remote_jid,
            contact_name=name,
            phone_e164=phone,
            evolution_instance=instance,
            ignore_suppression=ignore_suppression,
        )
    if not conversation:
        return False

    saved_msg = store.add_message(
        conversation["id"],
        direction="out" if from_me else "in",
        body=body or (f"[{msg_type}]" if msg_type != "text" else ""),
        msg_type=msg_type,
        media_url=media_url,
        media_mime=media_mime,
        media_filename=media_filename,
        evolution_id=evolution_id,
        sender="agent" if from_me else "contact",
        bump_unread=not from_me,
    )
    if (
        saved_msg
        and msg_type in ("audio", "image", "video", "document")
        and evolution_id
        and saved_msg.get("id")
    ):
        try:
            from app.services.attendance_media import schedule_hydrate_message

            schedule_hydrate_message(saved_msg["id"], conversation["id"])
        except Exception:
            logger.exception("hydrate schedule falhou jid=%s", remote_jid)
    return True


def _handle_messages_upsert(payload: dict) -> int:
    count = 0
    instance = _extract_instance_name(payload)
    for item in _iter_upsert_messages(payload):
        key = item.get("key") if isinstance(item.get("key"), dict) else {}
        remote_raw = normalize_text(key.get("remoteJid") or item.get("remoteJid") or "")
        # Bloqueia grupos ANTES de resolver identidade (participant vira "lead" falso)
        if message_looks_like_group(key, item):
            logger.info(
                "webhook drop group remoteJid=%s alt=%s",
                remote_raw,
                normalize_text(key.get("remoteJidAlt") or item.get("remoteJidAlt") or ""),
            )
            continue
        phone, remote_jid = resolve_contact_identity(key, item)
        if not remote_jid:
            logger.info("webhook drop no_jid raw=%s", remote_raw)
            continue
        if is_whatsapp_group_jid(remote_jid) or (phone and is_whatsapp_group_jid(phone)):
            logger.info("webhook drop group_jid phone=%s jid=%s", phone, remote_jid)
            continue

        from_me = bool(key.get("fromMe") or item.get("fromMe"))

        push_name = normalize_text(
            item.get("pushName")
            or _dig(payload, "data", "pushName")
            or payload.get("pushName")
            or ""
        )
        msg_type, body, media_url, media_mime, media_filename = _message_type_and_body(item)
        if not body and not media_url and msg_type == "text":
            # mensagem sem conteúdo útil (ex.: reaction-only)
            logger.info("webhook drop empty_body jid=%s type=%s", remote_jid, msg_type)
            continue

        evolution_id = normalize_text(key.get("id") or item.get("id") or "")
        # Inbound do lead só reabre exclusão se a mensagem for recente (evita
        # replay histórico / sync reabrir chat apagado). Outbound em excluído: drop.
        suppressed = False
        try:
            suppressed = store.is_chat_suppressed(
                phone_e164=phone, remote_jid=remote_jid, evolution_instance=instance
            )
        except Exception:
            logger.exception("is_chat_suppressed falhou no webhook")
            suppressed = False
        if suppressed and from_me:
            logger.info(
                "webhook drop suppressed_chat phone=%s jid=%s from_me=%s",
                phone,
                remote_jid,
                from_me,
            )
            continue

        msg_is_fresh = True
        try:
            ts_raw = item.get("messageTimestamp") or key.get("messageTimestamp") or 0
            ts = int(str(ts_raw).strip() or 0)
            if ts > 10_000_000_000:  # ms → s
                ts //= 1000
            if ts > 0:
                import time as _time

                msg_is_fresh = (_time.time() - ts) <= 600  # 10 min
        except Exception:
            msg_is_fresh = True

        if suppressed and not from_me and not msg_is_fresh:
            logger.info(
                "webhook drop suppressed_stale phone=%s jid=%s",
                phone,
                remote_jid,
            )
            continue
        if suppressed and not from_me and msg_is_fresh:
            try:
                store.clear_chat_suppression(
                    phone_e164=phone, remote_jid=remote_jid, evolution_instance=instance
                )
            except Exception:
                logger.exception("clear_chat_suppression falhou no webhook")
        # Mensagem nova do lead: pode reabrir; histórica/sync não
        ignore_suppression = bool(not from_me and msg_is_fresh)
        conversation = None
        if phone:
            conversation = store.upsert_conversation_by_phone(
                phone,
                contact_name=push_name,
                remote_jid=remote_jid,
                evolution_instance=instance,
                ignore_suppression=ignore_suppression,
            )
        if not conversation and remote_jid:
            conversation = store.upsert_conversation_by_remote_jid(
                remote_jid,
                contact_name=push_name,
                phone_e164=phone,
                evolution_instance=instance,
                ignore_suppression=ignore_suppression,
            )
        if not conversation:
            logger.info(
                "webhook drop no_conversation phone=%s jid=%s from_me=%s",
                phone,
                remote_jid,
                from_me,
            )
            continue

        direction = "out" if from_me else "in"
        sender = "agent" if from_me else "contact"
        # Grava a mensagem ANTES de CRM/IA — webhook não pode travar na planilha Google
        saved_msg = store.add_message(
            conversation["id"],
            direction=direction,
            body=body or (f"[{msg_type}]" if msg_type != "text" else ""),
            msg_type=msg_type,
            media_url=media_url,
            media_mime=media_mime,
            media_filename=media_filename,
            evolution_id=evolution_id,
            sender=sender,
            bump_unread=not from_me,
        )
        count += 1

        # Baixa áudio/mídia local para TODAS as conversas (não só a aberta)
        if (
            saved_msg
            and msg_type in ("audio", "image", "video", "document")
            and evolution_id
            and saved_msg.get("id")
        ):
            try:
                from app.services.attendance_media import schedule_hydrate_message

                schedule_hydrate_message(saved_msg["id"], conversation["id"])
            except Exception:
                logger.exception("hydrate schedule falhou jid=%s", remote_jid)

        conversation_id = conversation["id"]
        inbound_body = body if (not from_me and body) else ""

        def _post_save(
            conv_id: str = conversation_id,
            name: str = push_name,
            reply_text: str = inbound_body,
        ) -> None:
            try:
                current = store.get_conversation(conv_id)
                if current:
                    attendances.ensure_crm_link(current, contact_name=name)
                if reply_text:
                    attendances.maybe_ai_reply(conv_id, reply_text)
            except Exception:
                logger.exception("Falha pós-save webhook conversa %s", conv_id)

        threading.Thread(target=_post_save, daemon=True, name=f"wh-post-{conversation_id[:8]}").start()
    return count


def _handle_presence_or_typing(payload: dict) -> bool:
    """Presença / typing indicators (fase 2)."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return False
    jid = normalize_text(
        data.get("id")
        or data.get("remoteJid")
        or _dig(data, "key", "remoteJid")
        or ""
    )
    if not jid or is_whatsapp_group_jid(jid):
        return False
    phone = normalize_phone_from_jid(jid)
    # Presença: tenta achar conversa em qualquer linha pelo telefone
    conversation = store.get_conversation_by_phone(phone)
    if not conversation:
        return False

    presence = normalize_text(data.get("presences") or data.get("presence") or "").lower()
    # formatos comuns: composing / recording / paused / available
    if isinstance(data.get("presences"), dict):
        # { "5511...@s.whatsapp.net": { "lastKnownPresence": "composing" } }
        for value in data["presences"].values():
            if isinstance(value, dict):
                presence = normalize_text(value.get("lastKnownPresence") or "").lower()
                break
            presence = normalize_text(value).lower()
            break

    typing = presence in {"composing", "recording", "typing"}
    store.set_typing(conversation["id"], typing)
    return True


@router.get("/webhooks/evolution")
async def evolution_webhook_probe():
    """GET no navegador só confirma que a rota existe. A Evolution deve usar POST."""
    try:
        from app.services.evolution_client import webhook_callback_url

        callback = webhook_callback_url()
    except Exception:
        callback = "https://comercial.oppitech.com.br/webhooks/evolution"
    return JSONResponse(
        {
            "ok": True,
            "service": "evolution-webhook",
            "callback_url": callback,
            "stats": webhook_stats_snapshot(),
            "hint": "Este endpoint recebe POST da Evolution (MESSAGES_UPSERT).",
        }
    )


@router.get("/health/webhook")
async def health_webhook(
    ensure: str = Query(default="0"),
    sync: str = Query(default="0"),
):
    """Diagnóstico de inbound + reconfigura webhooks e/ou puxa chats da Evolution."""
    payload: dict[str, Any] = {
        "ok": True,
        "stats": webhook_stats_snapshot(),
    }
    try:
        from app.services.evolution_client import (
            ensure_webhooks_for_all_instances,
            find_instance_webhook,
            configured_instance_names,
            webhook_callback_url,
        )

        payload["callback_url"] = webhook_callback_url()
        if normalize_text(ensure) in {"1", "true", "yes", "sim"}:
            payload["ensure"] = ensure_webhooks_for_all_instances()
        else:
            payload["found"] = [
                find_instance_webhook(name)
                for name in (configured_instance_names() or [])
            ]
        if normalize_text(sync) in {"1", "true", "yes", "sim"}:
            from app.services.attendances import sync_inbox_from_evolution

            imported = sync_inbox_from_evolution(force=True, limit=50)
            payload["sync"] = {"imported_chats": int(imported or 0)}
    except Exception as error:
        payload["ok"] = False
        payload["error"] = str(error)
    payload["stats"] = webhook_stats_snapshot()
    return JSONResponse(payload)


@router.post("/webhooks/evolution")
async def evolution_webhook(
    request: Request,
    token: str | None = Query(default=None),
    x_evolution_token: str | None = Header(default=None, alias="X-Evolution-Token"),
    x_api_key: str | None = Header(default=None, alias="apikey"),
):
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        payload = await request.json()
    except Exception:
        _record_webhook_hit(
            received=1,
            last_at=now_iso,
            last_event="invalid_json",
            last_error="invalid_json",
        )
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

    if not isinstance(payload, dict):
        _record_webhook_hit(
            received=1,
            last_at=now_iso,
            last_event="invalid_payload",
            last_error="invalid_payload",
        )
        return JSONResponse({"ok": False, "error": "invalid_payload"}, status_code=400)

    auth_header = x_evolution_token or x_api_key
    if not _token_ok(auth_header, token, payload=payload):
        instance = _extract_instance_name(payload)
        _record_webhook_hit(
            received=1,
            unauthorized=1,
            last_at=now_iso,
            last_event="unauthorized",
            last_instance=instance,
            last_error="unauthorized",
        )
        logger.warning("webhook evolution unauthorized instance=%s", instance)
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    event = _extract_event_name(payload)
    instance = _extract_instance_name(payload)
    handled = 0
    typing_ok = False
    error = ""

    try:
        if "messages_upsert" in event or "message_upsert" in event or not event:
            handled = _handle_messages_upsert(payload)
            if not event and handled == 0:
                typing_ok = _handle_presence_or_typing(payload)
        elif "presence" in event or "typing" in event or "chats_update" in event:
            typing_ok = _handle_presence_or_typing(payload)
        else:
            handled = _handle_messages_upsert(payload)
    except Exception as exc:
        # Sempre responde 200 rápido — senão a Evolution para de entregar inbound
        logger.exception("webhook evolution falhou event=%s", event or "unknown")
        error = str(exc)[:200]

    _record_webhook_hit(
        received=1,
        authorized=1,
        messages_saved=int(handled or 0),
        last_at=now_iso,
        last_event=event or "unknown",
        last_instance=instance,
        last_messages=int(handled or 0),
        last_error=error or "",
    )

    return JSONResponse({
        "ok": not bool(error),
        "event": event or "unknown",
        "instance": instance,
        "messages": handled,
        "typing": typing_ok,
        "error": error or None,
    })
