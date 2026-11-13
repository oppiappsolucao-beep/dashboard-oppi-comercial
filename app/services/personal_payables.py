"""Contas pessoais do acesso Financeiro 1. Não entra em Contas a pagar da empresa."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.company_payables import (
    _due_on_same_month,
    _parse_due,
    add_months,
    category_label,
    money_input,
    normalize_category,
    payable_status,
)
from app.services.crm_local_db import _connect, _lock, init_crm_local_db
from app.services.legacy_core import normalize_text

_TZ = ZoneInfo("America/Sao_Paulo")
_pg_ready = False
_sqlite_ready = False

_CREATE_SQLITE = """
CREATE TABLE IF NOT EXISTS personal_payables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    due_date TEXT NOT NULL,
    paid_on TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    supplier TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'outra',
    repeat_count INTEGER NOT NULL DEFAULT 1,
    series_id TEXT NOT NULL DEFAULT ''
)
"""


def _uses_postgres() -> bool:
    from config.settings import settings

    return (settings.database_url or "").startswith("postgresql")


def _ensure_store() -> None:
    global _pg_ready, _sqlite_ready
    if not _uses_postgres():
        if _sqlite_ready:
            return
        init_crm_local_db()
        with _lock, _connect() as conn:
            conn.execute(_CREATE_SQLITE)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_personal_payables_due ON personal_payables(due_date)"
            )
            conn.commit()
        _sqlite_ready = True
        return
    if _pg_ready:
        return
    from database.connection import engine
    from database.models import PersonalPayable

    PersonalPayable.__table__.create(bind=engine, checkfirst=True)
    _import_sqlite_payables()
    _pg_ready = True


def _import_sqlite_payables() -> None:
    import sqlite3

    from database.connection import SessionLocal
    from database.models import PersonalPayable
    from app.services.crm_local_db import _db_path

    path = _db_path()
    if not path.exists():
        return
    db = SessionLocal()
    try:
        if db.query(PersonalPayable.id).first() is not None:
            return
        src = sqlite3.connect(str(path), timeout=30)
        src.row_factory = sqlite3.Row
        try:
            present = {
                row[0]
                for row in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            if "personal_payables" not in present:
                return
            columns = {row[1] for row in src.execute("PRAGMA table_info(personal_payables)").fetchall()}
            for row in src.execute("SELECT * FROM personal_payables").fetchall():
                def cell(key: str, default=""):
                    return row[key] if key in columns else default

                db.add(
                    PersonalPayable(
                        description=cell("description") or "Conta",
                        amount=float(cell("amount", 0) or 0),
                        due_date=cell("due_date"),
                        paid_on=cell("paid_on"),
                        created_at=cell("created_at"),
                        supplier=cell("supplier"),
                        category=cell("category", "outra") or "outra",
                        repeat_count=int(cell("repeat_count", 1) or 1),
                        series_id=cell("series_id"),
                    )
                )
        finally:
            src.close()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _row_view(row, today: date) -> dict[str, Any] | None:
    due = _parse_due(row.due_date if hasattr(row, "due_date") else row["due_date"])
    if not due:
        return None
    paid_raw = row.paid_on if hasattr(row, "paid_on") else row["paid_on"]
    paid_on = _parse_due(paid_raw)
    paid = bool(paid_on)
    supplier = row.supplier if hasattr(row, "supplier") else row["supplier"]
    category = row.category if hasattr(row, "category") else row["category"]
    description = row.description if hasattr(row, "description") else row["description"]
    repeat_count = row.repeat_count if hasattr(row, "repeat_count") else row["repeat_count"]
    series_id = row.series_id if hasattr(row, "series_id") else row["series_id"]
    amount = float((row.amount if hasattr(row, "amount") else row["amount"]) or 0)
    times = int(repeat_count or 1)
    series = normalize_text(series_id)
    return {
        "id": int(row.id if hasattr(row, "id") else row["id"]),
        "description": normalize_text(description) or "Conta",
        "supplier": normalize_text(supplier),
        "category": normalize_category(category),
        "category_label": category_label(category),
        "repeat_count": times,
        "series_id": series,
        "repete": bool(series) and times > 1,
        "amount": amount,
        "amount_input": money_input(amount),
        "due": due,
        "due_iso": due.isoformat(),
        "paid": paid,
        "paid_on": paid_on,
        "status": payable_status(due, paid, today),
    }


def list_suppliers() -> list[str]:
    _ensure_store()
    if _uses_postgres():
        from sqlalchemy import func

        from database.connection import SessionLocal
        from database.models import PersonalPayable

        db = SessionLocal()
        try:
            rows = (
                db.query(PersonalPayable.supplier)
                .filter(func.trim(PersonalPayable.supplier) != "")
                .distinct()
                .all()
            )
        finally:
            db.close()
        names = []
        seen = set()
        for (supplier,) in rows:
            name = normalize_text(supplier)
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            names.append(name)
        names.sort(key=str.lower)
        return names
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT supplier
            FROM personal_payables
            WHERE TRIM(supplier) != ''
            ORDER BY supplier COLLATE NOCASE
            """
        ).fetchall()
    names = []
    seen = set()
    for row in rows:
        name = normalize_text(row["supplier"])
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def list_payables(start: date, end: date, today: date | None = None) -> list[dict[str, Any]]:
    _ensure_store()
    today = today or datetime.now(_TZ).date()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import PersonalPayable

        db = SessionLocal()
        try:
            rows = (
                db.query(PersonalPayable)
                .filter(PersonalPayable.due_date >= start.isoformat(), PersonalPayable.due_date <= end.isoformat())
                .order_by(PersonalPayable.due_date.asc(), PersonalPayable.id.asc())
                .all()
            )
            return [item for row in rows if (item := _row_view(row, today))]
        finally:
            db.close()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, description, amount, due_date, paid_on, created_at,
                   supplier, category, repeat_count, series_id
            FROM personal_payables
            WHERE due_date >= ? AND due_date <= ?
            ORDER BY due_date ASC, id ASC
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    out = []
    for row in rows:
        item = _row_view(row, today)
        if item:
            out.append(item)
    return out


