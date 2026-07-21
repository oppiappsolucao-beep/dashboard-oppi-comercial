"""Metas mensais configuráveis pelo administrador."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from gspread.exceptions import WorksheetNotFound

from app.config import settings
from app.services.legacy_core import get_gsheet_client, normalize_text
from app.services.storage_paths import get_storage_dir

logger = logging.getLogger(__name__)

TEAM_SELLER_LABEL = "Todos os vendedores"
GOALS_WORKSHEET = "Metas"
GOALS_HEADERS = ["Ano", "Mes", "Vendedor", "Meta", "Comissao"]
DEFAULT_COMMISSION_RATE = 8.0

_lock = threading.Lock()
_file_cache: dict[str, dict] | None = None


def _goals_file_path() -> Path:
    return get_storage_dir() / "monthly_goals.json"


def _normalize_seller(value: str) -> str:
    text = normalize_text(value)
    return text or TEAM_SELLER_LABEL


def _goal_key(year: int, month: int, seller: str) -> str:
    return f"{int(year)}-{int(month):02d}|{_normalize_seller(seller)}"


def _parse_goal_key(key: str) -> tuple[int, int, str]:
    parts = key.split("|")
    if len(parts) == 2:
        period, seller = parts
        year_text, month_text = period.split("-", 1)
    elif len(parts) == 3:
        year_text, month_text, seller = parts
    else:
        raise ValueError(f"Chave de meta inválida: {key}")
    return int(year_text), int(month_text), seller


def _parse_amount(value) -> float | None:
    if value is None:
        return None
    text = normalize_text(value)
    if not text:
        return None
    text = text.replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        amount = float(text)
    except ValueError:
        return None
    return amount if amount >= 0 else None


def parse_commission_rate(value) -> float:
    text = normalize_text(value).replace("%", "").replace(",", ".")
    if not text:
        return DEFAULT_COMMISSION_RATE
    try:
        rate = float(text)
    except ValueError:
        raise ValueError("Informe uma comissão válida (ex.: 8 ou 8,5).")
    if rate < 0 or rate > 100:
        raise ValueError("A comissão deve estar entre 0% e 100%.")
    return rate


def _parse_commission_rate(value) -> float | None:
    try:
        return parse_commission_rate(value)
    except ValueError:
        return None


def _normalize_goal_record(value) -> dict | None:
    if isinstance(value, dict):
        amount = _parse_amount(value.get("amount"))
        if amount is None:
            return None
        rate = _parse_commission_rate(value.get("commission_rate"))
        if rate is None:
            rate = DEFAULT_COMMISSION_RATE
        return {"amount": amount, "commission_rate": rate}

    amount = _parse_amount(value)
    if amount is None:
        return None
    return {"amount": amount, "commission_rate": DEFAULT_COMMISSION_RATE}


def _empty_store() -> dict[str, dict]:
    return {}


def _load_from_file() -> dict[str, dict]:
    path = _goals_file_path()
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()

    result: dict[str, dict] = {}
    for key, value in data.items():
        record = _normalize_goal_record(value)
        if record is not None:
            result[str(key)] = record
    return result


def _save_to_file(store: dict[str, dict]) -> None:
    path = _goals_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_from_sheet() -> dict[str, dict] | None:
    if not settings.sheets_configured:
        return None

    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        worksheet = spreadsheet.worksheet(GOALS_WORKSHEET)
        rows = worksheet.get_all_values()
    except WorksheetNotFound:
        return None
    except Exception:
        return None

    if len(rows) < 2:
        return _empty_store()

    headers = [normalize_text(cell).lower() for cell in rows[0]]
    try:
        year_idx = headers.index("ano")
        month_idx = headers.index("mes")
        seller_idx = headers.index("vendedor")
        goal_idx = headers.index("meta")
    except ValueError:
        return None

    commission_idx = headers.index("comissao") if "comissao" in headers else None

    store = _empty_store()
    for row in rows[1:]:
        if len(row) <= max(year_idx, month_idx, seller_idx, goal_idx):
            continue
        try:
            year = int(normalize_text(row[year_idx]))
            month = int(normalize_text(row[month_idx]))
        except ValueError:
            continue
        seller = _normalize_seller(row[seller_idx])
        amount = _parse_amount(row[goal_idx])
        if amount is None:
            continue

        commission_rate = DEFAULT_COMMISSION_RATE
        if commission_idx is not None and len(row) > commission_idx:
            parsed_rate = _parse_commission_rate(row[commission_idx])
            if parsed_rate is not None:
                commission_rate = parsed_rate

        store[_goal_key(year, month, seller)] = {
            "amount": amount,
            "commission_rate": commission_rate,
        }
    return store


def _save_to_sheet(store: dict[str, dict]) -> bool:
    if not settings.sheets_configured:
        return False

    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        try:
            worksheet = spreadsheet.worksheet(GOALS_WORKSHEET)
        except WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(GOALS_WORKSHEET, rows=200, cols=len(GOALS_HEADERS))

        rows = [GOALS_HEADERS]
        for key in sorted(store.keys()):
            year, month, seller = _parse_goal_key(key)
            record = store[key]
            amount = record["amount"]
            amount_text = str(int(amount)) if float(amount).is_integer() else str(amount)
            commission_text = (
                str(int(record["commission_rate"]))
                if float(record["commission_rate"]).is_integer()
                else str(record["commission_rate"]).replace(".", ",")
            )
            rows.append([str(year), str(month), seller, amount_text, commission_text])

        range_name = f"A1:E{len(rows)}"
        worksheet.batch_update(
            [{"range": range_name, "values": rows}],
            value_input_option="USER_ENTERED",
        )

        existing_count = len(worksheet.get_all_values())
        if existing_count > len(rows):
            worksheet.batch_clear([f"A{len(rows) + 1}:E{existing_count}"])
        return True
    except Exception as error:
        logger.exception("Falha ao salvar aba Metas: %s", error)
        return False


def _get_goal_record(year: int, month: int, seller: str = TEAM_SELLER_LABEL) -> dict | None:
    store = load_monthly_goals()
    record = store.get(_goal_key(year, month, _normalize_seller(seller)))
    if not record:
        return None
    return dict(record)


def load_monthly_goals(force_refresh: bool = False) -> dict[str, dict]:
    global _file_cache
    with _lock:
        if not force_refresh and _file_cache is not None:
            return {key: dict(value) for key, value in _file_cache.items()}

        file_store = _load_from_file()
        sheet_store = _load_from_sheet()

        if sheet_store is not None:
            merged = {**file_store, **sheet_store}
            if merged != file_store:
                _save_to_file(merged)
            if not sheet_store and merged:
                _save_to_sheet(merged)
        else:
            merged = file_store

        _file_cache = merged
        return {key: dict(value) for key, value in merged}


def invalidate_monthly_goals_cache() -> None:
    global _file_cache
    with _lock:
        _file_cache = None


def get_monthly_goal(year: int, month: int, seller: str = TEAM_SELLER_LABEL) -> float | None:
    record = _get_goal_record(year, month, seller)
    if record is None:
        return None
    return float(record["amount"])


def get_monthly_goal_commission_rate(year: int, month: int, seller: str = TEAM_SELLER_LABEL) -> float:
    record = _get_goal_record(year, month, seller)
    if record is None:
        return DEFAULT_COMMISSION_RATE
    return float(record["commission_rate"])


def set_monthly_goal(
    year: int,
    month: int,
    amount: float,
    seller: str = TEAM_SELLER_LABEL,
    *,
    commission_rate: float | None = None,
) -> None:
    if month < 1 or month > 12:
        raise ValueError("Informe um mês válido.")
    if amount < 0:
        raise ValueError("A meta precisa ser zero ou maior.")

    rate = DEFAULT_COMMISSION_RATE if commission_rate is None else float(commission_rate)
    if rate < 0 or rate > 100:
        raise ValueError("A comissão deve estar entre 0% e 100%.")

    seller_name = _normalize_seller(seller)
    store = load_monthly_goals()
    store[_goal_key(year, month, seller_name)] = {
        "amount": float(amount),
        "commission_rate": rate,
    }

    _save_to_file(store)
    if settings.sheets_configured and not _save_to_sheet(store):
        raise RuntimeError("Não foi possível salvar a meta na aba Metas da planilha.")

    with _lock:
        global _file_cache
        _file_cache = {key: dict(value) for key, value in store.items()}


def delete_monthly_goal(year: int, month: int, seller: str = TEAM_SELLER_LABEL) -> None:
    if month < 1 or month > 12:
        raise ValueError("Informe um mês válido.")

    seller_name = _normalize_seller(seller)
    key = _goal_key(year, month, seller_name)
    store = load_monthly_goals()
    if key not in store:
        raise ValueError("Meta não encontrada.")

    del store[key]
    _save_to_file(store)
    if settings.sheets_configured and not _save_to_sheet(store):
        raise RuntimeError("Não foi possível atualizar a aba Metas da planilha.")

    with _lock:
        global _file_cache
        _file_cache = {key: dict(value) for key, value in store.items()}


def list_monthly_goals(limit: int = 12) -> list[dict]:
    store = load_monthly_goals()
    rows = []
    for key, record in store.items():
        try:
            year, month, seller = _parse_goal_key(key)
        except ValueError:
            continue
        amount = float(record["amount"])
        commission_rate = float(record.get("commission_rate", DEFAULT_COMMISSION_RATE))
        commission_label = (
            f"{int(commission_rate)}%"
            if float(commission_rate).is_integer()
            else f"{commission_rate:.1f}".replace(".", ",") + "%"
        )
        rows.append({
            "year": year,
            "month": month,
            "seller": seller,
            "amount": amount,
            "amount_label": f"R$ {amount:,.0f}".replace(",", "."),
            "commission_rate": commission_rate,
            "commission_label": commission_label,
            "sort_key": (year, month, seller.lower()),
        })
    rows.sort(key=lambda item: item["sort_key"], reverse=True)
    return rows[:limit]
