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


def _instance_path(segment: str) -> str:
    """Nome da instância pode ter espaço (ex.: Oppi Comercial)."""
    name = normalize_text(settings.evolution_instance)
    if not name:
        raise EvolutionClientError("EVOLUTION_INSTANCE não configurada.")
    return f"{segment}/{quote(name, safe='')}"


def _ensure_ok_response(response: requests.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise EvolutionClientError(
            f"Evolution retornou {response.status_code}: {response.text[:300]}"
        )
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}
    if not isinstance(data, dict):
        return {"data": data}
    # Alguns erros vêm com HTTP 200
    status = normalize_text(data.get("status") or data.get("error") or "").lower()
    if status in {"error", "unauthorized", "forbidden"} or data.get("error"):
        message = data.get("message") or data.get("error") or data
        raise EvolutionClientError(f"Evolution recusou o envio: {message}")
    return data


def send_text(phone: str, text: str) -> dict[str, Any]:
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    # Preserva o texto exatamente (só trim nas pontas) — sem “correção”
    body = str(text or "").strip()
    if not body:
        raise EvolutionClientError("Mensagem vazia.")
    number = normalize_digits(phone)
    if number.startswith("55") and len(number) >= 12:
        pass
    elif len(number) >= 10:
        number = f"55{number}"
    payload = {
        "number": number,
        "text": body,
    }
    url = _url(_instance_path("/message/sendText"))
    try:
        response = requests.post(url, json=payload, headers=_headers(), timeout=30)
    except requests.RequestException as error:
        raise EvolutionClientError(f"Falha ao enviar mensagem: {error}") from error
    return _ensure_ok_response(response)


def send_media(
    phone: str,
    *,
    media_url: str,
    media_type: str = "image",
    caption: str = "",
    filename: str = "",
    mimetype: str = "",
) -> dict[str, Any]:
    """Envia mídia via Evolution (image/document/audio)."""
    if not is_configured():
        raise EvolutionClientError("Evolution API não configurada.")
    number = normalize_digits(phone)
    if not number.startswith("55") and len(number) >= 10:
        number = f"55{number}"
    mediatype = {
        "image": "image",
        "document": "document",
        "audio": "audio",
        "video": "video",
    }.get(media_type, "document")
    payload = {
        "number": number,
        "mediatype": mediatype,
        "media": media_url,
        "caption": str(caption or "").strip(),
        "fileName": normalize_text(filename) or "arquivo",
    }
    if mimetype:
        payload["mimetype"] = mimetype
    url = _url(_instance_path("/message/sendMedia"))
    try:
        response = requests.post(url, json=payload, headers=_headers(), timeout=60)
    except requests.RequestException as error:
        raise EvolutionClientError(f"Falha ao enviar mídia: {error}") from error
    return _ensure_ok_response(response)


def extract_message_id(response: dict | None) -> str:
    data = response or {}
    for key in ("key", "message", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            for candidate in ("id", "messageId", "message_id"):
                value = nested.get(candidate)
                if value:
                    return normalize_text(value)
            nested_key = nested.get("key")
            if isinstance(nested_key, dict) and nested_key.get("id"):
                return normalize_text(nested_key.get("id"))
    for candidate in ("id", "messageId", "message_id"):
        if data.get(candidate):
            return normalize_text(data.get(candidate))
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