def create_payable(
    description: str,
    amount: float,
    due: date,
    *,
    supplier: str = "",
    category: str = "outra",
    repeat_count: int = 1,
) -> int:
    _ensure_store()
    now = datetime.now(_TZ).isoformat(timespec="seconds")
    times = max(1, min(int(repeat_count or 1), 36))
    series = uuid.uuid4().hex if times > 1 else ""
    name = normalize_text(description) or "Conta"
    vendor = normalize_text(supplier)
    kind = normalize_category(category)
    value = round(float(amount), 2)
    first_id = 0
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import PersonalPayable

        db = SessionLocal()
        try:
            for step in range(times):
                row = PersonalPayable(
                    description=name,
                    amount=value,
                    due_date=add_months(due, step).isoformat(),
                    paid_on="",
                    created_at=now,
                    supplier=vendor,
                    category=kind,
                    repeat_count=times,
                    series_id=series,
                )
                db.add(row)
                db.flush()
                if step == 0:
                    first_id = int(row.id or 0)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return first_id
    with _lock, _connect() as conn:
        for step in range(times):
            cursor = conn.execute(
                """
                INSERT INTO personal_payables (
                    description, amount, due_date, paid_on, created_at,
                    supplier, category, repeat_count, series_id
                )
                VALUES (?, ?, ?, '', ?, ?, ?, ?, ?)
                """,
                (name, value, add_months(due, step).isoformat(), now, vendor, kind, times, series),
            )
            if step == 0:
                first_id = int(cursor.lastrowid or 0)
        conn.commit()
    return first_id


def update_payable(
    payable_id: int,
    *,
    description: str,
    amount: float,
    due: date,
    supplier: str = "",
    category: str = "outra",
    scope: str = "mes",
) -> int:
    _ensure_store()
    name = normalize_text(description) or "Conta"
    vendor = normalize_text(supplier)
    kind = normalize_category(category)
    value = round(float(amount), 2)
    apply_all = normalize_text(scope).lower() == "todos"
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import PersonalPayable

        db = SessionLocal()
        try:
            current = db.query(PersonalPayable).filter(PersonalPayable.id == int(payable_id)).one_or_none()
            if current is None:
                return 0
            series = normalize_text(current.series_id)
            rows = [current]
            if apply_all and series:
                rows = db.query(PersonalPayable).filter(PersonalPayable.series_id == series).all()
            for row in rows:
                row.description = name
                row.amount = value
                row.supplier = vendor
                row.category = kind
                current_due = _parse_due(row.due_date)
                if apply_all and current_due:
                    row.due_date = _due_on_same_month(current_due, due).isoformat()
                else:
                    row.due_date = due.isoformat()
            db.commit()
            return len(rows)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    with _lock, _connect() as conn:
        current = conn.execute(
            "SELECT id, series_id, due_date FROM personal_payables WHERE id = ?",
            (int(payable_id),),
        ).fetchone()
        if current is None:
            return 0
        series = normalize_text(current["series_id"])
        if apply_all and series:
            targets = conn.execute(
                "SELECT id, due_date FROM personal_payables WHERE series_id = ?",
                (series,),
            ).fetchall()
        else:
            targets = [current]
        for row in targets:
            current_due = _parse_due(row["due_date"])
            if apply_all and current_due:
                next_due = _due_on_same_month(current_due, due)
            else:
                next_due = due
            conn.execute(
                """
                UPDATE personal_payables
                SET description = ?, amount = ?, supplier = ?, category = ?, due_date = ?
                WHERE id = ?
                """,
                (name, value, vendor, kind, next_due.isoformat(), int(row["id"])),
            )
        conn.commit()
    return len(targets)


def mark_payable_paid(payable_id: int, paid_on: date | None = None) -> bool:
    _ensure_store()
    when = (paid_on or datetime.now(_TZ).date()).isoformat()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import PersonalPayable

        db = SessionLocal()
        try:
            updated = (
                db.query(PersonalPayable)
                .filter(PersonalPayable.id == int(payable_id))
                .update({PersonalPayable.paid_on: when}, synchronize_session=False)
            )
            db.commit()
            return updated > 0
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "UPDATE personal_payables SET paid_on = ? WHERE id = ?",
            (when, int(payable_id)),
        )
        conn.commit()
        return cursor.rowcount > 0


def reopen_payable(payable_id: int) -> bool:
    _ensure_store()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import PersonalPayable

        db = SessionLocal()
        try:
            updated = (
                db.query(PersonalPayable)
                .filter(PersonalPayable.id == int(payable_id))
                .update({PersonalPayable.paid_on: ""}, synchronize_session=False)
            )
            db.commit()
            return updated > 0
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "UPDATE personal_payables SET paid_on = '' WHERE id = ?",
            (int(payable_id),),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_payable(payable_id: int) -> bool:
    _ensure_store()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import PersonalPayable

        db = SessionLocal()
        try:
            updated = (
                db.query(PersonalPayable)
                .filter(PersonalPayable.id == int(payable_id))
                .delete(synchronize_session=False)
            )
            db.commit()
            return updated > 0
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM personal_payables WHERE id = ?", (int(payable_id),))
        conn.commit()
        return cursor.rowcount > 0
