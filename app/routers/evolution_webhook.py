"""Webhook Evolution API → Atendimentos."""
from __future__ import annotations

import asyncio
import logging
import queue
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

# Processamento pós-ACK via BackgroundTasks (daemon queue falhava: queued sem save).
_WEBHOOK_INFLIGHT_LOCK = threading.Lock()
_WEBHOOK_INFLIGHT = [0]
_WEBHOOK_INFLIGHT_MAX = 80

# Mantidos por compat (não são mais o caminho principal do POST).
_WEBHOOK_QUEUE: queue.Queue = queue.Queue(maxsize=300)
_WEBHOOK_WORKERS_STARTED = False
_WEBHOOK_WORKERS_LOCK = threading.Lock()

_WEBHOOK_STATS_LOCK = threading.Lock()
_WEBHOOK_STATS = {
    "received": 0,
    "authorized": 0,
    "unauthorized": 0,
    "messages_saved": 0,
    "dropped": 0,
    "last_at": "",
    "last_event": "",
    "last_instance": "",
    "last_messages": 0,
    "last_error": "",
    "last_drop_reason": "",
    "last_payload_keys": "",
    "drop_counts": {},
}
_WEBHOOK_RECENT: deque[dict] = deque(maxlen=20)


def webhook_stats_snapshot() -> dict:
    with _WEBHOOK_STATS_LOCK:
        with _WEBHOOK_INFLIGHT_LOCK:
            inflight = int(_WEBHOOK_INFLIGHT[0])
        return {
            **dict(_WEBHOOK_STATS),
            "recent": list(_WEBHOOK_RECENT),
            "queue_size": _WEBHOOK_QUEUE.qsize(),
            "inflight": inflight,
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


def _process_webhook_job(payload: dict, event: str, instance: str, now_iso: str) -> None:
    handled = 0
    error = ""
    try:
        if "connection_update" in event or "send_message" in event:
            # Eventos de conexão/envio — não são mensagem inbound.
            handled = 0
        elif "messages_upsert" in event or "message_upsert" in event or not event:
            handled = _handle_messages_upsert(payload)
            if not event and handled == 0:
                _handle_presence_or_typing(payload)
        elif "presence" in event or "typing" in event or "chats_update" in event:
            _handle_presence_or_typing(payload)
        elif "messages_update" in event or "messages_set" in event:
            # Não hidratar aqui: findMessages na Evolution estava travando a inbox.
            handled = 0
        else:
            handled = _handle_messages_upsert(payload)
    except Exception as exc:
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


def _webhook_worker_loop() -> None:
    while True:
        try:
            job = _WEBHOOK_QUEUE.get()
        except Exception:
            time.sleep(0.2)
            continue
        try:
            payload, event, instance, now_iso = job
            _process_webhook_job(payload, event, instance, now_iso)
        except Exception:
            logger.exception("webhook worker job falhou")
        finally:
            try:
                _WEBHOOK_QUEUE.task_done()
            except Exception:
                pass


def _ensure_webhook_workers() -> None:
    global _WEBHOOK_WORKERS_STARTED
    if _WEBHOOK_WORKERS_STARTED:
        return
    with _WEBHOOK_WORKERS_LOCK:
        if _WEBHOOK_WORKERS_STARTED:
            return
        for idx in range(2):
            threading.Thread(
                target=_webhook_worker_loop,
                daemon=True,
                name=f"wh-worker-{idx}",
            ).start()
        _WEBHOOK_WORKERS_STARTED = True


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

    # Desembrulha envelopes comuns do Baileys/Evolution (senão body fica vazio).
    for _ in range(4):
        unwrapped = False
        for wrap in (
            "ephemeralMessage",
            "viewOnceMessage",
            "viewOnceMessageV2",
            "viewOnceMessageV2Extension",
            "documentWithCaptionMessage",
            "editedMessage",
        ):
            inner = msg.get(wrap)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                msg = inner["message"]
                unwrapped = True
                break
        if not unwrapped:
            break

    if msg.get("conversation"):
        return "text", normalize_text(msg.get("conversation")), "", "", ""
    if isinstance(msg.get("extendedTextMessage"), dict):
        return "text", normalize_text(msg["extendedTextMessage"].get("text")), "", "", ""
    if isinstance(msg.get("buttonsResponseMessage"), dict):
        return (
            "text",
            normalize_text(
                msg["buttonsResponseMessage"].get("selectedDisplayText")
                or msg["buttonsResponseMessage"].get("selectedButtonId")
            ),
            "",
            "",
            "",
        )
    if isinstance(msg.get("listResponseMessage"), dict):
        single = msg["listResponseMessage"].get("singleSelectReply")
        if isinstance(single, dict):
            return "text", normalize_text(single.get("selectedRowId")), "", "", ""
        return "text", normalize_text(msg["listResponseMessage"].get("title")), "", "", ""
    if isinstance(msg.get("templateButtonReplyMessage"), dict):
        return (
            "text",
            normalize_text(
                msg["templateButtonReplyMessage"].get("selectedDisplayText")
                or msg["templateButtonReplyMessage"].get("selectedId")
            ),
            "",
            "",
            "",
        )

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
        ("contactsArrayMessage", "text"),
        ("locationMessage", "text"),
        ("liveLocationMessage", "text"),
        ("reactionMessage", "text"),
    ):
        if msg.get(stub_key):
            label = stub_type if stub_key != "reactionMessage" else "reaction"
            return stub_type, f"[{label}]", "", "", ""

    # Fallback: messageType no item pai (Evolution v2)
    parent_type = normalize_text(message.get("messageType") or "").lower()
    if parent_type and parent_type not in {"", "unknown", "protocolmessage", "senderkeydistributionmessage"}:
        return "text", f"[{parent_type}]", "", "", ""

    return "text", "", "", "", ""


