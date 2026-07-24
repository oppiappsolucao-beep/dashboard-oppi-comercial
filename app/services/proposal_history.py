"""Histórico local de propostas geradas (não substitui registros anteriores)."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.legacy_core import normalize_text
from app.services.storage_paths import get_storage_dir

_lock = threading.Lock()
_TZ = ZoneInfo("America/Sao_Paulo")


def _history_path() -> Path:
    return get_storage_dir() / "proposal_history.json"


def _now() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def load_proposal_history(company: str | None = None, limit: int = 50) -> list[dict]:
    path = _history_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    rows = [item for item in data if isinstance(item, dict)]
    if company:
        key = normalize_text(company).lower()
        rows = [item for item in rows if normalize_text(item.get("cliente")).lower() == key]
    rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return rows[: max(1, int(limit))]


def save_proposal_history_entry(entry: dict) -> dict:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        rows = load_proposal_history(limit=5000)
        record = {
            "id": normalize_text(entry.get("id")) or str(uuid.uuid4()),
            "created_at": normalize_text(entry.get("created_at")) or _now().isoformat(timespec="seconds"),
            "date": normalize_text(entry.get("date")) or _now().strftime("%d/%m/%Y"),
            "time": normalize_text(entry.get("time")) or _now().strftime("%H:%M"),
            "cliente": normalize_text(entry.get("cliente")),
            "cnpj_cpf": normalize_text(entry.get("cnpj_cpf")),
            "colaboradores": int(entry.get("colaboradores") or 0),
            "quantidade_adicional": int(entry.get("quantidade_adicional") or 0),
            "plano": normalize_text(entry.get("plano")),
            "forma_pagamento": normalize_text(entry.get("forma_pagamento")),
            "valor_mensal": normalize_text(entry.get("valor_mensal")),
            "valor_anual": normalize_text(entry.get("valor_anual")),
            "valor_final": normalize_text(entry.get("valor_final")),
            "validade": normalize_text(entry.get("validade")),
            "usuario": normalize_text(entry.get("usuario")),
            "filename": normalize_text(entry.get("filename")),
            "pdf_cache_key": normalize_text(entry.get("pdf_cache_key")),
            "preview_url": normalize_text(entry.get("preview_url")),
            "download_url": normalize_text(entry.get("download_url")),
            "snapshot": entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {},
        }
        rows.insert(0, record)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return record


def delete_proposal_history_entry(entry_id: str) -> bool:
    entry_id = normalize_text(entry_id)
    if not entry_id:
        return False
    path = _history_path()
    with _lock:
        rows = load_proposal_history(limit=5000)
        filtered = [item for item in rows if normalize_text(item.get("id")) != entry_id]
        if len(filtered) == len(rows):
            return False
        path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
        return True


def get_proposal_history_entry(entry_id: str) -> dict | None:
    entry_id = normalize_text(entry_id)
    for item in load_proposal_history(limit=5000):
        if normalize_text(item.get("id")) == entry_id:
            return item
    return None
