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
                _set_company_links(item, match, link)
                registered.append(item)
            continue
        if match is None or (not linked and not _saved_for_this_close(match, concluded_on)):
            item["transfer_url"] = cadastro_url(link)
            _set_company_links(item, match, link)
            pending.append(item)
            continue
        closed_on = _stamp_day(card.get("updated_at")) or _stamp_day(match.get("created_at")) or _stamp_day(match.get("data_chamado"))
        if closed_on and start <= closed_on <= end:
            _set_company_links(item, match, link)
            registered.append(item)
    return {
        "pending": pending,
        "pending_total": len(pending),
        "registered": registered,
        "registered_total": len(registered),
        "month_label": f"{_MONTHS[today.month]} de {today.year}",
    }


def _set_company_links(item: dict, match: dict | None, link: dict) -> None:
    companies = company_family_links(
        sheet_row=int((match or {}).get("sheet_row") or 0),
        order_id=item.get("order_id") or "",
        phone=link.get("phone") or "",
        origin="leads",
    )
    item["companies"] = companies
    if companies:
        item["company_url"] = companies[0]["url"]
        if not item.get("contact_name"):
            item["contact_name"] = next(
                (company.get("contact_name") for company in companies if company.get("contact_name")),
                "",
            )
        empresa = normalize_text(item.get("empresa"))
        if empresa.lower() in {"", "-", "—", "lead de campanha", "cliente sem nome"}:
            named = next((company.get("empresa") for company in companies if company.get("empresa")), "")
            if named:
                item["empresa"] = named
    elif match and match.get("sheet_row"):
        item["company_url"] = _company_url(match.get("sheet_row"))
    else:
        item["transfer_url"] = cadastro_url(link)


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


def contacts_linked_to_orders(order_ids: list[str]) -> dict[str, str]:
    """Nome do contato já salvo no cadastro ligado a cada OS."""
    wanted = {normalize_text(item) for item in order_ids if normalize_text(item)}
    if not wanted:
        return {}
    try:
        from app.services.crm_registrations_storage import is_crm_postgres_ready
        from database.connection import SessionLocal
        from database.models import CrmRegistration
    except Exception:
        return {}
    if not is_crm_postgres_ready():
        return {}
    db = SessionLocal()
    try:
        rows = (
            db.query(CrmRegistration.nome_contato, CrmRegistration.extras_json)
            .filter(CrmRegistration.cadastro_ativo.is_(True))
            .filter(CrmRegistration.extras_json.contains("campaign_order_id"))
            .all()
        )
    except Exception:
        return {}
    finally:
        db.close()
    found: dict[str, str] = {}
    for nome, extras in rows:
        order_id = _order_id_from_extras(extras)
        contact = normalize_text(nome)
        if order_id in wanted and contact and order_id not in found:
            found[order_id] = contact
    return found


def company_family_links(
    *,
    sheet_row: int = 0,
    order_id: str = "",
    phone: str = "",
    origin: str = "activities",
) -> list[dict]:
    """Empresa ligada à OS, com a matriz e as filiais quando houver mais de uma."""
    try:
        from app.services.crm_registrations_storage import is_crm_postgres_ready
        from database.connection import SessionLocal
        from database.models import CrmRegistration
    except Exception:
        return []
    if not is_crm_postgres_ready():
        return _links_from_sheet_row(sheet_row, origin)
    order_id = normalize_text(order_id)
    phone_key = _phone_key(phone)
    db = SessionLocal()
    try:
        seeds = []
        if int(sheet_row or 0):
            row = (
                db.query(CrmRegistration)
                .filter(CrmRegistration.sheet_row == int(sheet_row))
                .first()
            )
            if row is not None:
                seeds.append(row)
        if order_id:
            tied = (
                db.query(CrmRegistration)
                .filter(CrmRegistration.extras_json.contains(order_id))
                .all()
            )
            seeds.extend(
                row for row in tied
                if _order_id_from_extras(row.extras_json) == order_id
            )
        if phone_key:
            known = {int(row.sheet_row or 0) for row in seeds}
            for row in _rows_matching_phone(db, phone):
                number = int(row.sheet_row or 0)
                if number not in known:
                    seeds.append(row)
                    known.add(number)
        if not seeds:
            return []
        matriz_ids: set[int] = set()
        for row in seeds:
            number = int(row.sheet_row or 0)
            parent = int(row.empresa_matriz_sheet_row or 0)
            if row.is_filial and parent:
                matriz_ids.add(parent)
            elif number:
                matriz_ids.add(number)
        family = list(seeds)
        if matriz_ids:
            family.extend(
                db.query(CrmRegistration)
                .filter(CrmRegistration.sheet_row.in_(list(matriz_ids)))
                .all()
            )
            family.extend(
                db.query(CrmRegistration)
                .filter(CrmRegistration.empresa_matriz_sheet_row.in_(list(matriz_ids)))
                .all()
            )
        unique: dict[int, object] = {}
        for row in family:
            number = int(row.sheet_row or 0)
            if number and getattr(row, "cadastro_ativo", True):
                unique[number] = row
        links = [_company_link(row, origin) for row in unique.values()]
        links.sort(key=lambda item: (item["kind"] != "Matriz", item["empresa"].lower()))
        return links
    except Exception:
        return _links_from_sheet_row(sheet_row, origin)
    finally:
        db.close()


