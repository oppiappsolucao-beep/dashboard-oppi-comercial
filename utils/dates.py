from datetime import date, datetime

import pandas as pd


def today() -> date:
    return date.today()


def _is_missing(value) -> bool:
    if value is None or value == "":
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if type(value).__name__ in {"NaTType", "NaT"}:
        return True
    return False


def format_date(value) -> str:
    # NaT é truthy e isinstance(datetime) em alguns pandas — não usar `if not value`.
    if _is_missing(value):
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", ""))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def format_datetime(value) -> str:
    if _is_missing(value):
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", ""))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def start_of_month(ref: date | None = None) -> date:
    ref = ref or today()
    return ref.replace(day=1)


def end_of_month(ref: date | None = None) -> date:
    ref = ref or today()
    return (pd.Timestamp(ref) + pd.offsets.MonthEnd(0)).date()
