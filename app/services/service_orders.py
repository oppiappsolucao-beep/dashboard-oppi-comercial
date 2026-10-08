"""Ordens de serviço do cadastro do cliente, com protocolo sequencial."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.crm_options import PRIORITY_OPTIONS
from config.settings import settings as runtime_settings

from app.services.crm_local_db import DEFAULT_TENANT_ID
from app.services.registry_store import _lock, connect as _connect, init_store as init_crm_local_db
from app.services.legacy_core import normalize_text

SERVICE_ORDER_STATUS_LABELS = {
    "aberta": "Aberta",
    "em_andamento": "Em andamento",
    "concluida": "Concluída",
    "reaberta": "Reaberta",
    "cancelada": "Cancelada",
}

EVENT_KIND_LABELS = {
    "criada": "Criada",
    "movida": "Movida",
    "concluida": "Concluída",
    "reaberta": "Reaberta",
    "atualizacao": "Atualização",
}


def _now() -> datetime:
    return datetime.now(ZoneInfo(runtime_settings.timezone)).replace(tzinfo=None)


_last_event_at: datetime | None = None


def _event_stamp() -> str:
    """Horário crescente mesmo quando dois registros caem no mesmo segundo."""
    global _last_event_at
    current = _now()
    if _last_event_at is not None and current <= _last_event_at:
        current = _last_event_at + timedelta(microseconds=1)
    _last_event_at = current
    return current.isoformat(timespec="microseconds")


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
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] if "updated_at" in row.keys() else "",
        "created_at_label": _format_when(row["created_at"]),
        "sheet_row": int(row["sheet_row"] or 0) if "sheet_row" in row.keys() else 0,
    }


def _add_event(conn, order_id: str, kind: str, summary: str, author: str, stamp: str) -> None:
    conn.execute(
        """
        INSERT INTO service_order_events (id, order_id, kind, summary, author, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            f"evt_{uuid.uuid4().hex[:12]}",
            order_id,
            kind,
            normalize_text(summary),
            normalize_text(author) or "Usuário",
            _event_stamp(),
        ),
    )


def _queue_label(conn, queue_id: str) -> str:
    if queue_id == ENTRY_QUEUE_ID:
        return ENTRY_QUEUE_NAME
    if queue_id == CAMPAIGN_QUEUE_ID:
        return CAMPAIGN_QUEUE_NAME
    if queue_id == DONE_QUEUE_ID:
        return DONE_QUEUE_NAME
    row = conn.execute(
        "SELECT name FROM org_queues WHERE id = ? AND active = 1",
        (queue_id,),
    ).fetchone()
    if row is None:
        return queue_id or ENTRY_QUEUE_NAME
    return row["name"] or queue_id


def _training_bits(description: str) -> dict:
    text = normalize_text(description)
    hour = ""
    trainee = ""
    hour_match = re.search(r"Horário:\s*(\d{2}:\d{2})", text)
    trainee_match = re.search(r"Responsável:\s*([^.]*)", text)
    if hour_match:
        hour = hour_match.group(1)
    if trainee_match:
        trainee = normalize_text(trainee_match.group(1))
    hour_label = hour
    if re.match(r"^\d{2}:\d{2}$", hour):
        hour_label = f"{hour} – {int(hour[:2]) + 1:02d}:{hour[3:]}"
    return {"hour": hour, "hour_label": hour_label, "trainee": trainee}


