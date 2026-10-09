"""Contas a pagar da empresa — controle mensal por dia, independente do Asaas."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.crm_local_db import _connect, _lock, init_crm_local_db
from app.services.legacy_core import normalize_text

_TZ = ZoneInfo("America/Sao_Paulo")


def _today() -> date:
    return datetime.now(_TZ).date()


def parse_money(raw: str) -> float | None:
    text = normalize_text(raw).replace("R$", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    if value <= 0:
        return None
    return round(value, 2)


def _parse_due(value: str | None) -> date | None:
    raw = normalize_text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def list_payables(start: date, end: date) -> list[dict[str, Any]]:
    init_crm_local_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, description, amount, due_date, paid_on, created_at
            FROM company_payables
            WHERE due_date >= ? AND due_date <= ?
            ORDER BY due_date ASC, id ASC
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    out = []
    for row in rows:
        due = _parse_due(row["due_date"])
        if not due:
            continue
        paid_on = _parse_due(row["paid_on"])
        out.append(
            {
                "id": int(row["id"]),
                "description": normalize_text(row["description"]) or "Conta",
                "amount": float(row["amount"] or 0),
                "due": due,
                "paid": bool(paid_on),
                "paid_on": paid_on,
            }
        )
    return out


def create_payable(description: str, amount: float, due: date) -> int:
    init_crm_local_db()
    now = datetime.now(_TZ).isoformat(timespec="seconds")
    with _lock, _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO company_payables (description, amount, due_date, paid_on, created_at)
            VALUES (?, ?, ?, '', ?)
            """,
            (normalize_text(description) or "Conta", round(float(amount), 2), due.isoformat(), now),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)


def mark_payable_paid(payable_id: int, paid_on: date | None = None) -> bool:
    init_crm_local_db()
    when = (paid_on or _today()).isoformat()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "UPDATE company_payables SET paid_on = ? WHERE id = ?",
            (when, int(payable_id)),
        )
        conn.commit()
        return cursor.rowcount > 0


def reopen_payable(payable_id: int) -> bool:
    init_crm_local_db()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "UPDATE company_payables SET paid_on = '' WHERE id = ?",
            (int(payable_id),),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_payable(payable_id: int) -> bool:
    init_crm_local_db()
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM company_payables WHERE id = ?", (int(payable_id),))
        conn.commit()
        return cursor.rowcount > 0


def payable_calendar(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    """Grade diária no espírito da planilha: cada dia com a pagar e pago."""
    span = (end - start).days
    if span < 0 or span > 45:
        return {
            "show_grid": False,
            "days": [],
            "a_pagar": 0.0,
            "pago": 0.0,
            "aberto": 0.0,
        }
    by_day: dict[date, list[dict[str, Any]]] = {}
    a_pagar = 0.0
    pago = 0.0
    for row in rows:
        due = row.get("due")
        if not isinstance(due, date) or due < start or due > end:
            continue
        amount = float(row.get("amount") or 0)
        if row.get("paid"):
            pago += amount
        else:
            a_pagar += amount
        by_day.setdefault(due, []).append(row)
    days = []
    cursor = start
    while cursor <= end:
        items = by_day.get(cursor) or []
        days.append(
            {
                "date": cursor,
                "day": cursor.day,
                "weekday": cursor.weekday(),
                "items": items,
                "a_pagar": sum(float(item["amount"]) for item in items if not item.get("paid")),
                "pago": sum(float(item["amount"]) for item in items if item.get("paid")),
                "empty": not items,
            }
        )
        cursor += timedelta(days=1)
    return {
        "show_grid": True,
        "days": days,
        "a_pagar": a_pagar,
        "pago": pago,
        "aberto": a_pagar,
    }
