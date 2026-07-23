"""Cliente HTTP da Evolution API (WhatsApp)."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import requests

from app.config import settings
from app.services.legacy_core import normalize_digits, normalize_text

logger = logging.getLogger(__name__)


class EvolutionClientError(RuntimeError):
    pass


def is_configured() -> bool:
    return settings.evolution_configured


def _headers() -> dict[str, str]:
    return {
        "apikey": settings.evolution_api_key,
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    base = settings.evolution_api_url.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _instance_name() -> str:
    name = normalize_text(settings.evolution_instance)
    if not name:
        raise EvolutionClientError("EVOLUTION_INSTANCE não configurada.")
    return name


def fetch_instance_names() -> list[str]:
    try:
        response = requests.get(_url("/instance/fetchInstances"), headers=_headers(), timeout=20)
    except requests.RequestException:
        return []
    if response.status_code >= 400:
        return []
    data = _parse_json(response)
    rows = data if isinstance(data, list) else data.get("data") or data.get("instances") or []
    if isinstance(data, dict) and not rows and data.get("name"):
        rows = [data]
    names: list[str] = []
    if not isinstance(rows, list):
        return names
    for item in rows:
        if isinstance(item, str):
            names.append(item)
            continue
        if not isinstance(item, dict):
            continue
        nested = item.get("instance") if isinstance(item.get("instance"), dict) else {}
        candidate = (
            item.get("name")
            or item.get("instanceName")
            or item.get("instanceId")
            or nested.get("instanceName")
            or nested.get("name")
            or ""
        )
        candidate = normalize_text(candidate)
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def resolved_instance_name() -> str:
    configured = _instance_name()
    names = fetch_instance_names()
    if not names:
        return configured
    if configured in names:
        return configured
    lower = configured.lower()
    for name in names:
        if name.lower() == lower:
            return name
    # match parcial (ex.: configurado "Oppi" e existe "Oppi Comercial")
    for name in names:
        if lower in name.lower() or name.lower() in lower:
            return name
    raise EvolutionClientError(
        f"Instância '{configured}' não encontrada na Evolution. "
        f"Disponíveis: {', '.join(names)}. "
        "Ajuste EVOLUTION_INSTANCE no Easypanel."
    )


def _instance_urls(segment: str) -> list[str]:
    """Gera URLs com nome da instância encoded e raw (alguns proxies diferem)."""
    name = resolved_instance_name()
    encoded = quote(name, safe="")
    paths = [f"{segment}/{encoded}"]
    if encoded != name:
        paths.append(f"{segment}/{name}")
    # também tenta o nome configurado cru, se diferente
    configured = _instance_name()
    if configured != name:
        paths.append(f"{segment}/{quote(configured, safe='')}")
    return [_url(p) for p in paths]


def _parse_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}
    if isinstance(data, dict):
        return data
    return {"data": data}


def _response_looks_like_error(data: dict[str, Any]) -> str:
    status = normalize_text(data.get("status") or "").lower()
    if status in {"error", "unauthorized", "forbidden", "not found", "404"}:
        return str(data.get("message") or data.get("error") or data)
    if data.get("error"):
        return str(data.get("message") or data.get("error"))
    # formato comum: {"status":404,"error":"Not Found","response":{"message":[...]}}
    nested = data.get("response")
    if isinstance(nested, dict) and nested.get("message"):
        msg = nested.get("message")
        if isinstance(msg, list):
            return "; ".join(str(x) for x in msg)
        return str(msg)
    return ""


def extract_message_id(response: dict | None) -> str:
    data = response or {}
    stack = [data]
    seen = 0
    while stack and seen < 30:
        seen += 1
        cur = stack.pop(0)
        if not isinstance(cur, dict):
            continue
        for candidate in ("id", "messageId", "message_id"):
            value = cur.get(candidate)
            if value and candidate != "instance" and len(str(value)) >= 6:
                # evita pegar ids genéricos demais; ids WA costumam ser longos
                text = normalize_text(value)
                if text and text.lower() not in {"open", "close", "connected"}:
                    # key.id do WhatsApp
                    if cur.get("fromMe") is not None or cur.get("remoteJid") or candidate.startswith("message"):
                        return text
        key = cur.get("key")
        if isinstance(key, dict) and key.get("id"):
            return normalize_text(key.get("id"))
        for child_key in ("data", "message", "key", "response"):
            child = cur.get(child_key)
            if isinstance(child, dict):
                stack.append(child)
            elif isinstance(child, list):
                stack.extend([x for x in child if isinstance(x, dict)])
    # fallback: qualquer key.id
    key = data.get("key") if isinstance(data.get("key"), dict) else None
    if key and key.get("id"):
        return normalize_text(key.get("id"))
    nested = data.get("data") if isinstance(data.get("data"), dict) else None
    if nested:
        key = nested.get("key") if isinstance(nested.get("key"), dict) else None
        if key and key.get("id"):
            return normalize_text(key.get("id"))
    return ""


def normalize_phone_from_jid(jid: str) -> str:
    raw = normalize_text(jid)
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    if ":" in raw:
        raw = raw.split(":", 1)[0]
    digits = normalize_digits(raw)
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    if len(digits) >= 10:
        return f"55{digits}"
    return digits


def resolve_contact_identity(key: dict | None, item: dict | None = None) -> tuple[str, str]:
    """
    Retorna (phone_e164, remote_jid_para_envio).

    WhatsApp/Evolution às vezes manda @lid em remoteJid e o número real em remoteJidAlt.
    Para responder, precisamos do JID original da conversa.
    """
    key = key if isinstance(key, dict) else {}
    item = item if isinstance(item, dict) else {}

    remote_jid = normalize_text(key.get("remoteJid") or item.get("remoteJid") or "")
    remote_alt = normalize_text(
        key.get("remoteJidAlt")
        or item.get("remoteJidAlt")
        or key.get("participant")
        or ""
    )
    sender_pn = normalize_text(
        key.get("senderPn")
        or item.get("senderPn")
        or item.get("sender")
        or ""
    )

    phone = ""
    send_jid = remote_jid

    # Preferir JID de telefone real
    for candidate in (remote_alt, sender_pn, remote_jid):
        if not candidate:
            continue
        lower = candidate.lower()
        if lower.endswith("@g.us") or "broadcast" in lower:
            continue
        if "@lid" in lower:
            continue
        if "@s.whatsapp.net" in lower or "@c.us" in lower or "@" not in candidate:
            digits = normalize_phone_from_jid(candidate)
            if digits and len(digits) >= 10:
                phone = digits
                send_jid = candidate if "@" in candidate else f"{digits}@s.whatsapp.net"
                break

    if not phone and remote_jid:
        # fallback: conversa só com LID — ainda assim guardamos o jid para reply
        phone = normalize_phone_from_jid(remote_jid) or normalize_digits(remote_jid.split("@")[0])
        send_jid = remote_jid

    return phone, send_jid


def _number_candidates(phone: str, jid: str = "") -> list[str]:
    out: list[str] = []
    jid = normalize_text(jid)
    if jid:
        out.append(jid)
        if "@" in jid:
            out.append(jid.split("@", 1)[0])

    number = normalize_digits(phone)
    if number:
        if not number.startswith("55") and len(number) >= 10:
            number = f"55{number}"
        out.append(number)
        out.append(f"{number}@s.whatsapp.net")
        out.append(f"{number}@c.us")
        if number.startswith("55") and len(number) == 12:
            with_nine = number[:4] + "9" + number[4:]
            out.append(with_nine)
            out.append(f"{with_nine}@s.whatsapp.net")
        if number.startswith("55") and len(number) == 13 and number[4] == "9":
            without_nine = number[:4] + number[5:]
            out.append(without_nine)
            out.append(f"{without_nine}@s.whatsapp.net")

    unique: list[str] = []
    for item in out:
        value = normalize_text(item)
        if value and value not in unique:
            unique.append(value)
    return unique


def enrich_targets_from_chats(phone: str, jid: str = "") -> list[str]:
    targets = _number_candidates(phone, jid)
    phone_digits = normalize_digits(phone)
    needle = normalize_text(jid)
    try:
        for url in _instance_urls("/chat/findChats"):
            response = requests.get(url, headers=_headers(), timeout=25)
            if response.status_code >= 400:
                continue
            data = _parse_json(response)
            chats = data if isinstance(data, list) else (
                data.get("data") or data.get("chats") or data.get("response") or []
            )
            if not isinstance(chats, list):
                continue
            matched: list[str] = []
            for chat in chats:
                if not isinstance(chat, dict):
                    continue
                cid = normalize_text(
                    chat.get("id")
                    or chat.get("remoteJid")
                    or _dig_chat_jid(chat)
                    or ""
                )
                if not cid or cid.endswith("@g.us") or "broadcast" in cid:
                    continue
                digits = normalize_digits(cid.split("@", 1)[0])
                if needle and (cid == needle or needle in cid or cid in needle):
                    matched.append(cid)
                elif phone_digits and digits and phone_digits[-8:] == digits[-8:]:
                    matched.append(cid)
            if matched:
                return list(dict.fromkeys(matched + targets))
            break
    except Exception as error:
        logger.warning("findChats falhou: %s", error)
    return targets


def _dig_chat_jid(chat: dict) -> str:
    for key in ("remoteJid", "jid", "chatId"):
        if chat.get(key):
            return normalize_text(chat.get(key))
    last = chat.get("lastMessage") if isinstance(chat.get("lastMessage"), dict) else {}
    key = last.get("key") if isinstance(last.get("key"), dict) else {}
    return normalize_text(key.get("remoteJid") or "")


def get_connection_state() -> str:
    last_error = ""
    for url in _instance_urls("/instance/connectionState"):
        try:
            response = requests.get(url, headers=_headers(), timeout=15)
        except requests.RequestException as error:
            last_error = str(error)
            continue
        data = _parse_json(response)
        if response.status_code >= 400:
            last_error = _response_looks_like_error(data) or response.text[:200]
            continue
        state = ""
        if isinstance(data.get("instance"), dict):
            state = normalize_text(data["instance"].get("state") or data["instance"].get("status"))
        state = state or normalize_text(data.get("state") or data.get("status"))
        return state.lower()
    if last_error:
        logger.warning("connectionState falhou: %s", last_error)
    return ""


def assert_instance_ready() -> None:
    state = get_connection_state()
    if not state:
        # não bloqueia se o endpoint não existir em algumas versões
        return
    if state not in {"open", "connected"}:
        raise EvolutionClientError(
            f"Instância Evolution não está conectada (estado: {state}). "
            "Reconecte o QR no Manager e tente de novo."
        )


def _text_payloads(number: str, body: str) -> list[dict[str, Any]]:
    return [
        {"number": number, "text": body},
        {"number": number, "textMessage": {"text": body}},
        {
            "number": number,
            "textMessage": {"text": body},
            "options": {"delay": 0, "presence": "composing", "linkPreview": False},
        },
    ]


def send_text(phone: str, text: str, *, jid: str = "") -> dict[str, Any]:
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    body = str(text or "").strip()
    if not body:
        raise EvolutionClientError("Mensagem vazia.")

    assert_instance_ready()

    numbers = enrich_targets_from_chats(phone, jid)
    if not numbers:
        raise EvolutionClientError("Telefone/JID da conversa inválido para envio.")

    urls = _instance_urls("/message/sendText")
    errors: list[str] = []

    for number in numbers:
        for url in urls:
            for payload in _text_payloads(number, body):
                try:
                    response = requests.post(url, json=payload, headers=_headers(), timeout=30)
                except requests.RequestException as error:
                    errors.append(f"{number}: {error}")
                    continue

                data = _parse_json(response)
                err = _response_looks_like_error(data)
                if response.status_code >= 400 or err:
                    errors.append(
                        f"{number} HTTP {response.status_code}: {err or response.text[:180]}"
                    )
                    continue

                msg_id = extract_message_id(data)
                if not msg_id:
                    errors.append(
                        f"{number}: Evolution respondeu sem ID de mensagem: {str(data)[:180]}"
                    )
                    continue

                logger.info(
                    "Evolution sendText ok instance=%s number=%s id=%s",
                    resolved_instance_name(),
                    number,
                    msg_id,
                )
                return data

    detail = " | ".join(errors[-4:]) if errors else "sem detalhes"
    raise EvolutionClientError(
        "Não foi possível enviar no WhatsApp via Evolution. "
        f"Instância={resolved_instance_name()}. {detail}"
    )


def send_media(
    phone: str,
    *,
    media_url: str,
    media_type: str = "image",
    caption: str = "",
    filename: str = "",
    mimetype: str = "",
    jid: str = "",
) -> dict[str, Any]:
    """Envia mídia via Evolution (image/document/audio)."""
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    assert_instance_ready()
    numbers = enrich_targets_from_chats(phone, jid)
    if not numbers:
        raise EvolutionClientError("Telefone/JID da conversa inválido para envio.")
    mediatype = {
        "image": "image",
        "document": "document",
        "audio": "audio",
        "video": "video",
    }.get(media_type, "document")
    errors: list[str] = []
    for number in numbers:
        payload = {
            "number": number,
            "mediatype": mediatype,
            "media": media_url,
            "caption": str(caption or "").strip(),
            "fileName": normalize_text(filename) or "arquivo",
        }
        if mimetype:
            payload["mimetype"] = mimetype
        for url in _instance_urls("/message/sendMedia"):
            try:
                response = requests.post(url, json=payload, headers=_headers(), timeout=60)
            except requests.RequestException as error:
                errors.append(str(error))
                continue
            data = _parse_json(response)
            err = _response_looks_like_error(data)
            if response.status_code >= 400 or err:
                errors.append(err or response.text[:180])
                continue
            if extract_message_id(data):
                return data
            errors.append(f"sem ID: {str(data)[:160]}")
    raise EvolutionClientError(
        "Falha ao enviar mídia via Evolution. " + (" | ".join(errors[-3:]) if errors else "")
    )
