"""Metas mensais configuráveis pelo administrador."""
import json
import threading
from pathlib import Path

from gspread.exceptions import WorksheetNotFound

from app.config import settings
from app.services.legacy_core import get_gsheet_client, normalize_text

TEAM_SELLER_LABEL = "Todos os vendedores"
GOALS_WORKSHEET = "Metas"
GOALS_HEADERS = ["Ano", "Mes", "Vendedor", "Meta"]

_lock = threading.Lock()
_file_cache: dict | None = None


def _goals_file_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "storage" / "monthly_goals.json"


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


def _empty_store() -> dict[str, float]:
    return {}


def _load_from_file() -> dict[str, float]:
    path = _goals_file_path()
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    result = {}
    for key, value in data.items():
        amount = _parse_amount(value)
        if amount is not None:
            result[str(key)] = amount
    return result


def _save_to_file(store: dict[str, float]) -> None:
    path = _goals_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_from_sheet() -> dict[str, float] | None:
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
        store[_goal_key(year, month, seller)] = amount
    return store


def _save_to_sheet(store: dict[str, float]) -> bool:
    if not settings.sheets_configured:
        return False

    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        try:
            worksheet = spreadsheet.worksheet(GOALS_WORKSHEET)
        except WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(GOALS_WORKSHEET, rows=200, cols=4)

        rows = [GOALS_HEADERS]
        for key in sorted(store.keys()):
            year, month, seller = _parse_goal_key(key)
            amount = store[key]
            amount_text = str(int(amount)) if float(amount).is_integer() else str(amount)
            rows.append([str(year), str(month), seller, amount_text])

        worksheet.clear()
        worksheet.update(rows, value_input_option="USER_ENTERED")
        return True
    except Exception:
        return False


def load_monthly_goals(force_refresh: bool = False) -> dict[str, float]:
    global _file_cache
    with _lock:
        if not force_refresh and _file_cache is not None:
            return dict(_file_cache)

        file_store = _load_from_file()
        sheet_store = _load_from_sheet()

        if sheet_store is not None:
            merged = {**file_store, **sheet_store}
            if merged != file_store:
                _save_to_file(merged)
        else:
            merged = file_store

        _file_cache = merged
        return dict(merged)


def invalidate_monthly_goals_cache() -> None:
    global _file_cache
    with _lock:
        _file_cache = None


def get_monthly_goal(year: int, month: int, seller: str = TEAM_SELLER_LABEL) -> float | None:
    store = load_monthly_goals()
    amount = store.get(_goal_key(year, month, _normalize_seller(seller)))
    if amount is None:
        return None
    return float(amount)


def set_monthly_goal(year: int, month: int, amount: float, seller: str = TEAM_SELLER_LABEL) -> None:
    if month < 1 or month > 12:
        raise ValueError("Informe um mês válido.")
    if amount < 0:
        raise ValueError("A meta precisa ser zero ou maior.")

    seller_name = _normalize_seller(seller)
    store = load_monthly_goals()
    store[_goal_key(year, month, seller_name)] = float(amount)

    _save_to_file(store)
    _save_to_sheet(store)

    with _lock:
        global _file_cache
        _file_cache = dict(store)


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
    _save_to_sheet(store)

    with _lock:
        global _file_cache
        _file_cache = dict(store)


def list_monthly_goals(limit: int = 12) -> list[dict]:
    store = load_monthly_goals()
    rows = []
    for key, amount in store.items():
        try:
            year, month, seller = _parse_goal_key(key)
        except ValueError:
            continue
        rows.append({
            "year": year,
            "month": month,
            "seller": seller,
            "amount": amount,
            "amount_label": f"R$ {amount:,.0f}".replace(",", "."),
            "sort_key": (year, month, seller.lower()),
        })
    rows.sort(key=lambda item: item["sort_key"], reverse=True)
    return rows[:limit]