def list_training_appointments(responsible: str = "") -> list[dict]:
    """Treinamentos agendados. Sem responsável, devolve todos."""
    init_crm_local_db()
    name = normalize_text(responsible).lower()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM service_orders
            WHERE lower(subject) = 'treinamento'
            ORDER BY scheduled_date, created_at
            """
        ).fetchall()
    items = []
    for row in rows:
        view = _row_to_view(row)
        if name and normalize_text(view.get("responsible")).lower() != name:
            continue
        view.update(_training_bits(view.get("description") or ""))
        items.append(view)
    items.sort(key=lambda item: (item.get("scheduled_date") or "", item.get("hour") or "", item.get("empresa") or ""))
    return items


def trainer_busy_hours(responsible: str, day: str) -> set[str]:
    target_day = normalize_text(day)
    hours: set[str] = set()
    for item in list_training_appointments(responsible):
        if item.get("status") == "cancelada":
            continue
        if item.get("scheduled_date") == target_day and item.get("hour"):
            hours.add(item["hour"])
    return hours


def trainer_is_busy(responsible: str, day: str, hour: str) -> bool:
    return normalize_text(hour)[:5] in trainer_busy_hours(responsible, day)


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
        _add_event(
            conn,
            order_id,
            "criada",
            clean_description or clean_subject,
            created_by,
            stamp,
        )

    orders = list_service_orders(tenant, sheet_row)
    created = next((item for item in orders if item["id"] == order_id), None)
    if created is None:
        raise ValueError("A ordem de serviço foi salva, mas não consegui recarregá-la.")
    return created


ENTRY_QUEUE_ID = "analise"
ENTRY_QUEUE_NAME = "Análise"
CAMPAIGN_QUEUE_ID = "campanha"
CAMPAIGN_QUEUE_NAME = "Campanha"
DONE_QUEUE_ID = "concluida"
DONE_QUEUE_NAME = "Concluída"


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


def create_campaign_card(
    *,
    empresa: str,
    subject: str,
    description: str,
    sector: str,
    scheduled_date: str,
    phone: str = "",
) -> str:
    """Card da coluna Campanha. Não exige funcionário responsável."""
    init_crm_local_db()
    now = _now()
    stamp = now.isoformat(timespec="seconds")
    order_id = f"os_{uuid.uuid4().hex[:12]}"
    day = scheduled_date if re.match(r"^\d{4}-\d{2}-\d{2}$", scheduled_date or "") else ""
    with _lock, _connect() as conn:
        protocol = _allocate_protocol(conn, now.year)
        conn.execute(
            """
            INSERT INTO service_orders (
                id, tenant_id, sheet_row, protocol, empresa, subject, description,
                status, priority, sector, scheduled_date, queue_id, responsible, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'aberta', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                DEFAULT_TENANT_ID,
                0,
                protocol,
                normalize_text(empresa) or "Lead de campanha",
                normalize_text(subject) or "Lead de campanha",
                normalize_text(description),
                "Média",
                normalize_text(sector),
                day,
                CAMPAIGN_QUEUE_ID,
                "Comercial",
                "Leads Raissa",
                stamp,
                stamp,
            ),
        )
        _add_event(
            conn,
            order_id,
            "criada",
            normalize_text(description) or normalize_text(subject) or "Lead da aba Leads Raissa",
            "Leads Raissa",
            stamp,
        )
    return order_id


