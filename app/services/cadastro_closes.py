"""Clientes que o comercial concluiu no Kanban e o que já virou cadastro no mês."""
from __future__ import annotations

from datetime import date
import re

from app.services.campaign_leads import cadastro_url, links_by_order
from app.services.legacy_core import normalize_text
from app.services.service_orders import DONE_QUEUE_ID, is_commercial_sector, is_oppi_tech_sector, list_orders_by_sector

_MONTHS = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def build_registration_closes() -> dict:
    today = date.today()
    start = today.replace(day=1).isoformat()
    end = today.isoformat()
    pending: list[dict] = []
    registered: list[dict] = []
    index = _registration_index()
    registered_orders = {row["order_id"] for row in index if row.get("order_id")}
    queues_cache: dict[str, list[dict]] = {}

    def queues_for(sector_name: str) -> list[dict]:
        key = normalize_text(sector_name).lower()
        if key not in queues_cache:
            queues_cache[key] = _sector_queues(sector_name)
        return queues_cache[key]

    for card in _done_campaign_cards():
        link = card.get("link") or {}
        match = _find_registration(link, index)
        item = {
            "empresa": normalize_text(link["empresa"]) or "Cliente sem nome",
            "phone": normalize_text(link["phone"]) or "Sem número",
            "contact_name": normalize_text(link["contact_name"]),
        }
        concluded_on = card.get("updated_at") or ""
        item["order_id"] = card.get("id") or ""
        item["queue_id"] = card.get("queue_id") or "concluida"
        item["queues"] = queues_for(card.get("sector") or "")
        linked = bool(item["order_id"] and item["order_id"] in registered_orders)
        if linked:
            match = next((row for row in index if row.get("order_id") == item["order_id"]), match)
        counted_day = _stamp_day(card.get("counted_on") or "")
        if card.get("counted_on"):
            if counted_day and start <= counted_day <= end:
                if match and match.get("sheet_row"):
                    item["company_url"] = _company_url(match.get("sheet_row"))
                else:
                    item["transfer_url"] = cadastro_url(link)
                registered.append(item)
            continue
        if match is None or (not linked and not _saved_for_this_close(match, concluded_on)):
            item["transfer_url"] = cadastro_url(link)
            pending.append(item)
            continue
        closed_on = _stamp_day(card.get("updated_at")) or _stamp_day(match.get("created_at")) or _stamp_day(match.get("data_chamado"))
        if closed_on and start <= closed_on <= end:
            item["company_url"] = _company_url(match.get("sheet_row"))
            registered.append(item)
    return {
        "pending": pending,
        "pending_total": len(pending),
        "registered": registered,
        "registered_total": len(registered),
        "month_label": f"{_MONTHS[today.month]} de {today.year}",
    }


def _done_campaign_cards() -> list[dict]:
    from app.services.org_registry import list_sectors
    from app.services.service_orders import counted_closed_stamps

    cards: list[dict] = []
    for sector in list_sectors():
        name = sector.get("name") or ""
        if not is_commercial_sector(name) and not is_oppi_tech_sector(name):
            continue
        rows = list_orders_by_sector(name)
        links = links_by_order([row.get("id", "") for row in rows])
        for card in rows:
            if card.get("queue_id") != DONE_QUEUE_ID:
                continue
            link = links.get(card.get("id")) or _link_from_order(card)
            if not link.get("empresa"):
                link["empresa"] = card.get("empresa") or ""
            link["order_id"] = link.get("order_id") or card.get("id") or ""
            card["link"] = link
            cards.append(card)
    stamps = counted_closed_stamps([card.get("id", "") for card in cards])
    for card in cards:
        card["counted_on"] = stamps.get(card.get("id") or "", "")
    return cards


def _desc_line(text: str, label: str) -> str:
    match = re.search(rf"(?im)^{re.escape(label)}:\s*(.+)$", text or "")
    return normalize_text(match.group(1)) if match else ""


def _link_from_order(card: dict) -> dict:
    description = card.get("description") or ""
    return {
        "empresa": card.get("empresa") or "",
        "phone": _desc_line(description, "WhatsApp"),
        "email": _desc_line(description, "E-mail"),
        "contact_name": _desc_line(description, "Contato"),
        "creative": "",
        "campaign": "",
        "city": "",
        "uf": "",
        "order_id": card.get("id") or "",
    }


