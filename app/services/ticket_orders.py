"""Chamados da aba ticket viram OS. O comercial só escolhe o setor."""
from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime

from app.services.legacy_core import normalize_text
from app.services.registry_store import _lock, connect, init_store

TAB_NAMES = ("ticket", "tickets", "chamados")
_HEADER_ALIASES = {
    "empresa": ("empresa", "nome empresa", "cliente", "razao social", "nome"),
    "phone": ("whatsapp", "telefone", "celular", "fone"),
    "email": ("email", "e-mail"),
    "contact_name": ("contato", "nome contato", "responsavel", "nome do responsavel"),
    "subject": ("assunto", "motivo", "solicitacao", "necessidade", "descricao", "pedido"),
}
_SECTOR_HEADERS = (
    "setor direcionado",
    "setor destino",
    "setor responsavel",
    "setor",
    "destino",
)


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.replace("_", " ").lower().split())


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return normalize_text(row[index])


def sector_column_index(headers: list[str]) -> int | None:
    """Coluna já existente na aba ticket. Não cria coluna nova."""
    folded = [_plain(header) for header in headers]
    for name in _SECTOR_HEADERS:
        if name in folded:
            return folded.index(name) + 1
    for index, header in enumerate(folded):
        if "setor" in header:
            return index + 1
    return None


def _find_ticket_worksheet(spreadsheet):
    by_title = {}
    for item in spreadsheet.worksheets():
        title = _plain(item.title)
        by_title.setdefault(title, item)
        by_title.setdefault(title.replace(" ", ""), item)
    for name in TAB_NAMES:
        found = by_title.get(name) or by_title.get(name.replace(" ", ""))
        if found is not None:
            return found
    return None


def _pick_columns(headers: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    normalized = [_plain(header) for header in headers]
    for key, aliases in _HEADER_ALIASES.items():
        for index, header in enumerate(normalized):
            if header in aliases:
                found[key] = index
                break
    return found


def read_ticket_rows() -> tuple[list[dict], str]:
    try:
        from app.config import settings
        from app.services.legacy_core import get_gsheet_client
        from app.services.sheet_read_cache import get_cached_worksheet_values

        if not settings.sheets_configured:
            return [], "Planilha não configurada."
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        worksheet = _find_ticket_worksheet(spreadsheet)
        if worksheet is None:
            return [], ""
        values = get_cached_worksheet_values(worksheet.title, worksheet.get_all_values)
        if not values or len(values) < 2:
            return [], ""
        header_row = [str(cell) for cell in values[0]]
        columns = _pick_columns(header_row)
        rows = []
        for offset, raw in enumerate(values[1:], start=2):
            row = [str(cell) for cell in raw]
            empresa = _cell(row, columns.get("empresa"))
            phone = _cell(row, columns.get("phone"))
            if not empresa and not phone:
                continue
            rows.append(
                {
                    "sheet_row": offset,
                    "empresa": empresa or "Cliente da base",
                    "phone": phone,
                    "email": _cell(row, columns.get("email")),
                    "contact_name": _cell(row, columns.get("contact_name")),
                    "subject": _cell(row, columns.get("subject")) or "Chamado da base",
                }
            )
        return rows, ""
    except Exception:
        return [], "Não consegui ler a aba ticket."


def sync_ticket_orders(sector_name: str) -> int:
    from app.services.service_orders import create_ticket_card

    rows, _warning = read_ticket_rows()
    if not rows:
        return 0
    init_store()
    created = 0
    with _lock, connect() as conn:
        existing = {
            int(row["sheet_row"])
            for row in conn.execute("SELECT sheet_row FROM ticket_orders").fetchall()
        }
    today = datetime.now().date().isoformat()
    for item in rows:
        if int(item["sheet_row"]) in existing:
            continue
        parts = [
            f"Contato: {item['contact_name']}" if item["contact_name"] else "",
            f"WhatsApp: {item['phone']}" if item["phone"] else "",
            f"E-mail: {item['email']}" if item["email"] else "",
        ]
        order_id = create_ticket_card(
            empresa=item["empresa"],
            subject=item["subject"],
            description="\n".join(part for part in parts if part),
            sector=sector_name,
            scheduled_date=today,
            phone=item["phone"],
        )
        stamp = datetime.now().isoformat(timespec="seconds")
        with _lock, connect() as conn:
            conn.execute(
                """
                INSERT INTO ticket_orders (
                    id, sheet_row, order_id, empresa, phone, email, contact_name, subject, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"tck_{uuid.uuid4().hex[:12]}",
                    int(item["sheet_row"]),
                    order_id,
                    item["empresa"],
                    item["phone"],
                    item["email"],
                    item["contact_name"],
                    item["subject"],
                    stamp,
                ),
            )
        existing.add(int(item["sheet_row"]))
        created += 1
    return created


def ticket_card_extra(order_id: str) -> dict | None:
    init_store()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM ticket_orders WHERE order_id = ?",
            (normalize_text(order_id),),
        ).fetchone()
    if row is None:
        return None
    return {
        "source": "ticket",
        "phone": row["phone"] or "",
        "email": row["email"] or "",
        "contact_name": row["contact_name"] or "",
        "sheet_row_ticket": int(row["sheet_row"] or 0),
        "cadastro_url": "",
    }


def write_directed_sector(sheet_row: int, sector_name: str) -> str:
    """Grava o setor na coluna que já existe na aba ticket."""
    if int(sheet_row or 0) < 2 or not normalize_text(sector_name):
        return ""
    try:
        from app.config import settings
        from app.services.legacy_core import get_gsheet_client

        if not settings.sheets_configured:
            return "Planilha não configurada. A OS foi encaminhada, sem atualizar a aba ticket."
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        worksheet = _find_ticket_worksheet(spreadsheet)
        if worksheet is None:
            return "Aba ticket não encontrada. A OS foi encaminhada."
        headers = worksheet.row_values(1)
        column = sector_column_index(headers)
        if column is None:
            return "A aba ticket não tem coluna de setor. A OS foi encaminhada."
        worksheet.update_cell(int(sheet_row), column, normalize_text(sector_name))
        return ""
    except Exception:
        return "Não consegui gravar o setor na aba ticket. A OS foi encaminhada."


def direct_ticket_order(order_id: str, sector_name: str, author: str) -> str:
    from app.services.service_orders import ENTRY_QUEUE_ID, _add_event, _connect as orders_connect
    from app.services.service_orders import _lock as orders_lock
    from app.services.service_orders import _now

    target = normalize_text(sector_name)
    if not target:
        raise ValueError("Escolha o setor responsável.")
    init_store()
    with connect() as conn:
        link = conn.execute(
            "SELECT sheet_row FROM ticket_orders WHERE order_id = ?",
            (normalize_text(order_id),),
        ).fetchone()
    if link is None:
        raise ValueError("Essa ordem não veio da aba ticket.")
    stamp = _now().isoformat(timespec="seconds")
    with orders_lock, orders_connect() as conn:
        current = conn.execute(
            "SELECT id, sector FROM service_orders WHERE id = ?",
            (normalize_text(order_id),),
        ).fetchone()
        if current is None:
            raise ValueError("Ordem de serviço não encontrada.")
        conn.execute(
            """
            UPDATE service_orders
            SET sector = ?, queue_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (target, ENTRY_QUEUE_ID, stamp, current["id"]),
        )
        _add_event(
            conn,
            current["id"],
            "movida",
            f"Encaminhada para {target}.",
            author,
            stamp,
        )
    return write_directed_sector(int(link["sheet_row"]), target)