def _links_from_sheet_row(sheet_row: int, origin: str) -> list[dict]:
    try:
        number = int(sheet_row or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return []
    return [{
        "sheet_row": number,
        "empresa": "",
        "contact_name": "",
        "kind": "Empresa",
        "url": f"/cadastro/todos/{number}/editar?from={origin}",
    }]


def _company_link(row, origin: str) -> dict:
    number = int(row.sheet_row or 0)
    kind = "Filial" if row.is_filial else "Matriz"
    return {
        "sheet_row": number,
        "empresa": normalize_text(row.empresa),
        "contact_name": normalize_text(row.nome_contato),
        "kind": kind,
        "url": f"/cadastro/todos/{number}/editar?from={origin}",
    }


def _company_url(sheet_row) -> str:
    try:
        number = int(sheet_row or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return ""
    return f"/cadastro/todos/{number}/editar?from=leads"


def _phone_key(value: str) -> str:
    """DDD + número, sem o 9 extra do celular. 85 9212-7042 e 85 99212-7042 viram a mesma chave."""
    digits = "".join(ch for ch in normalize_text(value) if ch.isdigit())
    if digits.startswith("0") and len(digits) in {11, 12, 13}:
        digits = digits[1:]
    if digits.startswith("55") and len(digits) > 11:
        digits = digits[2:]
    if len(digits) >= 11 and digits[2] == "9":
        digits = digits[:2] + digits[3:]
    if len(digits) >= 10:
        return digits[-10:]
    if len(digits) >= 8:
        return digits[-8:]
    return ""


def _phone_patterns(phone: str) -> list[str]:
    key = _phone_key(phone)
    if len(key) < 8:
        return []
    tail = key[-8:]
    patterns = [
        tail,
        f"{tail[:4]}-{tail[4:]}",
        f"{tail[:4]} {tail[4:]}",
        f"9{tail}",
        f"9{tail[:4]}-{tail[4:]}",
        f"9 {tail[:4]}-{tail[4:]}",
    ]
    if len(key) >= 10:
        ddd = key[:2]
        patterns.extend((f"{ddd}{tail}", f"{ddd}9{tail}", f"({ddd}) {tail[:4]}-{tail[4:]}", f"({ddd}) 9{tail[:4]}-{tail[4:]}"))
    return list(dict.fromkeys(pattern for pattern in patterns if len(pattern) >= 8))


def _row_phone_keys(row) -> set[str]:
    return {
        key
        for key in (
            _phone_key(getattr(row, "telefone_b2b", "")),
            _phone_key(getattr(row, "telefone_fixo", "")),
            _phone_key(getattr(row, "telefone_alternativo", "")),
        )
        if key
    }


def _rows_matching_phone(db, phone: str):
    from sqlalchemy import or_

    from database.models import CrmRegistration

    key = _phone_key(phone)
    patterns = _phone_patterns(phone)
    if not key or not patterns:
        return []
    clauses = []
    for pattern in patterns:
        like = f"%{pattern}%"
        clauses.extend(
            (
                CrmRegistration.telefone_b2b.ilike(like),
                CrmRegistration.telefone_fixo.ilike(like),
                CrmRegistration.telefone_alternativo.ilike(like),
            )
        )
    rough = (
        db.query(CrmRegistration)
        .filter(CrmRegistration.cadastro_ativo.is_(True))
        .filter(or_(*clauses))
        .limit(80)
        .all()
    )
    matched = [row for row in rough if key in _row_phone_keys(row)]
    if matched:
        return matched
    rows = db.query(CrmRegistration).filter(CrmRegistration.cadastro_ativo.is_(True)).all()
    return [row for row in rows if key in _row_phone_keys(row)]


def lookup_clients_by_phones(phones: list[str]) -> dict[str, dict]:
    """Chave do telefone → nome do contato e empresa já cadastrados."""
    wanted = {_phone_key(phone) for phone in phones if _phone_key(phone)}
    if not wanted:
        return {}
    try:
        from app.services.crm_registrations_storage import is_crm_postgres_ready
        from database.connection import SessionLocal
        from database.models import CrmRegistration
    except Exception:
        return {}
    if not is_crm_postgres_ready():
        return {}
    db = SessionLocal()
    try:
        rows = (
            db.query(
                CrmRegistration.sheet_row,
                CrmRegistration.empresa,
                CrmRegistration.nome_contato,
                CrmRegistration.telefone_b2b,
                CrmRegistration.telefone_fixo,
                CrmRegistration.telefone_alternativo,
            )
            .filter(CrmRegistration.cadastro_ativo.is_(True))
            .all()
        )
    except Exception:
        return {}
    finally:
        db.close()
    found: dict[str, dict] = {}
    for row in rows:
        client = {
            "empresa": normalize_text(row.empresa),
            "contact_name": normalize_text(row.nome_contato),
            "sheet_row": int(row.sheet_row or 0),
        }
        for key in _row_phone_keys(row):
            if key not in wanted:
                continue
            current = found.get(key)
            if current is None or (not current.get("contact_name") and client["contact_name"]):
                found[key] = client
    return found


def _stamp_day(value: str) -> str:
    text = normalize_text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""
