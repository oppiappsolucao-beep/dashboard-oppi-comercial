"""Contas a pagar da empresa — controle mensal por dia, independente do Asaas."""
from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.crm_local_db import _connect, _lock, init_crm_local_db
from app.services.legacy_core import normalize_text

_TZ = ZoneInfo("America/Sao_Paulo")

CATEGORIES = (
    ("fixa", "Fixa"),
    ("emprestimo", "Empréstimo"),
    ("variavel", "Variável"),
    ("imposto", "Imposto"),
    ("servico", "Serviço"),
    ("outra", "Outra"),
)
CATEGORY_LABELS = dict(CATEGORIES)
_pg_ready = False


def _uses_postgres() -> bool:
    from config.settings import settings

    return (settings.database_url or "").startswith("postgresql")


def _ensure_store() -> None:
    global _pg_ready
    if not _uses_postgres():
        init_crm_local_db()
        return
    if _pg_ready:
        return
    from database.connection import engine
    from database.models import CompanyPayable

    CompanyPayable.__table__.create(bind=engine, checkfirst=True)
    _import_sqlite_payables()
    _pg_ready = True


def _import_sqlite_payables() -> None:
    """Copia o arquivo local se o Postgres ainda estiver vazio. Não apaga o que já existe."""
    import sqlite3

    from database.connection import SessionLocal
    from database.models import CompanyPayable
    from app.services.crm_local_db import _db_path

    path = _db_path()
    if not path.exists():
        return
    db = SessionLocal()
    try:
        if db.query(CompanyPayable.id).first() is not None:
            return
        src = sqlite3.connect(str(path), timeout=30)
        src.row_factory = sqlite3.Row
        try:
            present = {
                row[0]
                for row in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            if "company_payables" not in present:
                return
            columns = {row[1] for row in src.execute("PRAGMA table_info(company_payables)").fetchall()}
            for row in src.execute("SELECT * FROM company_payables").fetchall():
                def cell(key: str, default=""):
                    return row[key] if key in columns else default

                db.add(
                    CompanyPayable(
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
    amount = row.amount if hasattr(row, "amount") else row["amount"]
    return {
        "id": int(row.id if hasattr(row, "id") else row["id"]),
        "description": normalize_text(description) or "Conta",
        "supplier": normalize_text(supplier),
        "category": normalize_category(category),
        "category_label": category_label(category),
        "repeat_count": int(repeat_count or 1),
        "amount": float(amount or 0),
        "due": due,
        "paid": paid,
        "paid_on": paid_on,
        "status": payable_status(due, paid, today),
    }


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


def category_label(key: str) -> str:
    return CATEGORY_LABELS.get(normalize_text(key).lower(), "Outra")


def normalize_category(key: str) -> str:
    clean = normalize_text(key).lower()
    return clean if clean in CATEGORY_LABELS else "outra"


def payable_status(due: date, paid: bool, today: date) -> str:
    if paid:
        return "pago"
    if due < today:
        return "atrasado"
    return "a_pagar"


def add_months(due: date, months: int) -> date:
    month_index = due.month - 1 + months
    year = due.year + month_index // 12
    month = month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(due.day, last))


def total_launched() -> float:
    """Soma de todas as contas já lançadas, em qualquer mês."""
    _ensure_store()
    if _uses_postgres():
        from sqlalchemy import func

        from database.connection import SessionLocal
        from database.models import CompanyPayable

        db = SessionLocal()
        try:
            total = db.query(func.coalesce(func.sum(CompanyPayable.amount), 0)).scalar()
            return round(float(total or 0), 2)
        finally:
            db.close()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM company_payables").fetchone()
    return round(float(row[0] or 0), 2)


def list_suppliers() -> list[str]:
    _ensure_store()
    if _uses_postgres():
        from sqlalchemy import func

        from database.connection import SessionLocal
        from database.models import CompanyPayable

        db = SessionLocal()
        try:
            rows = (
                db.query(CompanyPayable.supplier)
                .filter(func.trim(CompanyPayable.supplier) != "")
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
            FROM company_payables
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
    today = today or _today()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import CompanyPayable

        db = SessionLocal()
        try:
            rows = (
                db.query(CompanyPayable)
                .filter(CompanyPayable.due_date >= start.isoformat(), CompanyPayable.due_date <= end.isoformat())
                .order_by(CompanyPayable.due_date.asc(), CompanyPayable.id.asc())
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
        paid = bool(paid_on)
        out.append(
            {
                "id": int(row["id"]),
                "description": normalize_text(row["description"]) or "Conta",
                "supplier": normalize_text(row["supplier"]),
                "category": normalize_category(row["category"]),
                "category_label": category_label(row["category"]),
                "repeat_count": int(row["repeat_count"] or 1),
                "amount": float(row["amount"] or 0),
                "due": due,
                "paid": paid,
                "paid_on": paid_on,
                "status": payable_status(due, paid, today),
            }
        )
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
        from database.models import CompanyPayable

        db = SessionLocal()
        try:
            for step in range(times):
                row = CompanyPayable(
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
                INSERT INTO company_payables (
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


def mark_payable_paid(payable_id: int, paid_on: date | None = None) -> bool:
    _ensure_store()
    when = (paid_on or _today()).isoformat()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import CompanyPayable

        db = SessionLocal()
        try:
            updated = (
                db.query(CompanyPayable)
                .filter(CompanyPayable.id == int(payable_id))
                .update({CompanyPayable.paid_on: when}, synchronize_session=False)
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
            "UPDATE company_payables SET paid_on = ? WHERE id = ?",
            (when, int(payable_id)),
        )
        conn.commit()
        return cursor.rowcount > 0


def reopen_payable(payable_id: int) -> bool:
    _ensure_store()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import CompanyPayable

        db = SessionLocal()
        try:
            updated = (
                db.query(CompanyPayable)
                .filter(CompanyPayable.id == int(payable_id))
                .update({CompanyPayable.paid_on: ""}, synchronize_session=False)
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
            "UPDATE company_payables SET paid_on = '' WHERE id = ?",
            (int(payable_id),),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_payable(payable_id: int) -> bool:
    _ensure_store()
    if _uses_postgres():
        from database.connection import SessionLocal
        from database.models import CompanyPayable

        db = SessionLocal()
        try:
            updated = (
                db.query(CompanyPayable)
                .filter(CompanyPayable.id == int(payable_id))
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