def create_ticket_card(
    *,
    empresa: str,
    subject: str,
    description: str,
    sector: str,
    scheduled_date: str,
    phone: str = "",
) -> str:
    """Card de cliente da base (aba ticket). O comercial só encaminha o setor."""
    init_crm_local_db()
    now = _now()
    stamp = now.isoformat(timespec="seconds")
    order_id = f"os_{uuid.uuid4().hex[:12]}"
    day = scheduled_date if re.match(r"^\d{4}-\d{2}-\d{2}$", scheduled_date or "") else now.date().isoformat()
    with _lock, _connect() as conn:
        protocol = _allocate_protocol(conn, now.year)
        conn.execute(
            """
            INSERT INTO service_orders (
                id, tenant_id, sheet_row, protocol, empresa, subject, description,
                status, priority, sector, scheduled_date, queue_id, responsible, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'aberta', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                DEFAULT_TENANT_ID,
                0,
                protocol,
                normalize_text(empresa) or "Cliente da base",
                normalize_text(subject) or "Chamado da base",
                normalize_text(description),
                "Média",
                normalize_text(sector),
                day,
                ENTRY_QUEUE_ID,
                "Comercial",
                "Tickets",
                stamp,
                stamp,
            ),
        )
        _add_event(
            conn,
            order_id,
            "criada",
            normalize_text(description) or normalize_text(subject) or "Chamado da aba ticket",
            "Tickets",
            stamp,
        )
    return order_id


def is_commercial_sector(sector_name: str) -> bool:
    name = normalize_text(sector_name).lower()
    return "comercial" in name


def is_oppi_tech_sector(sector_name: str) -> bool:
    compact = re.sub(r"\s+", "", normalize_text(sector_name).lower())
    return "oppitech" in compact


def _entry_follows_period(sector_name: str) -> bool:
    """Comercial e Oppi Tech filtram a coluna Análise pelo período escolhido."""
    name = normalize_text(sector_name).lower()
    compact = re.sub(r"\s+", "", name)
    return "comercial" in name or "oppitech" in compact


def _entry_visible(card: dict, start: str, end: str) -> bool:
    day = normalize_text(card.get("lead_date") or card.get("scheduled_date") or card.get("created_at"))[:10]
    if len(day) != 10 or day[4] != "-":
        return False
    return start <= day <= end


def queue_choices(sector_name: str) -> tuple[str, list[dict]]:
    """Colunas do Kanban do setor, para o status que muda o local do cliente."""
    from app.services.org_registry import list_sector_queues, list_sectors

    sector = next(
        (
            item
            for item in list_sectors()
            if normalize_text(item.get("name")).lower() == normalize_text(sector_name).lower()
        ),
        None,
    )
    sector_id = sector["id"] if sector else ""
    choices = [{"id": ENTRY_QUEUE_ID, "name": ENTRY_QUEUE_NAME}]
    if is_commercial_sector(sector_name):
        choices.append({"id": CAMPAIGN_QUEUE_ID, "name": CAMPAIGN_QUEUE_NAME})
    if sector_id:
        choices.extend({"id": item["id"], "name": item["name"]} for item in list_sector_queues(sector_id))
    choices.append({"id": DONE_QUEUE_ID, "name": DONE_QUEUE_NAME})
    return sector_id, choices


def _campaign_visible(card: dict, start: str, end: str) -> bool:
    """A coluna Campanha segue a data do lead, não o dia em que o card foi importado."""
    day = normalize_text(card.get("lead_date") or card.get("scheduled_date"))[:10]
    if len(day) != 10:
        return False
    return start <= day <= end


def build_sector_board(sector_id: str, sector_name: str, inicio: str = "", fim: str = "") -> list[dict]:
    from app.services.campaign_leads import attach_campaign_cards, finish_cadastro_url, sync_campaign_leads
    from app.services.org_registry import add_sector_queue, list_sector_queues

    period_start = ""
    period_end = ""
    if inicio or fim:
        from app.services.kanban_summary import period_bounds

        period_start, period_end = period_bounds(inicio, fim)

    if is_commercial_sector(sector_name):
        try:
            sync_campaign_leads(sector_name)
        except Exception:
            pass
        try:
            from app.services.ticket_orders import sync_ticket_orders

            sync_ticket_orders(sector_name)
        except Exception:
            pass
        if sector_id and not any(
            "proposta" in normalize_text(item.get("name")).lower()
            for item in list_sector_queues(sector_id)
        ):
            try:
                add_sector_queue(sector_id, "Proposta")
            except Exception:
                pass
    columns = [{"id": ENTRY_QUEUE_ID, "name": ENTRY_QUEUE_NAME, "fixed": True, "cards": []}]
    known = {ENTRY_QUEUE_ID}
    if is_commercial_sector(sector_name):
        columns.append({"id": CAMPAIGN_QUEUE_ID, "name": CAMPAIGN_QUEUE_NAME, "fixed": True, "cards": []})
        known.add(CAMPAIGN_QUEUE_ID)
    for queue in list_sector_queues(sector_id):
        columns.append({"id": queue["id"], "name": queue["name"], "fixed": False, "cards": []})
        known.add(queue["id"])
    columns.append({"id": DONE_QUEUE_ID, "name": DONE_QUEUE_NAME, "fixed": True, "cards": []})
    known.add(DONE_QUEUE_ID)
    buckets = {column["id"]: column for column in columns}
    cards = list_orders_by_sector(sector_name)
    attach_campaign_cards(cards)
    if is_commercial_sector(sector_name):
        for card in cards:
            if normalize_text(card.get("cadastro_url")):
                continue
            if card.get("source") == "campanha" or card.get("queue_id") == CAMPAIGN_QUEUE_ID:
                card["cadastro_url"] = finish_cadastro_url(card)
    for card in cards:
        queue_id = card.get("queue_id") or ENTRY_QUEUE_ID
        if queue_id not in known:
            queue_id = ENTRY_QUEUE_ID
        if queue_id == CAMPAIGN_QUEUE_ID and period_start and not _campaign_visible(card, period_start, period_end):
            continue
        if (
            queue_id == ENTRY_QUEUE_ID
            and period_start
            and _entry_follows_period(sector_name)
            and not _entry_visible(card, period_start, period_end)
        ):
            continue
        column = buckets[queue_id]
        if period_start and normalize_text(column["name"]).lower() in {"andamento", "em andamento"}:
            day = normalize_text(card.get("scheduled_date"))[:10]
            if not (len(day) == 10 and period_start <= day <= period_end):
                continue
        buckets[queue_id]["cards"].append(card)
    return columns


def send_order_to_sector(order_id: str, sector_name: str, author: str) -> str:
    """Leva a ordem para outro setor e a coloca na coluna Análise."""
    from app.services.org_registry import list_sectors

    target_name = normalize_text(sector_name)
    if not target_name:
        raise ValueError("Escolha o setor.")
    match = next(
        (
            item
            for item in list_sectors()
            if normalize_text(item.get("name")).lower() == target_name.lower()
        ),
        None,
    )
    if match is None:
        raise ValueError("Setor não encontrado.")
    target = match["name"]
    init_crm_local_db()
    stamp = _now().isoformat(timespec="seconds")
    with _lock, _connect() as conn:
        current = conn.execute(
            "SELECT id, sector FROM service_orders WHERE id = ?",
            (normalize_text(order_id),),
        ).fetchone()
        if current is None:
            raise ValueError("Ordem de serviço não encontrada.")
        if normalize_text(current["sector"]).lower() == target.lower():
            return "Esta ordem já está neste setor."
        conn.execute(
            """
            UPDATE service_orders
            SET sector = ?, queue_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (target, ENTRY_QUEUE_ID, stamp, current["id"]),
        )
        _add_event(conn, current["id"], "movida", f"Encaminhada para {target}.", author, stamp)
    return f"OS encaminhada para {target}."


