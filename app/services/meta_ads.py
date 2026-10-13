"""Espelho somente leitura do Meta Ads para validar campanhas de tráfego."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from app.config import settings

log = logging.getLogger(__name__)

_CACHE = None
_GRAPH = "https://graph.facebook.com/v26.0"
_CONVERSATION_ACTIONS = (
    "onsite_conversion.messaging_conversation_started_7d",
    "onsite_conversion.total_messaging_connection",
    "messaging_conversation_started_7d",
)


class MetaAdsError(RuntimeError):
    pass


def _account_id(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not digits:
        raise MetaAdsError("META_AD_ACCOUNT_ID precisa ser o número da conta de anúncios.")
    return f"act_{digits}"


def _parse_day(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise MetaAdsError("Use datas no formato AAAA-MM-DD.") from exc


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    return int(round(_num(value)))


def _action_value(actions: list | None, names: tuple[str, ...]) -> int:
    wanted = {name.lower() for name in names}
    total = 0
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        kind = str(action.get("action_type") or "").lower()
        if kind in wanted:
            total += _int(action.get("value"))
    return total


def _money(value: float, currency: str = "BRL") -> str:
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if (currency or "BRL").upper() == "BRL":
        return f"R$ {formatted}"
    return f"{currency} {formatted}"


def _qty(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _pct(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + "%"


def _row(raw: dict, currency: str = "BRL") -> dict:
    impressions = _int(raw.get("impressions"))
    clicks = _int(raw.get("clicks"))
    spend = _num(raw.get("spend"))
    conversations = _action_value(raw.get("actions"), _CONVERSATION_ACTIONS)
    ctr = _num(raw.get("ctr"))
    if not ctr and impressions:
        ctr = clicks / impressions * 100
    cost_per_conversation = spend / conversations if conversations else 0.0
    return {
        "campaign": raw.get("campaign_name") or "—",
        "adset": raw.get("adset_name") or "—",
        "ad": raw.get("ad_name") or "—",
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "ctr": ctr,
        "conversations": conversations,
        "cost_per_conversation": cost_per_conversation,
        "impressions_label": _qty(impressions),
        "clicks_label": _qty(clicks),
        "spend_label": _money(spend, currency),
        "ctr_label": _pct(ctr),
        "conversations_label": _qty(conversations),
        "cost_label": _money(cost_per_conversation, currency) if conversations else "—",
    }


def _totals(rows: list[dict], currency: str = "BRL") -> dict:
    impressions = sum(row["impressions"] for row in rows)
    clicks = sum(row["clicks"] for row in rows)
    spend = sum(row["spend"] for row in rows)
    conversations = sum(row["conversations"] for row in rows)
    ctr = (clicks / impressions * 100) if impressions else 0.0
    cost = spend / conversations if conversations else 0.0
    return {
        "impressions_label": _qty(impressions),
        "clicks_label": _qty(clicks),
        "spend_label": _money(spend, currency),
        "ctr_label": _pct(ctr),
        "conversations_label": _qty(conversations),
        "cost_label": _money(cost, currency) if conversations else "—",
        "ads_label": _qty(len(rows)),
    }


def _meta_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        message = ((payload or {}).get("error") or {}).get("message") or ""
    except Exception:
        message = ""
    message = str(message).strip()
    if response.status_code == 190 or "access token" in message.lower():
        return "O token do Meta expirou ou não tem permissão de leitura dos anúncios."
    if message:
        return message
    return f"O Meta respondeu com erro {response.status_code}."


def _cache():
    global _CACHE
    if _CACHE is None:
        from cachetools import TTLCache

        _CACHE = TTLCache(maxsize=24, ttl=300)
    return _CACHE


def _get(path: str, params: dict) -> dict:
    import requests

    url = path if path.startswith("http") else f"{_GRAPH}/{path}"
    try:
        response = requests.get(url, params=params, timeout=25)
    except requests.RequestException as exc:
        raise MetaAdsError(f"Não consegui falar com o Meta: {exc}") from exc
    if response.status_code >= 400:
        raise MetaAdsError(_meta_message(response))
    try:
        return response.json()
    except Exception as exc:
        raise MetaAdsError("O Meta devolveu uma resposta que não é JSON.") from exc


def _insights(account: str, token: str, start: str, end: str) -> list[dict]:
    params = {
        "access_token": token,
        "level": "ad",
        "time_range": json.dumps({"since": start, "until": end}),
        "fields": "campaign_name,adset_name,ad_name,impressions,clicks,spend,ctr,actions",
        "limit": "200",
    }
    rows: list[dict] = []
    path = f"{account}/insights"
    for _ in range(4):
        payload = _get(path, params)
        rows.extend(item for item in (payload.get("data") or []) if isinstance(item, dict))
        next_url = ((payload.get("paging") or {}).get("next") or "").strip()
        if not next_url:
            break
        path = next_url
        params = {}
    return rows


def _account(account: str, token: str) -> tuple[str, str]:
    payload = _get(account, {"access_token": token, "fields": "name,currency"})
    name = str(payload.get("name") or "").strip() or account
    currency = str(payload.get("currency") or "BRL").strip() or "BRL"
    return name, currency


def load_campaign_mirror(start: str, end: str) -> dict:
    start = _parse_day(start)
    end = _parse_day(end)
    if start > end:
        start, end = end, start
    if not settings.meta_configured:
        return {
            "configured": False,
            "error": "",
            "account_name": "",
            "rows": [],
            "totals": _totals([]),
            "start": start,
            "end": end,
        }

    account = _account_id(settings.meta_ad_account_id)
    cache_key = (account, start, end)
    cached = _cache().get(cache_key)
    if cached is not None:
        return cached

    try:
        account_name, currency = _account(account, settings.meta_access_token)
        raw_rows = _insights(account, settings.meta_access_token, start, end)
        rows = [_row(item, currency) for item in raw_rows]
        rows.sort(key=lambda item: (item["conversations"], item["spend"]), reverse=True)
        result = {
            "configured": True,
            "error": "",
            "account_name": account_name,
            "rows": rows,
            "totals": _totals(rows, currency),
            "start": start,
            "end": end,
        }
    except MetaAdsError as exc:
        log.warning("Espelho Meta: %s", exc)
        result = {
            "configured": True,
            "error": str(exc),
            "account_name": "",
            "rows": [],
            "totals": _totals([]),
            "start": start,
            "end": end,
        }
    if not result.get("error"):
        _cache()[cache_key] = result
    return result
