"""Cobranças do Mercado Pago: boleto, cartão anual e assinatura de Pix Automático."""
from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_API = "https://api.mercadopago.com"
_TZ = ZoneInfo("America/Sao_Paulo")


class MercadoPagoError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.mercado_pago_access_token)


def payer_names(empresa: str) -> tuple[str, str]:
    parts = [part for part in (empresa or "").split() if part]
    if not parts:
        return "Cliente", "Oppi"
    if len(parts) == 1:
        return parts[0][:40], "LTDA"
    return parts[0][:40], " ".join(parts[1:])[:40]


def _mp_timestamp(moment: datetime) -> str:
    """O Mercado Pago só aceita data com milissegundos e Z, como 2026-11-16T12:00:00.000Z."""
    utc = moment.astimezone(ZoneInfo("UTC"))
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _start_at(due: str) -> str:
    try:
        day = date.fromisoformat(due[:10])
    except ValueError:
        day = date.today() + timedelta(days=1)
    start = datetime(day.year, day.month, day.day, 12, 0, tzinfo=_TZ)
    earliest = datetime.now(_TZ) + timedelta(hours=2)
    if start <= earliest:
        start = earliest
    return _mp_timestamp(start)


def _boleto_expiration(due: str) -> str:
    try:
        day = date.fromisoformat(due[:10])
    except ValueError:
        day = date.today() + timedelta(days=3)
    if day <= date.today():
        day = date.today() + timedelta(days=1)
    return _mp_timestamp(datetime(day.year, day.month, day.day, 21, 0, tzinfo=_TZ))


def build_boleto_body(
    *,
    amount: float,
    description: str,
    due: str,
    email: str,
    empresa: str,
    cnpj: str,
    sheet_row: int,
    notification_url: str,
) -> dict[str, Any]:
    first, last = payer_names(empresa)
    return {
        "transaction_amount": round(float(amount), 2),
        "description": description[:200],
        "payment_method_id": "bolbradesco",
        "date_of_expiration": _boleto_expiration(due),
        "external_reference": f"crm-{int(sheet_row)}",
        "notification_url": notification_url,
        "payer": {
            "email": email,
            "first_name": first,
            "last_name": last,
            "identification": {"type": "CNPJ", "number": cnpj},
        },
    }


def build_preference_body(
    *,
    amount: float,
    description: str,
    email: str,
    sheet_row: int,
    notification_url: str,
    back_url: str,
    excluded_types: list[str],
) -> dict[str, Any]:
    return {
        "items": [
            {
                "title": description[:120] or "Plano Oppi",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": round(float(amount), 2),
            }
        ],
        "payer": {"email": email},
        "external_reference": f"crm-{int(sheet_row)}",
        "notification_url": notification_url,
        "payment_methods": {
            "excluded_payment_types": [{"id": item} for item in excluded_types],
            "installments": 1,
        },
        "back_urls": {"success": back_url, "pending": back_url, "failure": back_url},
        "auto_return": "approved",
    }


def build_card_preference_body(**kwargs) -> dict[str, Any]:
    return build_preference_body(excluded_types=["ticket", "bank_transfer", "atm"], **kwargs)


def build_pix_boleto_preference_body(**kwargs) -> dict[str, Any]:
    return build_preference_body(excluded_types=["credit_card", "debit_card"], **kwargs)


def build_pix_body(
    *,
    amount: float,
    description: str,
    due: str,
    email: str,
    empresa: str,
    cnpj: str,
    sheet_row: int,
    notification_url: str,
) -> dict[str, Any]:
    first, last = payer_names(empresa)
    payer: dict[str, Any] = {"email": email, "first_name": first, "last_name": last}
    if len(cnpj) == 14:
        payer["identification"] = {"type": "CNPJ", "number": cnpj}
    return {
        "transaction_amount": round(float(amount), 2),
        "description": description[:200],
        "payment_method_id": "pix",
        "date_of_expiration": _boleto_expiration(due),
        "external_reference": f"crm-{int(sheet_row)}",
        "notification_url": notification_url,
        "payer": payer,
    }


