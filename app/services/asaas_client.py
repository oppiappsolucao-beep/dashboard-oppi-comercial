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
_STATEMENT_CACHE: dict[str, dict[str, Any]] = {}
_DUE_CACHE: dict[str, dict[str, Any]] = {}
_BALANCE_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_CACHE_TTL_SEC = 90.0
_MAX_PAGES = 8
_STATEMENT_MAX_PAGES = 20


class AsaasError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.asaas_api_key)


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE["at"] = 0.0
        _CACHE["payload"] = None
        _STATEMENT_CACHE.clear()
        _DUE_CACHE.clear()
        _BALANCE_CACHE["at"] = 0.0
        _BALANCE_CACHE["value"] = None


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


def peek_cached_payload() -> dict[str, Any] | None:
    """Devolve o cache local do Asaas sem fazer nova requisição HTTP."""
    with _CACHE_LOCK:
        payload = _CACHE.get("payload")
        return payload if isinstance(payload, dict) else None


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


def fetch_account_balance(*, force: bool = False) -> float | None:
    """Saldo atual da conta Asaas. None quando a consulta falha."""
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _BALANCE_CACHE.get("value")
        if not force and cached is not None and (now - float(_BALANCE_CACHE["at"] or 0)) < _CACHE_TTL_SEC:
            return float(cached)
    data = _get("finance/balance")
    try:
        balance = float(data.get("balance"))
    except (TypeError, ValueError):
        return None
    with _CACHE_LOCK:
        _BALANCE_CACHE["value"] = balance
        _BALANCE_CACHE["at"] = time.monotonic()
    return balance


def fetch_statement(start: date, finish: date, *, force: bool = False) -> list[dict]:
    """Extrato da conta Asaas no período (entradas e saídas que mexem no saldo)."""
    key = f"{start.isoformat()}|{finish.isoformat()}"
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _STATEMENT_CACHE.get(key)
        if (
            not force
            and isinstance(cached, dict)
            and (now - float(cached.get("at") or 0)) < _CACHE_TTL_SEC
        ):
            items = cached.get("items")
            return list(items) if isinstance(items, list) else []
    items = _list(
        "financialTransactions",
        {
            "startDate": start.isoformat(),
            "finishDate": finish.isoformat(),
            "order": "asc",
        },
        max_pages=_STATEMENT_MAX_PAGES,
    )
    with _CACHE_LOCK:
        _STATEMENT_CACHE[key] = {"at": time.monotonic(), "items": items}
    return items


def fetch_payments_due(
    start: date,
    finish: date,
    *,
    billing_type: str = "",
    force: bool = False,
) -> list[dict]:
    """Cobranças do Asaas com vencimento no intervalo."""
    kind = (billing_type or "").strip().upper()
    key = f"{start.isoformat()}|{finish.isoformat()}|{kind}"
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _DUE_CACHE.get(key)
        if (
            not force
            and isinstance(cached, dict)
            and (now - float(cached.get("at") or 0)) < _CACHE_TTL_SEC
        ):
            items = cached.get("items")
            return list(items) if isinstance(items, list) else []
    params: dict[str, Any] = {
        "dueDate[ge]": start.isoformat(),
        "dueDate[le]": finish.isoformat(),
    }
    if kind:
        params["billingType"] = kind
    items = _list("payments", params, max_pages=_STATEMENT_MAX_PAGES)
    with _CACHE_LOCK:
        _DUE_CACHE[key] = {"at": time.monotonic(), "items": items}
    return items


def list_payments_for_customer(customer_id: str) -> list[dict]:
    if not customer_id:
        return []
    return _list("payments", {"customer": customer_id}, max_pages=4)
