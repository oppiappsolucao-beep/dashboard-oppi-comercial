"""Configurações administrativas persistidas localmente."""
import json
import threading
from pathlib import Path

from app.config import settings
from app.services.legacy_core import normalize_text

_lock = threading.Lock()
_cache: dict | None = None


def _settings_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "storage" / "app_settings.json"


def _default_settings() -> dict:
    doc_id = normalize_text(getattr(settings, "proposal_template_doc_id", ""))
    return {
        "proposal_template_doc_id": doc_id,
        "proposal_template_url": (
            f"https://docs.google.com/document/d/{doc_id}/edit"
            if doc_id else ""
        ),
        "commercial_services": ["Oppi Vision", "Oppi Flow", "Oppi Track"],
    }


def load_app_settings(force_refresh: bool = False) -> dict:
    global _cache
    with _lock:
        if not force_refresh and _cache is not None:
            return dict(_cache)

        defaults = _default_settings()
        path = _settings_path()
        if not path.exists():
            _cache = defaults
            return dict(defaults)

        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            stored = {}

        merged = {**defaults, **(stored if isinstance(stored, dict) else {})}
        _cache = merged
        return dict(merged)


def save_app_settings(values: dict) -> None:
    global _cache
    current = load_app_settings()
    current.update(values)
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    with _lock:
        _cache = dict(current)


def extract_google_doc_id(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    match = __import__("re").search(r"/document/d/([a-zA-Z0-9_-]+)", text)
    if match:
        return match.group(1)
    if __import__("re").fullmatch(r"[a-zA-Z0-9_-]{20,}", text):
        return text
    return ""


def get_proposal_template_doc_id() -> str:
    data = load_app_settings()
    return normalize_text(data.get("proposal_template_doc_id")) or settings.proposal_template_doc_id


def get_proposal_pdf_folder_id() -> str:
    data = load_app_settings()
    return (
        normalize_text(data.get("proposal_pdf_folder_id"))
        or settings.proposal_pdf_folder_id
    )


def get_proposal_template_url() -> str:
    data = load_app_settings()
    url = normalize_text(data.get("proposal_template_url"))
    if url:
        return url
    doc_id = get_proposal_template_doc_id()
    if doc_id:
        return f"https://docs.google.com/document/d/{doc_id}/edit"
    return ""


def set_proposal_template(value: str) -> str:
    doc_id = extract_google_doc_id(value)
    if not doc_id:
        raise ValueError("Informe um link válido do Google Docs ou o ID do documento.")

    url = value if "docs.google.com" in value else f"https://docs.google.com/document/d/{doc_id}/edit"
    save_app_settings({
        "proposal_template_doc_id": doc_id,
        "proposal_template_url": url,
    })
    return doc_id
