"""Ordens de serviço do cadastro do cliente, com protocolo sequencial."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from config.crm_options import PRIORITY_OPTIONS
from config.settings import settings as runtime_settings

from app.services.crm_local_db import DEFAULT_TENANT_ID, _connect, _lock, init_crm_local_db
from app.services.legacy_core import normalize_text

SERVICE_ORDER_STATUS_LABELS = {
    "aberta": "Aberta",
    "em_andamento": "Em andamento",
    "concluida": "Concluída",
    "cancelada": "Cancelada",
}


def _now() -> datetime:
    return datetime.now(ZoneInfo(runtime_settings.timezone)).replace(tzinfo=None)


def _format_day(value: str) -> str:
    text = normalize_text(value)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return "Sem dia"
    year, month, day = text.split("-")
    return f"{day}/{month}/{year}"


def _format_when(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    return parsed.strftime("%d/%m/%Y %H:%M")


def _allocate_protocol(conn, year: int) -> str:
    row = conn.execute(
        "SELECT last_seq FROM service_order_counters WHERE year = ?",
        (year,),
    ).fetchone()
    seq = int(row["last_seq"]) + 1 if row else 1
    conn.execute(
        """
        INSERT INTO service_order_counters (year, last_seq) VALUES (?, ?)
        ON CONFLICT(year) DO UPDATE SET last_seq = excluded.last_seq
        """,
        (year, seq),
    )
    return f"OS-{year}-{seq:06d}"


def _row_to_view(row) -> dict:
    status = normalize_text(row["status"]) or "aberta"
    return {
        "id": row["id"],
        "protocol": row["protocol"],
        "empresa": row["empresa"],
        "subject": row["subject"],
        "description": row["description"],
        "status": status,
        "status_label": SERVICE_ORDER_STATUS_LABELS.get(status, "Aberta"),
        "status_class": status,
        "priority": row["priority"] or "—",
        "sector": row["sector"] if "sector" in row.keys() else "",
        "queue_id": (row["queue_id"] if "queue_id" in row.keys() else "") or "analise",
        "scheduled_date": row["scheduled_date"] if "scheduled_date" in row.keys() else "",
        "scheduled_date_label": _format_day(row["scheduled_date"] if "scheduled_date" in row.keys() else ""),
        "responsible": row["responsible"] or "—",
        "created_by": row["created_by"] or "—",
        "created_at_label": _format_when(row["created_at"]),
    }


def list_service_orders(tenant_id: str | None, sheet_row: int) -> list[dict]:
    init_crm_local_db()
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM service_orders
            WHERE tenant_id = ? AND sheet_row = ?
            ORDER BY created_at DESC, protocol DESC
            """,
            (tenant, int(sheet_row)),
        ).fetchall()
    return [_row_to_view(row) for row in rows]


def create_service_order(
    *,
    tenant_id: str | None,
    sheet_row: int,
    empresa: str,
    subject: str,
    description: str = "",
    sector: str = "",
    scheduled_date: str = "",
    responsible: str = "",
    priority: str = "Média",
    created_by: str = "",
) -> dict:
    clean_subject = normalize_text(subject)
    if not clean_subject:
        raise ValueError("Informe o assunto da ordem de serviço.")
    if len(clean_subject) > 180:
        raise ValueError("O assunto da ordem de serviço pode ter no máximo 180 caracteres.")

    clean_description = normalize_text(description)
    if len(clean_description) > 2000:
        raise ValueError("A descrição da ordem de serviço pode ter no máximo 2000 caracteres.")

    from app.services.org_registry import validate_service_assignment

    clean_sector, clean_responsible = validate_service_assignment(sector, responsible)
    clean_day = normalize_text(scheduled_date)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", clean_day):
        raise ValueError("Informe o dia da ordem de serviço.")

    clean_priority = normalize_text(priority)
    if clean_priority not in PRIORITY_OPTIONS:
        clean_priority = "Média"

    init_crm_local_db()
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    now = _now()
    stamp = now.isoformat(timespec="seconds")
    order_id = f"os_{uuid.uuid4().hex[:12]}"

    with _lock, _connect() as conn:
        protocol = _allocate_protocol(conn, now.year)
        conn.execute(
            """
            INSERT INTO service_orders (
                id, tenant_id, sheet_row, protocol, empresa, subject, description,
                status, priority, sector, scheduled_date, queue_id, responsible, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'aberta', ?, ?, ?, 'analise', ?, ?, ?, ?)
            """,
            (
                order_id,
                tenant,
                int(sheet_row),
                protocol,
                normalize_text(empresa),
                clean_subject,
                clean_description,
                clean_priority,
                clean_sector,
                clean_day,
                clean_responsible,
                normalize_text(created_by),
                stamp,
                stamp,
            ),
        )

    orders = list_service_orders(tenant, sheet_row)
    created = next((item for item in orders if item["id"] == order_id), None)
    if created is None:
        raise ValueError("A ordem de serviço foi salva, mas não consegui recarregá-la.")
    return created


ENTRY_QUEUE_ID = "analise"
ENTRY_QUEUE_NAME = "Análise"


def list_orders_by_sector(sector_name: str) -> list[dict]:
    init_crm_local_db()
    sector = normalize_text(sector_name)
    if not sector:
        return []
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM service_orders
            WHERE lower(sector) = lower(?)
            ORDER BY scheduled_date, created_at
            """,
            (sector,),
        ).fetchall()
    return [_row_to_view(row) for row in rows]


def build_sector_board(sector_id: str, sector_name: str) -> list[dict]:
    from app.services.org_registry import list_sector_queues

    columns = [{"id": ENTRY_QUEUE_ID, "name": ENTRY_QUEUE_NAME, "fixed": True, "cards": []}]
    known = {ENTRY_QUEUE_ID}
    for queue in list_sector_queues(sector_id):
        columns.append({"id": queue["id"], "name": queue["name"], "fixed": False, "cards": []})
        known.add(queue["id"])
    buckets = {column["id"]: column for column in columns}
    for card in list_orders_by_sector(sector_name):
        queue_id = card.get("queue_id") or ENTRY_QUEUE_ID
        if queue_id not in known:
            queue_id = ENTRY_QUEUE_ID
        buckets[queue_id]["cards"].append(card)
    return columns


def move_service_order(order_id: str, queue_id: str, sector_id: str, sector_name: str) -> None:
    from app.services.org_registry import list_sector_queues

    target = normalize_text(queue_id) or ENTRY_QUEUE_ID
    allowed = {ENTRY_QUEUE_ID} | {queue["id"] for queue in list_sector_queues(sector_id)}
    if target not in allowed:
        raise ValueError("Essa fila não pertence ao setor.")
    init_crm_local_db()
    stamp = _now().isoformat(timespec="seconds")
    with _lock, _connect() as conn:
        current = conn.execute(
            "SELECT id, sector FROM service_orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Ordem de serviço não encontrada.")
        if normalize_text(current["sector"]).lower() != normalize_text(sector_name).lower():
            raise ValueError("Essa ordem é de outro setor.")
        conn.execute(
            "UPDATE service_orders SET queue_id = ?, updated_at = ? WHERE id = ?",
            (target, stamp, order_id),
        )