def build_pix_subscription_body(
    *,
    amount: float,
    description: str,
    email: str,
    due: str,
    ciclo: str,
    sheet_row: int,
    notification_url: str,
    back_url: str,
) -> dict[str, Any]:
    frequency = 12 if ciclo == "anual" else 1
    return {
        "reason": description[:200] or "Assinatura Oppi",
        "external_reference": f"crm-{int(sheet_row)}",
        "payer_email": email,
        "back_url": back_url,
        "status": "pending",
        "auto_recurring": {
            "frequency": frequency,
            "frequency_type": "months",
            "start_date": _start_at(due),
            "transaction_amount": round(float(amount), 2),
            "currency_id": "BRL",
        },
        "notification_url": notification_url,
    }


def charge_link(payload: dict) -> str:
    data = payload if isinstance(payload, dict) else {}
    point = data.get("point_of_interaction") if isinstance(data.get("point_of_interaction"), dict) else {}
    ticket = point.get("transaction_data") if isinstance(point.get("transaction_data"), dict) else {}
    details = data.get("transaction_details") if isinstance(data.get("transaction_details"), dict) else {}
    for value in (
        data.get("init_point"),
        data.get("sandbox_init_point"),
        ticket.get("ticket_url"),
        details.get("external_resource_url"),
        data.get("transaction_details", {}).get("external_resource_url") if isinstance(data.get("transaction_details"), dict) else "",
    ):
        text = str(value or "").strip()
        if text.startswith("http"):
            return text
    return ""


def sheet_row_from_reference(value: str) -> int:
    text = str(value or "").strip()
    if not text.startswith("crm-"):
        return 0
    try:
        row = int(text.split("-", 1)[1])
    except (TypeError, ValueError):
        return 0
    return row if row > 0 else 0


def verify_webhook_signature(
    *,
    secret: str,
    data_id: str,
    request_id: str,
    signature_header: str,
) -> bool:
    if not secret:
        return True
    parts: dict[str, str] = {}
    for piece in (signature_header or "").split(","):
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        parts[key.strip()] = value.strip()
    ts = parts.get("ts") or ""
    expected = parts.get("v1") or ""
    if not ts or not expected:
        return False
    resource_id = (data_id or "").strip()
    if resource_id.isalnum():
        resource_id = resource_id.lower()
    manifest = f"id:{resource_id};request-id:{request_id or ''};ts:{ts};"
    digest = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.mercado_pago_access_token}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4()),
    }


def _request(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    if not is_configured():
        raise MercadoPagoError("Mercado Pago não está configurado. Defina MERCADO_PAGO_ACCESS_TOKEN no Easypanel.")
    url = f"{_API}/{path.lstrip('/')}"
    try:
        response = requests.request(method, url, headers=_headers(), json=body, timeout=25)
    except requests.RequestException as exc:
        raise MercadoPagoError(f"Falha ao conectar no Mercado Pago: {exc}") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if response.status_code == 401:
        raise MercadoPagoError("Mercado Pago recusou o Access Token. Confira MERCADO_PAGO_ACCESS_TOKEN no Easypanel.")
    if not response.ok:
        cause = data.get("cause")
        detail = data.get("message") or ""
        if isinstance(cause, list) and cause and isinstance(cause[0], dict):
            detail = cause[0].get("description") or cause[0].get("message") or detail
        raise MercadoPagoError(f"Mercado Pago HTTP {response.status_code}: {detail or (response.text or '')[:180]}")
    return data


def create_payment(body: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/v1/payments", body)


def create_boleto(body: dict[str, Any]) -> dict[str, Any]:
    return create_payment(body)


def create_card_link(body: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/checkout/preferences", body)


def create_pix_subscription(body: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/preapproval", body)


def get_payment(payment_id: str) -> dict[str, Any]:
    return _request("GET", f"/v1/payments/{payment_id}")


def get_subscription(preapproval_id: str) -> dict[str, Any]:
    return _request("GET", f"/preapproval/{preapproval_id}")
