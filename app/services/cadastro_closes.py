"""Clientes que o comercial concluiu no Kanban e o que já virou cadastro no mês."""
from __future__ import annotations

from datetime import date

from app.services.campaign_leads import cadastro_url, links_by_order
from app.services.legacy_core import normalize_text
from app.services.service_orders import DONE_QUEUE_ID, is_commercial_sector, list_orders_by_sector

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
    for card in _done_campaign_cards():
        link = card.get("link") or {}
        match = _find_registration(link, index)
        item = {
            "empresa": normalize_text(link["empresa"]) or "Cliente sem nome",
            "phone": normalize_text(link["phone"]) or "Sem número",
            "contact_name": normalize_text(link["contact_name"]),
        }
        concluded_on = card.get("updated_at") or ""
        if match is None or not _saved_for_this_close(match, concluded_on):
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

    cards: list[dict] = []
    for sector in list_sectors():
        name = sector.get("name") or ""
        if not is_commercial_sector(name):
            continue
        rows = list_orders_by_sector(name)
        links = links_by_order([row.get("id", "") for row in rows])
        for card in rows:
            if card.get("queue_id") != DONE_QUEUE_ID:
                continue
            link = links.get(card.get("id"))
            if not link and normalize_text(card.get("created_by")) != "Leads Raissa":
                continue
            card["link"] = link or {
                "empresa": card.get("empresa") or "",
                "phone": "",
                "email": "",
                "contact_name": "",
                "creative": "",
                "campaign": "",
                "city": "",
                "uf": "",
            }
            if not card["link"].get("empresa"):
                card["link"]["empresa"] = card.get("empresa") or ""
            cards.append(card)
    return cards


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
        }
        for row in rows
    ]


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
