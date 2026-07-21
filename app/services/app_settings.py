"""Configurações administrativas persistidas localmente e na planilha."""
import json
import logging
import threading
from pathlib import Path

from app.config import settings
from app.services.legacy_core import normalize_text
from app.services.sheet_crm_storage import CRM_STORAGE_TABS, get_worksheet, header_indexes
from app.services.storage_paths import get_storage_dir

logger = logging.getLogger(__name__)

CONFIG_WORKSHEET = "Configuracoes"
CONFIG_HEADERS = CRM_STORAGE_TABS[CONFIG_WORKSHEET]
PERSISTED_CONFIG_KEYS = (
    "proposal_template_doc_id",
    "proposal_template_url",
    "proposal_pdf_folder_id",
    "commercial_services",
)

_lock = threading.Lock()
_cache: dict | None = None


def _settings_path() -> Path:
    return get_storage_dir() / "app_settings.json"


def invalidate_app_settings_cache() -> None:
    global _cache
    from app.services.sheet_read_cache import invalidate_worksheet_cache

    invalidate_worksheet_cache(CONFIG_WORKSHEET)
    with _lock:
        _cache = None


def _default_settings() -> dict:
    doc_id = normalize_text(getattr(settings, "proposal_template_doc_id", ""))
    return {
        "proposal_template_doc_id": doc_id,
        "proposal_template_url": (
            f"https://docs.google.com/document/d/{doc_id}/edit"
            if doc_id else ""
        ),
        "commercial_services": [],
        "proposal_pdf_folder_id": "",
    }


def _encode_config_value(key: str, value) -> str:
    if key == "commercial_services":
        return json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
    return normalize_text(value)


def _decode_config_value(key: str, value: str):
    text = normalize_text(value)
    if key == "commercial_services":
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return text


def _load_from_sheet(force_refresh: bool = False) -> dict | None:
    if not settings.sheets_configured:
        return None
    worksheet = get_worksheet(CONFIG_WORKSHEET)
    if worksheet is None:
        return None

    from app.services.sheet_read_cache import get_cached_worksheet_values

    try:
        rows = get_cached_worksheet_values(
            CONFIG_WORKSHEET,
            worksheet.get_all_values,
            force_refresh=force_refresh,
        )
    except Exception:
        return None
    if rows is None:
        return None
    if len(rows) < 2:
        return {}

    indexes = header_indexes(rows[0], CONFIG_HEADERS)
    if indexes is None:
        return None

    result: dict = {}
    for row in rows[1:]:
        if len(row) <= max(indexes.values()):
            continue
        key = normalize_text(row[indexes["Chave"]])
        if key not in PERSISTED_CONFIG_KEYS:
            continue
        result[key] = _decode_config_value(key, row[indexes["Valor"]])
    return result


def _save_to_sheet(values: dict) -> bool:
    if not settings.sheets_configured:
        return False
    worksheet = get_worksheet(CONFIG_WORKSHEET)
    if worksheet is None:
        return False
    try:
        rows = [CONFIG_HEADERS]
        for key in PERSISTED_CONFIG_KEYS:
            if key not in values:
                continue
            rows.append([key, _encode_config_value(key, values[key])])

        range_name = f"A1:B{len(rows)}"
        worksheet.batch_update(
            [{"range": range_name, "values": rows}],
            value_input_option="USER_ENTERED",
        )
        from app.services.sheet_read_cache import invalidate_worksheet_cache

        invalidate_worksheet_cache(CONFIG_WORKSHEET)
        return True
    except Exception as error:
        logger.exception("Falha ao salvar aba Configuracoes: %s", error)
        return False


def load_app_settings(force_refresh: bool = False) -> dict:
    global _cache
    with _lock:
        if not force_refresh and _cache is not None:
            return dict(_cache)

        defaults = _default_settings()
        path = _settings_path()
        stored: dict = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    stored = loaded
            except Exception:
                stored = {}

        merged = {**defaults, **stored}
        sheet_store = _load_from_sheet(force_refresh=force_refresh)
        if sheet_store is not None:
            merged.update(sheet_store)
            if merged != {**defaults, **stored}:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    pass
            if not sheet_store and {**defaults, **stored} != defaults:
                _save_to_sheet(merged)

        _cache = merged
        return dict(merged)


def save_app_settings(values: dict) -> None:
    global _cache
    invalidate_app_settings_cache()
    current = load_app_settings(force_refresh=True)
    current.update(values)
    path = _settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        raise OSError(
            f"Não foi possível salvar as configurações em {path}. "
            "Verifique permissões de escrita ou configure APP_STORAGE_DIR."
        ) from error
    if settings.sheets_configured and not _save_to_sheet(current):
        raise RuntimeError("Não foi possível salvar configurações na aba Configuracoes da planilha.")
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
