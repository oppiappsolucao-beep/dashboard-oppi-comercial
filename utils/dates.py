from datetime import date, datetime

import pandas as pd


def today() -> date:
    return date.today()


def format_date(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", ""))
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def format_datetime(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", ""))
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def start_of_month(ref: date | None = None) -> date:
    ref = ref or today()
    return ref.replace(day=1)


def end_of_month(ref: date | None = None) -> date:
    ref = ref or today()
    return (pd.Timestamp(ref) + pd.offsets.MonthEnd(0)).date()