def _iter_upsert_messages(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, dict):
        if "messages" in data:
            return [m for m in _as_list(data.get("messages")) if isinstance(m, dict)]
        # MESSAGES_UPDATE flat: remoteJid + status + keyId — sem conteúdo de mensagem
        if (
            "status" in data
            and "message" not in data
            and "messageType" not in data
            and not isinstance(data.get("key"), dict)
        ):
            return []
        # Alguns payloads aninham em data.message / data.key
        if isinstance(data.get("key"), dict) or "message" in data or "messageType" in data:
            return [data]
        nested = data.get("message")
        if isinstance(nested, dict) and (nested.get("key") or nested.get("message")):
            return [nested]
        # Evolution às vezes manda conteúdo sem key aninhada
        if any(k in data for k in ("pushName", "messageTimestamp", "source")) and (
            "message" in data or "conversation" in data or "extendedTextMessage" in data
        ):
            return [data]
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    # formato plano
    if payload.get("key") or payload.get("message") or payload.get("messageType"):
        return [payload]
    return []


_HYDRATE_LOCK = threading.Lock()
_HYDRATE_LAST: dict[str, float] = {}
_HYDRATE_MIN_INTERVAL_SEC = 20.0


def _hydrate_chat_from_update(payload: dict, instance: str) -> int:
    """Quando só chega UPDATE (sem body), puxa mensagens recentes daquele chat."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    remote_jid = normalize_text(
        data.get("remoteJid")
        or _dig(data, "key", "remoteJid")
        or payload.get("remoteJid")
        or ""
    )
    if not remote_jid or is_whatsapp_group_jid(remote_jid):
        return 0

    throttle_key = f"{normalize_text(instance)}|{remote_jid}"
    now = time.monotonic()
    with _HYDRATE_LOCK:
        last = float(_HYDRATE_LAST.get(throttle_key) or 0)
        if (now - last) < _HYDRATE_MIN_INTERVAL_SEC:
            return 0
        _HYDRATE_LAST[throttle_key] = now
        # Limpa chaves antigas para não crescer sem limite
        if len(_HYDRATE_LAST) > 400:
            cutoff = now - 600
            for key in [k for k, ts in _HYDRATE_LAST.items() if ts < cutoff]:
                _HYDRATE_LAST.pop(key, None)

    try:
        from app.services.evolution_client import find_messages

        records = find_messages(remote_jid, limit=12, instance=instance)
    except Exception:
        logger.exception("hydrate find_messages falhou jid=%s", remote_jid)
        return 0

    saved = 0
    for item in records:
        try:
            if ingest_evolution_message_item(
                item,
                evolution_instance=instance,
                allow_reopen=True,
            ):
                saved += 1
        except Exception:
            logger.exception("hydrate ingest falhou jid=%s", remote_jid)
    if saved:
        logger.info(
            "hydrate ok instance=%s jid=%s saved=%s",
            instance,
            remote_jid,
            saved,
        )
    else:
        _note_drop("hydrate_empty", instance=instance, jid=remote_jid)
    return saved

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


def _note_drop(reason: str, **extra) -> None:
    with _WEBHOOK_STATS_LOCK:
        _WEBHOOK_STATS["dropped"] = int(_WEBHOOK_STATS.get("dropped") or 0) + 1
        counts = _WEBHOOK_STATS.get("drop_counts")
        if not isinstance(counts, dict):
            counts = {}
        counts[reason] = int(counts.get(reason) or 0) + 1
        _WEBHOOK_STATS["drop_counts"] = counts
        _WEBHOOK_STATS["last_drop_reason"] = reason
        if extra:
            bits = [f"{k}={v}" for k, v in extra.items() if v not in (None, "")]
            if bits:
                _WEBHOOK_STATS["last_drop_reason"] = f"{reason} ({', '.join(bits)})"


def _handle_messages_upsert(payload: dict) -> int:
    count = 0
    instance = _extract_instance_name(payload)
    items = _iter_upsert_messages(payload)
    data = payload.get("data")
    with _WEBHOOK_STATS_LOCK:
        keys = []
        if isinstance(payload, dict):
            keys.extend(sorted(str(k) for k in payload.keys())[:12])
        if isinstance(data, dict):
            keys.append("data:" + ",".join(sorted(str(k) for k in data.keys())[:12]))
        _WEBHOOK_STATS["last_payload_keys"] = " | ".join(keys)
    if not items:
        _note_drop("no_items", instance=instance)
        logger.info(
            "webhook drop no_items instance=%s keys=%s",
            instance,
            _WEBHOOK_STATS.get("last_payload_keys"),
        )
        return 0

    for item in items:
        key = item.get("key") if isinstance(item.get("key"), dict) else {}
        remote_raw = normalize_text(key.get("remoteJid") or item.get("remoteJid") or "")
        message_type = normalize_text(item.get("messageType") or "").lower()
        if message_type in {
            "protocolmessage",
            "senderkeydistributionmessage",
            "reactionmessage",
        }:
            # reaction agora vira stub no parser; protocol/skmsg ainda ignora
            if message_type != "reactionmessage":
                _note_drop("protocol", type=message_type, jid=remote_raw)
                continue
        # Bloqueia grupos ANTES de resolver identidade (participant vira "lead" falso)
        if message_looks_like_group(key, item):
            _note_drop("group", jid=remote_raw)
            logger.info(
                "webhook drop group remoteJid=%s alt=%s",
                remote_raw,
                normalize_text(key.get("remoteJidAlt") or item.get("remoteJidAlt") or ""),
            )
            continue
        phone, remote_jid = resolve_contact_identity(key, item)
        if not remote_jid:
            _note_drop("no_jid", raw=remote_raw)
            logger.info("webhook drop no_jid raw=%s", remote_raw)
            continue
        try:
            from app.services.evolution_client import is_usable_whatsapp_identity

            if not is_usable_whatsapp_identity(phone=phone, remote_jid=remote_jid):
                _note_drop("invalid_identity", phone=phone, jid=remote_jid)
                logger.info(
                    "webhook drop invalid_identity phone=%s jid=%s",
                    phone,
                    remote_jid,
                )
                continue
        except Exception:
            pass
        if is_whatsapp_group_jid(remote_jid) or (phone and is_whatsapp_group_jid(phone)):
            _note_drop("group_jid", phone=phone, jid=remote_jid)
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
            _note_drop("empty_body", jid=remote_jid, type=message_type or msg_type)
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
            _note_drop("suppressed_out", phone=phone, jid=remote_jid)
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

                msg_is_fresh = (_time.time() - ts) <= 86_400  # 24h — reabre exclusão
        except Exception:
            msg_is_fresh = True

        if suppressed and not from_me and not msg_is_fresh:
            _note_drop("suppressed_stale", phone=phone, jid=remote_jid)
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
            _note_drop(
                "no_conversation",
                phone=phone,
                jid=remote_jid,
                from_me=from_me,
            )
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
    unsuppress: str = Query(default="0"),
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
        if normalize_text(unsuppress) in {"1", "true", "yes", "sim"}:
            try:
                payload["unsuppress"] = await asyncio.to_thread(
                    lambda: store.clear_all_chat_suppressions(reopen_excluded=True)
                )
            except Exception as unsuppress_error:
                payload["unsuppress"] = {
                    "ok": False,
                    "error": str(unsuppress_error)[:200],
                }
        if normalize_text(ensure) in {"1", "true", "yes", "sim"}:
            # Nunca await HTTP Evolution aqui — travava /health/webhook por minutos.
            def _ensure_bg() -> None:
                try:
                    ensure_webhooks_for_all_instances()
                except Exception:
                    logger.exception("ensure_webhooks background falhou")

            threading.Thread(target=_ensure_bg, daemon=True, name="wh-ensure").start()
            payload["ensure"] = {"scheduled": True}
        else:
            try:
                payload["found"] = await asyncio.wait_for(
                    asyncio.to_thread(
                        lambda: [
                            find_instance_webhook(name)
                            for name in (configured_instance_names() or [])
                        ]
                    ),
                    timeout=8,
                )
            except Exception as found_error:
                payload["found_error"] = str(found_error)[:200]
        if normalize_text(sync) in {"1", "true", "yes", "sim"}:
            # Nunca bloqueia o worker — sync pesado em background
            from app.services.attendances import schedule_sync_inbox_from_evolution

            schedule_sync_inbox_from_evolution(force=True)
            payload["sync"] = {"scheduled": True}
    except Exception as error:
        payload["ok"] = False
        payload["error"] = str(error)
    payload["stats"] = webhook_stats_snapshot()
    try:
        payload["inbox"] = {
            "unread": int(store.count_unread() or 0),
            "conversations": len(store.list_conversations(search="", status="") or []),
        }
    except Exception as inbox_error:
        payload["inbox_error"] = str(inbox_error)[:200]
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

    # ACK imediato: processa em thread daemon (to_thread síncrono travava a Evolution
    # quando findMessages/hydrate demorava; BackgroundTasks/fila também falhavam).
    with _WEBHOOK_INFLIGHT_LOCK:
        inflight = int(_WEBHOOK_INFLIGHT[0])
        if inflight >= _WEBHOOK_INFLIGHT_MAX:
            _record_webhook_hit(
                received=1,
                authorized=1,
                last_at=now_iso,
                last_event=event or "unknown",
                last_instance=instance,
                last_error="inflight_max",
            )
            logger.warning("webhook inflight_max instance=%s", instance)
            return JSONResponse(
                {"ok": False, "error": "busy", "instance": instance},
                status_code=503,
            )
        _WEBHOOK_INFLIGHT[0] = inflight + 1

    def _run_job() -> None:
        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_process_webhook_job, payload, event, instance, now_iso)
                try:
                    fut.result(timeout=15)
                except concurrent.futures.TimeoutError:
                    logger.error(
                        "webhook job timeout event=%s instance=%s",
                        event,
                        instance,
                    )
                    _record_webhook_hit(
                        received=1,
                        authorized=1,
                        last_at=now_iso,
                        last_event=event or "unknown",
                        last_instance=instance,
                        last_error="job_timeout",
                    )
        finally:
            with _WEBHOOK_INFLIGHT_LOCK:
                _WEBHOOK_INFLIGHT[0] = max(0, int(_WEBHOOK_INFLIGHT[0]) - 1)

    threading.Thread(
        target=_run_job,
        daemon=True,
        name=f"wh-{normalize_text(event)[:16] or 'job'}",
    ).start()

    return JSONResponse({
        "ok": True,
        "event": event or "unknown",
        "instance": instance,
        "queued": True,
        "inflight": inflight + 1,
    })