def _sector_queues(sector_name: str) -> list[dict]:
    from app.services.service_orders import CAMPAIGN_QUEUE_ID, CAMPAIGN_QUEUE_NAME, DONE_QUEUE_ID, DONE_QUEUE_NAME, ENTRY_QUEUE_ID, ENTRY_QUEUE_NAME, queue_choices

    try:
        _sector_id, choices = queue_choices(sector_name)
    except Exception:
        choices = []
    if choices:
        return choices
    return [
        {"id": ENTRY_QUEUE_ID, "name": ENTRY_QUEUE_NAME},
        {"id": CAMPAIGN_QUEUE_ID, "name": CAMPAIGN_QUEUE_NAME},
        {"id": DONE_QUEUE_ID, "name": DONE_QUEUE_NAME},
    ]


def remember_lead_order(sheet_row: int, order_id: str) -> None:
    """Liga o cadastro salvo ao lead, para ele sair da fila ao ser cadastrado."""
    order_id = normalize_text(order_id)
    if int(sheet_row or 0) <= 0 or not order_id:
        return
    try:
        from app.services.crm_registrations_storage import get_registration_by_sheet_row, is_crm_postgres_ready
        from database.connection import SessionLocal
        from database.models import CrmRegistration
    except Exception:
        return
    if not is_crm_postgres_ready():
        return
    row = get_registration_by_sheet_row(int(sheet_row))
    if row is None:
        return
    from app.services.crm_registrations_storage import _json_dumps, _json_loads

    db = SessionLocal()
    try:
        current = db.get(CrmRegistration, int(row.id))
        if current is None:
            return
        extras = _json_loads(current.extras_json, {})
        if extras.get("campaign_order_id") == order_id:
            return
        extras["campaign_order_id"] = order_id
        current.extras_json = _json_dumps(extras)
        db.commit()
    except Exception:
        return
    finally:
        db.close()


def _registration_index() -> list[dict]:
    try:
        from app.services.crm_registrations_storage import is_crm_postgres_ready
        from database.connection import SessionLocal
        from database.models import CrmRegistration
    except Exception:
        return []
    if not is_crm_postgres_ready():
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(
                CrmRegistration.sheet_row,
                CrmRegistration.empresa,
                CrmRegistration.telefone_b2b,
                CrmRegistration.telefone_fixo,
                CrmRegistration.telefone_alternativo,
                CrmRegistration.created_at,
                CrmRegistration.data_chamado,
                CrmRegistration.extras_json,
            )
            .filter(CrmRegistration.cadastro_ativo.is_(True))
            .all()
        )
    except Exception:
        return []
    finally:
        db.close()
    return [
        {
            "sheet_row": int(row.sheet_row or 0),
            "empresa": normalize_text(row.empresa),
            "phones": [
                _phone_key(row.telefone_b2b),
                _phone_key(row.telefone_fixo),
                _phone_key(row.telefone_alternativo),
            ],
            "created_at": row.created_at or "",
            "data_chamado": row.data_chamado or "",
            "order_id": _order_id_from_extras(row.extras_json),
        }
        for row in rows
    ]


def _order_id_from_extras(raw) -> str:
    import json

    if not raw:
        return ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return normalize_text(data.get("campaign_order_id"))


def _saved_for_this_close(match: dict, concluded_on: str) -> bool:
    """Só sai da fila de cadastro se o cadastro foi salvo neste fechamento."""
    closed = _stamp_day(concluded_on)
    created = _stamp_day(match.get("created_at")) or _stamp_day(match.get("data_chamado"))
    if not closed or not created:
        return False
    return created >= closed


def _find_registration(link: dict, index: list[dict]) -> dict | None:
    phone = _phone_key(link.get("phone"))
    empresa = normalize_text(link.get("empresa")).lower()
    by_phone = None
    by_name = None
    for row in index:
        if phone and phone in row["phones"]:
            by_phone = row
            break
        if empresa and empresa == row["empresa"].lower() and by_name is None:
            by_name = row
    return by_phone or by_name


def _company_url(sheet_row) -> str:
    try:
        number = int(sheet_row or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return "/leads-e-empresas"
    return f"/cadastro/todos/{number}/editar?from=leads"


def _phone_key(value: str) -> str:
    digits = "".join(ch for ch in normalize_text(value) if ch.isdigit())
    if len(digits) >= 11:
        return digits[-11:]
    if len(digits) >= 8:
        return digits
    return ""


def _stamp_day(value: str) -> str:
    text = normalize_text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""