def delete_service_order(order_id: str) -> None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        current = conn.execute(
            "SELECT id FROM service_orders WHERE id = ?",
            (normalize_text(order_id),),
        ).fetchone()
        if current is None:
            raise ValueError("Ordem de serviço não encontrada.")
        conn.execute("DELETE FROM service_order_events WHERE order_id = ?", (current["id"],))
        conn.execute("DELETE FROM service_orders WHERE id = ?", (current["id"],))


def move_service_order(
    order_id: str,
    queue_id: str,
    sector_id: str,
    sector_name: str,
    *,
    author: str = "",
    reopen: bool = False,
) -> None:
    from app.services.org_registry import list_sector_queues

    target = normalize_text(queue_id) or ENTRY_QUEUE_ID
    allowed = {ENTRY_QUEUE_ID, DONE_QUEUE_ID} | {queue["id"] for queue in list_sector_queues(sector_id)}
    if is_commercial_sector(sector_name):
        allowed.add(CAMPAIGN_QUEUE_ID)
    if target not in allowed:
        raise ValueError("Essa fila não pertence ao setor.")
    init_crm_local_db()
    stamp = _now().isoformat(timespec="seconds")
    with _lock, _connect() as conn:
        current = conn.execute(
            "SELECT id, sector, status, queue_id FROM service_orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if current is None:
            raise ValueError("Ordem de serviço não encontrada.")
        if normalize_text(current["sector"]).lower() != normalize_text(sector_name).lower():
            raise ValueError("Essa ordem é de outro setor.")
        current_queue = normalize_text(current["queue_id"]) or ENTRY_QUEUE_ID
        if current_queue == target:
            return
        label = _queue_label(conn, target)
        leaving_done = current_queue == DONE_QUEUE_ID and target != DONE_QUEUE_ID
        if leaving_done and not reopen:
            raise ValueError("Confirme se deseja reabrir a ordem de serviço.")
        if leaving_done:
            status = "reaberta"
            kind = "reaberta"
            summary = f"Reaberta na fila {label}."
        elif target == DONE_QUEUE_ID:
            status = "concluida"
            kind = "concluida"
            summary = "Concluída."
        else:
            status = "reaberta" if normalize_text(current["status"]) == "reaberta" else "em_andamento"
            kind = "movida"
            summary = f"Movida para {label}."
        conn.execute(
            "UPDATE service_orders SET queue_id = ?, status = ?, updated_at = ? WHERE id = ?",
            (target, status, stamp, order_id),
        )
        _add_event(conn, order_id, kind, summary, author, stamp)


def _client_contact(sheet_row: int) -> dict:
    empty = {
        "contato": "",
        "whatsapp": "",
        "telefone": "",
        "email": "",
        "cidade": "",
        "uf": "",
        "endereco": "",
    }
    if not sheet_row:
        return empty
    try:
        from app.services.crm_registrations_storage import is_crm_postgres_ready

        if not is_crm_postgres_ready():
            return empty
        from database.connection import SessionLocal
        from database.models import CrmRegistration

        db = SessionLocal()
        try:
            row = (
                db.query(CrmRegistration)
                .filter(CrmRegistration.sheet_row == int(sheet_row))
                .first()
            )
            if row is None:
                return empty
            cidade = normalize_text(row.municipio)
            uf = normalize_text(row.uf)
            endereco = ", ".join(
                part
                for part in (
                    normalize_text(row.endereco),
                    normalize_text(row.endereco_numero),
                    normalize_text(row.bairro),
                    cidade,
                    uf,
                )
                if part
            )
            return {
                "contato": normalize_text(row.nome_contato),
                "whatsapp": normalize_text(row.telefone_b2b),
                "telefone": normalize_text(row.telefone_fixo) or normalize_text(row.telefone_alternativo),
                "email": normalize_text(row.email_empresa) or normalize_text(row.email_socio_1),
                "cidade": cidade,
                "uf": uf,
                "endereco": endereco,
            }
        finally:
            db.close()
    except Exception:
        return empty


def _event_view(row) -> dict:
    kind = normalize_text(row["kind"]) or "atualizacao"
    return {
        "kind": kind,
        "kind_label": EVENT_KIND_LABELS.get(kind, "Atualização"),
        "summary": row["summary"] or "",
        "author": row["author"] or "Usuário",
        "created_at_label": _format_when(row["created_at"]),
    }


def get_order_detail(order_id: str) -> dict | None:
    init_crm_local_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM service_orders WHERE id = ?",
            (normalize_text(order_id),),
        ).fetchone()
        if row is None:
            return None
        events = conn.execute(
            """
            SELECT kind, summary, author, created_at
            FROM service_order_events
            WHERE order_id = ?
            ORDER BY created_at, id
            """,
            (row["id"],),
        ).fetchall()
        queue_id = (row["queue_id"] if "queue_id" in row.keys() else "") or ENTRY_QUEUE_ID
        queue_name = _queue_label(conn, queue_id)
    detail = _row_to_view(row)
    detail["queue_name"] = queue_name
    detail["description"] = normalize_text(row["description"])
    history = [_event_view(item) for item in events]
    if not history:
        history.append(
            {
                "kind": "criada",
                "kind_label": "Criada",
                "summary": detail["description"] or detail["subject"],
                "author": detail["created_by"],
                "created_at_label": detail["created_at_label"],
            }
        )
    detail["history"] = history
    detail["client"] = _client_contact(detail["sheet_row"])
    detail["source"] = ""
    detail["creative"] = ""
    detail["campaign"] = ""
    detail["cadastro_url"] = ""
    try:
        from app.services.campaign_leads import campaign_card_extra

        extra = campaign_card_extra(row["id"])
    except Exception:
        extra = None
    if not extra:
        try:
            from app.services.ticket_orders import ticket_card_extra

            extra = ticket_card_extra(row["id"])
        except Exception:
            extra = None
    if extra:
        detail.update(extra)
        client = detail["client"]
        if not client.get("whatsapp"):
            client["whatsapp"] = extra.get("phone") or ""
        if not client.get("email"):
            client["email"] = extra.get("email") or ""
        if not client.get("contato"):
            client["contato"] = extra.get("contact_name") or ""
        if not client.get("cidade"):
            client["cidade"] = extra.get("city") or ""
        if not client.get("uf"):
            client["uf"] = extra.get("uf") or ""
    return detail


def add_order_update(order_id: str, note: str, author: str = "") -> None:
    clean = normalize_text(note)
    if len(clean) < 2:
        raise ValueError("Escreva a atualização.")
    if len(clean) > 1000:
        raise ValueError("A atualização pode ter no máximo 1000 caracteres.")
    init_crm_local_db()
    stamp = _now().isoformat(timespec="seconds")
    with _lock, _connect() as conn:
        current = conn.execute(
            "SELECT id FROM service_orders WHERE id = ?",
            (normalize_text(order_id),),
        ).fetchone()
        if current is None:
            raise ValueError("Ordem de serviço não encontrada.")
        _add_event(conn, current["id"], "atualizacao", clean, author, stamp)
        conn.execute(
            "UPDATE service_orders SET updated_at = ? WHERE id = ?",
            (stamp, current["id"]),
        )
