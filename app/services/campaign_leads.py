"""Leads da aba Leads Raissa viram cards da coluna Campanha no comercial."""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime
from urllib.parse import urlencode

from app.services.legacy_core import normalize_text
from app.services.registry_store import _lock, connect, init_store

TAB_NAMES = ("Leads Raissa", "Leads Raíssa", "LeadsRaissa")

_HEADER_ALIASES = {
    "empresa": ("empresa", "nome empresa", "nome empresas", "cliente", "razao social", "nome"),
    "phone": ("whatsapp", "telefone", "celular", "fone", "telefone b2b"),
    "email": ("email", "e-mail", "email empresa"),
    "contact_name": ("contato", "nome contato", "nome do contato", "responsavel"),
    "creative": ("criativo", "anuncio", "ad"),
    "campaign": ("campanha",),
    "lead_date": ("data", "data entrada", "entrada", "criado em", "data do lead"),
    "city": ("cidade", "municipio"),
    "uf": ("uf", "estado"),
}


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.replace("_", " ").lower().split())


def _parse_day(value: str) -> str:
    text = normalize_text(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if not match:
        return ""
    day, month, year = match.groups()
    if len(year) == 2:
        year = f"20{year}"
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return ""


def _pick_columns(headers: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    normalized = [_plain(header) for header in headers]
    for key, aliases in _HEADER_ALIASES.items():
        for index, header in enumerate(normalized):
            if header in aliases:
                found[key] = index
                break
    return found


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return normalize_text(row[index])


def read_raissa_leads() -> tuple[list[dict], str]:
    """Lê a aba. O segundo valor é um aviso quando a planilha não abre."""
    try:
        from app.config import settings
        from app.services.legacy_core import get_gsheet_client
        from app.services.sheet_read_cache import get_cached_worksheet_values

        if not settings.sheets_configured:
            return [], "Planilha não configurada."
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        worksheet = None
        wanted = {_plain(name) for name in TAB_NAMES} | {_plain(name).replace(" ", "") for name in TAB_NAMES}
        for item in spreadsheet.worksheets():
            title = _plain(item.title)
            if title in wanted or title.replace(" ", "") in wanted:
                worksheet = item
                break
        if worksheet is None:
            return [], "Aba Leads Raissa não encontrada na planilha."

        values = get_cached_worksheet_values(worksheet.title, worksheet.get_all_values)
        if not values or len(values) < 2:
            return [], ""
        columns = _pick_columns([str(cell) for cell in values[0]])
        leads = []
        for offset, raw in enumerate(values[1:], start=2):
            row = [str(cell) for cell in raw]
            empresa = _cell(row, columns.get("empresa"))
            phone = _cell(row, columns.get("phone"))
            if not empresa and not phone:
                continue
            leads.append(
                {
                    "sheet_row": offset,
                    "empresa": empresa or "Lead de campanha",
                    "phone": phone,
                    "email": _cell(row, columns.get("email")),
                    "contact_name": _cell(row, columns.get("contact_name")),
                    "creative": _cell(row, columns.get("creative")),
                    "campaign": _cell(row, columns.get("campaign")),
                    "city": _cell(row, columns.get("city")),
                    "uf": _cell(row, columns.get("uf")),
                    "lead_date": _parse_day(_cell(row, columns.get("lead_date"))),
                }
            )
        return leads, ""
    except Exception:
        return [], "Não consegui ler a aba Leads Raissa."


def count_raissa_leads(start: str, end: str) -> tuple[int, str]:
    leads, warning = read_raissa_leads()
    if warning and not leads:
        return 0, warning
    total = 0
    for lead in leads:
        day = lead.get("lead_date") or ""
        if day and not (start <= day <= end):
            continue
        total += 1
    note = warning or "Leads da aba Leads Raissa neste período."
    return total, note


def _existing_rows(conn) -> set[int]:
    rows = conn.execute("SELECT sheet_row FROM campaign_leads").fetchall()
    return {int(row["sheet_row"]) for row in rows}


def sync_campaign_leads(sector_name: str) -> int:
    """Cria um card em Campanha para cada lead novo da aba. Não duplica."""
    from app.services.service_orders import create_campaign_card

    leads, _warning = read_raissa_leads()
    if not leads:
        return 0
    init_store()
    created = 0
    with _lock, connect() as conn:
        taken = _existing_rows(conn)
    for lead in leads:
        if int(lead["sheet_row"]) in taken:
            continue
        parts = [
            f"Campanha: {lead['campaign']}" if lead["campaign"] else "",
            f"Criativo: {lead['creative']}" if lead["creative"] else "",
            f"Contato: {lead['contact_name']}" if lead["contact_name"] else "",
            f"WhatsApp: {lead['phone']}" if lead["phone"] else "",
            f"E-mail: {lead['email']}" if lead["email"] else "",
        ]
        description = "\n".join(part for part in parts if part)
        order_id = create_campaign_card(
            empresa=lead["empresa"],
            subject=lead["creative"] or lead["campaign"] or "Lead de campanha",
            description=description,
            sector=sector_name,
            scheduled_date=lead["lead_date"],
            phone=lead["phone"],
        )
        stamp = datetime.now().isoformat(timespec="seconds")
        with _lock, connect() as conn:
            conn.execute(
                """
                INSERT INTO campaign_leads (
                    id, sheet_row, order_id, empresa, phone, email, contact_name,
                    creative, campaign, city, uf, lead_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"cmp_{uuid.uuid4().hex[:12]}",
                    int(lead["sheet_row"]),
                    order_id,
                    lead["empresa"],
                    lead["phone"],
                    lead["email"],
                    lead["contact_name"],
                    lead["creative"],
                    lead["campaign"],
                    lead["city"],
                    lead["uf"],
                    lead["lead_date"],
                    stamp,
                ),
            )
        created += 1
    return created


def cadastro_url(link: dict) -> str:
    notes = " · ".join(
        part
        for part in (
            f"Campanha {link.get('campaign')}" if link.get("campaign") else "",
            f"criativo {link.get('creative')}" if link.get("creative") else "",
        )
        if part
    )
    params = {
        "empresa": link.get("empresa") or "",
        "nome_contato": link.get("contact_name") or "",
        "telefone_b2b": link.get("phone") or "",
        "email": link.get("email") or "",
        "municipio": link.get("city") or "",
        "uf": link.get("uf") or "",
        "observacoes": notes,
    }
    clean = {key: value for key, value in params.items() if normalize_text(value)}
    if not clean:
        return "/cadastro/novo"
    return "/cadastro/novo?" + urlencode(clean)


def _link_view(row) -> dict:
    link = {
        "order_id": row["order_id"],
        "empresa": row["empresa"] or "",
        "phone": row["phone"] or "",
        "email": row["email"] or "",
        "contact_name": row["contact_name"] or "",
        "creative": row["creative"] or "",
        "campaign": row["campaign"] or "",
        "city": row["city"] or "",
        "uf": row["uf"] or "",
    }
    link["cadastro_url"] = cadastro_url(link)
    return link


def links_by_order(order_ids: list[str]) -> dict[str, dict]:
    ids = [normalize_text(item) for item in order_ids if normalize_text(item)]
    if not ids:
        return {}
    init_store()
    marks = ",".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM campaign_leads WHERE order_id IN ({marks})",
            tuple(ids),
        ).fetchall()
    return {row["order_id"]: _link_view(row) for row in rows}


def campaign_card_extra(order_id: str) -> dict | None:
    found = links_by_order([order_id])
    link = found.get(normalize_text(order_id))
    if not link:
        return None
    return {
        "source": "campanha",
        "creative": link["creative"],
        "campaign": link["campaign"],
        "cadastro_url": link["cadastro_url"],
        "phone": link["phone"],
        "email": link["email"],
        "contact_name": link["contact_name"],
        "city": link["city"],
        "uf": link["uf"],
    }


def attach_campaign_cards(cards: list[dict]) -> None:
    links = links_by_order([card.get("id", "") for card in cards])
    for card in cards:
        link = links.get(card.get("id"))
        if not link:
            card.setdefault("source", "")
            card.setdefault("cadastro_url", "")
            continue
        card["source"] = "campanha"
        card["cadastro_url"] = link["cadastro_url"]
        card["creative"] = link["creative"]
