"""Cliente HTTP da API Asaas (cobranças e assinaturas)."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_TTL_SEC = 90.0
_MAX_PAGES = 8


class AsaasError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.asaas_api_key)


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE["at"] = 0.0
        _CACHE["payload"] = None


def _headers() -> dict[str, str]:
    return {
        "access_token": settings.asaas_api_key,
        "Content-Type": "application/json",
        "User-Agent": "OppiCRM-Financeiro/1.0",
    }


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not is_configured():
        raise AsaasError("ASAAS_API_KEY não configurada.")
    url = f"{settings.asaas_api_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = requests.get(url, headers=_headers(), params=params or {}, timeout=25)
    except requests.RequestException as exc:
        raise AsaasError(f"Falha ao conectar no Asaas: {exc}") from exc
    if response.status_code == 401:
        raise AsaasError("Asaas recusou a chave (401). Confira ASAAS_API_KEY no Easypanel.")
    if not response.ok:
        snippet = (response.text or "")[:180]
        raise AsaasError(f"Asaas HTTP {response.status_code}: {snippet}")
    try:
        data = response.json()
    except ValueError as exc:
        raise AsaasError("Asaas devolveu resposta inválida.") from exc
    return data if isinstance(data, dict) else {}


def _list(path: str, params: dict[str, Any] | None = None, *, max_pages: int = _MAX_PAGES) -> list[dict]:
    items: list[dict] = []
    offset = 0
    extra = dict(params or {})
    for _ in range(max_pages):
        extra["limit"] = 100
        extra["offset"] = offset
        payload = _get(path, extra)
        batch = payload.get("data") or []
        if isinstance(batch, list):
            items.extend([row for row in batch if isinstance(row, dict)])
        if not payload.get("hasMore"):
            break
        offset += 100
    return items


def test_connection() -> dict[str, Any]:
    if not is_configured():
        return {"ok": False, "message": "ASAAS_API_KEY não configurada."}
    try:
        _get("customers", {"limit": 1})
        return {"ok": True, "message": "Conectado ao Asaas."}
    except AsaasError as exc:
        return {"ok": False, "message": str(exc)}


def fetch_dashboard_payload(*, force: bool = False) -> dict[str, Any]:
    """Pagamentos + assinaturas + clientes (cache curto para não travar o worker)."""
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE["payload"]
        if not force and cached is not None and (now - float(_CACHE["at"] or 0)) < _CACHE_TTL_SEC:
            return cached

    customers = _list("customers")
    since = (date.today() - timedelta(days=240)).isoformat()
    payments = _list("payments", {"dueDate[ge]": since})
    subscriptions = _list("subscriptions")
    payload = {
        "customers": customers,
        "payments": payments,
        "subscriptions": subscriptions,
        "fetched_at": time.time(),
    }
    with _CACHE_LOCK:
        _CACHE["payload"] = payload
        _CACHE["at"] = time.monotonic()
    return payload


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    if not is_configured():
        raise AsaasError("ASAAS_API_KEY não configurada.")
    url = f"{settings.asaas_api_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = requests.post(url, headers=_headers(), json=body, timeout=25)
    except requests.RequestException as exc:
        raise AsaasError(f"Falha ao conectar no Asaas: {exc}") from exc
    if response.status_code == 401:
        raise AsaasError("Asaas recusou a chave (401). Confira ASAAS_API_KEY no Easypanel.")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not response.ok:
        errors = data.get("errors") if isinstance(data, dict) else None
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            detail = first.get("description") or first.get("message") or str(first)
        else:
            detail = (response.text or "")[:180]
        raise AsaasError(f"Asaas HTTP {response.status_code}: {detail}")
    return data if isinstance(data, dict) else {}


def find_customers(**params: Any) -> list[dict]:
    return _list("customers", {key: value for key, value in params.items() if value})


def create_customer(payload: dict[str, Any]) -> dict[str, Any]:
    data = _post("customers", payload)
    invalidate_cache()
    return data


def create_payment(payload: dict[str, Any]) -> dict[str, Any]:
    data = _post("payments", payload)
    invalidate_cache()
    return data


def create_subscription(payload: dict[str, Any]) -> dict[str, Any]:
    data = _post("subscriptions", payload)
    invalidate_cache()
    return data


def list_payments_for_customer(customer_id: str) -> list[dict]:
    if not customer_id:
        return []
    return _list("payments", {"customer": customer_id}, max_pages=4)
