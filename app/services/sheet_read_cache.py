"""Cache de leituras da planilha para reduzir quota da API Google Sheets."""
from __future__ import annotations

import threading
import time

from cachetools import TTLCache

from app.config import settings

_lock = threading.Lock()
_cache: TTLCache = TTLCache(maxsize=64, ttl=max(30, settings.cache_ttl_seconds))
_last_good: dict[str, list[list[str]]] = {}
_tabs_ensured_at: float = 0.0
TABS_ENSURE_INTERVAL_SECONDS = 300


def worksheet_cache_key(tab_name: str) -> str:
    return f"{settings.sheet_id}:{normalize_tab(tab_name)}"


def normalize_tab(tab_name: str) -> str:
    return str(tab_name or "").strip()


def get_cached_worksheet_values(tab_name: str, loader, *, force_refresh: bool = False) -> list[list[str]] | None:
    """Carrega valores da aba com cache TTL. `loader` deve chamar worksheet.get_all_values()."""
    key = worksheet_cache_key(tab_name)
    if not force_refresh:
        with _lock:
            cached = _cache.get(key)
            if cached is not None:
                return [row[:] for row in cached]

    try:
        values = loader()
    except Exception as exc:
        text = str(exc).lower()
        if "429" in text or "quota" in text:
            raise
        with _lock:
            previous = _last_good.get(key)
        if previous is not None:
            return [row[:] for row in previous]
        return None

    with _lock:
        _cache[key] = values
        _last_good[key] = values
    return [row[:] for row in values]


def peek_fresh_worksheet_values(tab_name: str) -> list[list[str]] | None:
    """Cache curto ainda válido. Não chama o Google."""
    key = worksheet_cache_key(tab_name)
    with _lock:
        cached = _cache.get(key)
    if cached is None:
        return None
    return [row[:] for row in cached]


def peek_cached_worksheet_values(tab_name: str) -> list[list[str]] | None:
    """Última leitura boa, mesmo depois que o cache curto foi limpo."""
    key = worksheet_cache_key(tab_name)
    with _lock:
        cached = _cache.get(key)
        if cached is None:
            cached = _last_good.get(key)
    if cached is None:
        return None
    return [row[:] for row in cached]


def invalidate_worksheet_cache(tab_name: str | None = None) -> None:
    with _lock:
        if tab_name:
            _cache.pop(worksheet_cache_key(tab_name), None)
            return
        _cache.clear()


def should_skip_ensure_tabs(force: bool = False) -> bool:
    if force:
        return False
    with _lock:
        return (time.time() - _tabs_ensured_at) < TABS_ENSURE_INTERVAL_SECONDS


def mark_tabs_ensured() -> None:
    global _tabs_ensured_at
    with _lock:
        _tabs_ensured_at = time.time()
