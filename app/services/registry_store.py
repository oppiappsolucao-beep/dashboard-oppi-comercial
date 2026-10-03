"""Setores, pessoas, filas e ordens de serviço.

Em produção (DATABASE_URL PostgreSQL) grava no banco que permanece no redeploy.
Sem Postgres, continua no SQLite local — o mesmo arquivo de antes.
A cópia do SQLite só inclui o que ainda não existe; não apaga e não sobrescreve.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager

from config.settings import settings

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_pg_ready = False

_TABLES = (
    "org_sectors",
    "org_people",
    "org_queues",
    "service_orders",
    "service_order_counters",
    "service_order_events",
)


def uses_postgres() -> bool:
    return settings.database_url.startswith("postgresql")


def _qmark_to_named(sql: str) -> str:
    sql = sql.replace("COLLATE NOCASE", "")
    parts: list[str] = []
    index = 0
    for char in sql:
        if char == "?":
            parts.append(f":p{index}")
            index += 1
        else:
            parts.append(char)
    return "".join(parts)


class _Result:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _PgConn:
    def __init__(self):
        from database.connection import engine

        self._conn = engine.connect()
        self._tx = self._conn.begin()

    def execute(self, sql: str, params=()):
        from sqlalchemy import text

        statement = text(_qmark_to_named(sql))
        bound = {f"p{i}": value for i, value in enumerate(params or ())}
        result = self._conn.execute(statement, bound)
        if not result.returns_rows:
            return _Result([])
        return _Result([dict(row) for row in result.mappings()])

    def commit(self) -> None:
        self._tx.commit()

    def rollback(self) -> None:
        self._tx.rollback()

    def close(self) -> None:
        self._conn.close()


def _ensure_postgres_tables() -> None:
    from database.connection import engine
    from database.models import (
        OrgPerson,
        OrgQueue,
        OrgSector,
        ServiceOrderCounter,
        ServiceOrderEvent,
        ServiceOrderRecord,
    )

    for model in (OrgSector, OrgPerson, OrgQueue, ServiceOrderRecord, ServiceOrderCounter, ServiceOrderEvent):
        model.__table__.create(bind=engine, checkfirst=True)


def _cell(row: sqlite3.Row, key: str, default=""):
    return row[key] if key in row.keys() else default


def _import_sqlite() -> None:
    """Copia registros do crm_local.db que ainda não estão no Postgres."""
    from database.connection import engine
    from app.services.crm_local_db import _db_path

    path = _db_path()
    if not path.exists():
        return
    src = sqlite3.connect(str(path), timeout=30)
    src.row_factory = sqlite3.Row
    try:
        present = {
            row[0]
            for row in src.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        copied = {name: 0 for name in _TABLES}
        if "org_sectors" in present:
            for row in src.execute("SELECT * FROM org_sectors").fetchall():
                if _insert_ignore(
                    engine,
                    """
                    INSERT INTO org_sectors (id, name, accesses_json, active, created_at, updated_at)
                    VALUES (:id, :name, :accesses_json, :active, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "accesses_json": _cell(row, "accesses_json", "[]") or "[]",
                        "active": int(_cell(row, "active", 1) or 0),
                        "created_at": _cell(row, "created_at"),
                        "updated_at": _cell(row, "updated_at"),
                    },
                ):
                    copied["org_sectors"] += 1
        if "org_people" in present:
            for row in src.execute("SELECT * FROM org_people").fetchall():
                if _insert_ignore(
                    engine,
                    """
                    INSERT INTO org_people (
                        id, kind, name, email, phone, sector_id, region, username, password_hash,
                        state_name, city, active, created_at, updated_at
                    ) VALUES (
                        :id, :kind, :name, :email, :phone, :sector_id, :region, :username, :password_hash,
                        :state_name, :city, :active, :created_at, :updated_at
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    {
                        "id": row["id"],
                        "kind": _cell(row, "kind"),
                        "name": _cell(row, "name"),
                        "email": _cell(row, "email"),
                        "phone": _cell(row, "phone"),
                        "sector_id": _cell(row, "sector_id"),
                        "region": _cell(row, "region"),
                        "username": _cell(row, "username"),
                        "password_hash": _cell(row, "password_hash"),
                        "state_name": _cell(row, "state_name"),
                        "city": _cell(row, "city"),
                        "active": int(_cell(row, "active", 1) or 0),
                        "created_at": _cell(row, "created_at"),
                        "updated_at": _cell(row, "updated_at"),
                    },
                ):
                    copied["org_people"] += 1
        if "org_queues" in present:
            for row in src.execute("SELECT * FROM org_queues").fetchall():
                if _insert_ignore(
                    engine,
                    """
                    INSERT INTO org_queues (id, sector_id, name, position, active, created_at)
                    VALUES (:id, :sector_id, :name, :position, :active, :created_at)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    {
                        "id": row["id"],
                        "sector_id": _cell(row, "sector_id"),
                        "name": _cell(row, "name"),
                        "position": int(_cell(row, "position", 1) or 1),
                        "active": int(_cell(row, "active", 1) or 0),
                        "created_at": _cell(row, "created_at"),
                    },
                ):
                    copied["org_queues"] += 1
        if "service_orders" in present:
            for row in src.execute("SELECT * FROM service_orders").fetchall():
                if _insert_ignore(
                    engine,
                    """
                    INSERT INTO service_orders (
                        id, tenant_id, sheet_row, protocol, empresa, subject, description,
                        status, priority, sector, scheduled_date, queue_id, responsible,
                        created_by, created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :sheet_row, :protocol, :empresa, :subject, :description,
                        :status, :priority, :sector, :scheduled_date, :queue_id, :responsible,
                        :created_by, :created_at, :updated_at
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    {
                        "id": row["id"],
                        "tenant_id": _cell(row, "tenant_id", "default") or "default",
                        "sheet_row": int(_cell(row, "sheet_row", 0) or 0),
                        "protocol": _cell(row, "protocol"),
                        "empresa": _cell(row, "empresa"),
                        "subject": _cell(row, "subject"),
                        "description": _cell(row, "description"),
                        "status": _cell(row, "status", "aberta") or "aberta",
                        "priority": _cell(row, "priority"),
                        "sector": _cell(row, "sector"),
                        "scheduled_date": _cell(row, "scheduled_date"),
                        "queue_id": _cell(row, "queue_id", "analise") or "analise",
                        "responsible": _cell(row, "responsible"),
                        "created_by": _cell(row, "created_by"),
                        "created_at": _cell(row, "created_at"),
                        "updated_at": _cell(row, "updated_at"),
                    },
                ):
                    copied["service_orders"] += 1
        if "service_order_counters" in present:
            for row in src.execute("SELECT * FROM service_order_counters").fetchall():
                if _insert_ignore(
                    engine,
                    """
                    INSERT INTO service_order_counters (year, last_seq)
                    VALUES (:year, :last_seq)
                    ON CONFLICT (year) DO UPDATE
                    SET last_seq = GREATEST(service_order_counters.last_seq, EXCLUDED.last_seq)
                    """,
                    {"year": int(row["year"]), "last_seq": int(row["last_seq"] or 0)},
                ):
                    copied["service_order_counters"] += 1
        if "service_order_events" in present:
            for row in src.execute("SELECT * FROM service_order_events").fetchall():
                if _insert_ignore(
                    engine,
                    """
                    INSERT INTO service_order_events (id, order_id, kind, summary, author, created_at)
                    VALUES (:id, :order_id, :kind, :summary, :author, :created_at)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    {
                        "id": row["id"],
                        "order_id": _cell(row, "order_id"),
                        "kind": _cell(row, "kind"),
                        "summary": _cell(row, "summary"),
                        "author": _cell(row, "author"),
                        "created_at": _cell(row, "created_at"),
                    },
                ):
                    copied["service_order_events"] += 1
        if any(copied.values()):
            logger.info("Cadastros copiados do SQLite para o Postgres: %s", copied)
    finally:
        src.close()


def _insert_ignore(engine, sql: str, params: dict) -> bool:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
        return (result.rowcount or 0) > 0
    except IntegrityError:
        return False
    except Exception:
        logger.exception("Linha de cadastro não copiada")
        return False


def init_store() -> None:
    """Prepara o destino. No Postgres, cria as tabelas e importa o SQLite uma vez por processo."""
    global _pg_ready
    if not uses_postgres():
        from app.services.crm_local_db import init_crm_local_db

        init_crm_local_db()
        return
    with _lock:
        if _pg_ready:
            return
        _ensure_postgres_tables()
        try:
            _import_sqlite()
        except Exception:
            logger.exception("Falha ao copiar cadastros do SQLite; o Postgres segue como destino")
        _pg_ready = True


@contextmanager
def connect():
    init_store()
    if uses_postgres():
        conn = _PgConn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return
    from app.services.crm_local_db import _connect

    with _connect() as conn:
        yield conn
