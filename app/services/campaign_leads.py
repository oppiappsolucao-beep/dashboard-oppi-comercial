"""Leads da aba Leads Raissa viram cards da coluna Campanha no comercial."""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime
from urllib.parse import urlencode

from app.services.legacy_core import normalize_text
from app.services.registry_store import _lock, connect, init_store

TAB_NAMES = ("Leads Raissa", "Leads Raíssa", "LeadsRaissa", "Leads")
COMPANY_TAB_NAMES = ("Raissa", "Raíssa")

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
    match = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        try:
            return datetime(int(year), int(month), int(day)).date().isoformat()
        except ValueError:
            return ""
    if re.fullmatch(r"\d{5}", text):
        serial = int(text)
        if 30000 <= serial <= 65000:
            from datetime import timedelta

            return (datetime(1899, 12, 30) + timedelta(days=serial)).date().isoformat()
    return ""


def _pick_columns(headers: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    normalized = [_plain(header) for header in headers]
    for key, aliases in _HEADER_ALIASES.items():
        if key == "lead_date":
            continue
        for index, header in enumerate(normalized):
            if header in aliases:
                found[key] = index
                break
    for index, header in enumerate(normalized):
        if header in _HEADER_ALIASES["lead_date"] or header.startswith("data"):
            found["lead_date"] = index
            break
    return found


def _detect_date_column(headers: list[str], rows: list[list[str]]) -> int | None:
    picked = _pick_columns(headers).get("lead_date")
    if picked is not None:
        return picked
    best_index = None
    best_score = 0
    width = max((len(row) for row in rows[:40]), default=0)
    for index in range(width):
        score = sum(1 for row in rows[:40] if index < len(row) and _parse_day(row[index]))
        if score > best_score:
            best_score = score
            best_index = index
    if best_score >= 3:
        return best_index
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return normalize_text(row[index])


def _find_raissa_company_worksheet(spreadsheet):
    """Aba dos clientes atuais (matriz e filial). Não é a aba Leads Raissa."""
    exact = []
    loose = []
    for item in spreadsheet.worksheets():
        plain = _plain(item.title)
        compact = plain.replace(" ", "")
        if "lead" in compact:
            continue
        if plain in {"raissa", "raisa"} or compact in {"raissa", "raisa"}:
            exact.append(item)
            continue
        words = [word for word in plain.split() if word not in {"aba", "planilha", "clientes", "base", "dados"}]
        if words == ["raissa"] or "raissa" in compact:
            loose.append(item)
    if exact:
        return exact[0]
    if loose:
        loose.sort(key=lambda item: len(_plain(item.title)))
        return loose[0]
    return None


def _header_index(headers: list[str], aliases: tuple[str, ...], *, skip_words: tuple[str, ...] = ()) -> int | None:
    normalized = [_plain(header) for header in headers]
    for alias in aliases:
        for index, header in enumerate(normalized):
            if any(word in header for word in skip_words):
                continue
            if header == alias:
                return index
    return None


def _company_kind(tipo: str, parent: str, filial_flag: str) -> str:
    flag = _plain(filial_flag)
    if flag in {"sim", "s", "1", "x", "true", "yes", "filial"}:
        return "Filial"
    if flag in {"nao", "não", "n", "0", "matriz"}:
        return "Matriz"
    text = _plain(tipo)
    if "filial" in text:
        return "Filial"
    if "matriz" in text or text in {"sede", "principal"}:
        return "Matriz"
    parent_text = _plain(parent)
    if parent_text in {"matriz", "filial", "sede", "principal", "sim", "nao", "s", "n"}:
        parent = ""
        parent_text = ""
    if parent_text:
        return "Filial"
    return "Matriz"


def read_raissa_companies() -> dict:
    """Clientes atualizados da aba Raissa: matrizes e filiais."""
    empty = {"aba": "", "total": 0, "empresas": [], "aviso": ""}
    sheet = read_raissa_sheet()
    if sheet.get("aviso") and not sheet.get("clientes"):
        empty["aviso"] = sheet["aviso"]
        empty["aba"] = sheet.get("aba") or ""
        return empty
    headers = list(sheet.get("colunas") or [])
    name_index = _header_index(
        headers,
        ("empresa", "nome da empresa", "razao social", "nome fantasia", "cliente", "nome"),
        skip_words=("matriz", "filial"),
    )
    if name_index is None:
        for index, header in enumerate(headers):
            plain = _plain(header)
            if "matriz" in plain or "filial" in plain:
                continue
            if "empresa" in plain or plain.startswith("nome") or "razao" in plain or "cliente" in plain:
                name_index = index
                break
    if name_index is None and headers:
        best_index = 0
        best_count = -1
        for index, header in enumerate(headers):
            count = sum(1 for item in sheet.get("clientes") or [] if normalize_text(item.get(header)))
            if count > best_count:
                best_count = count
                best_index = index
        name_index = best_index
    tipo_index = _header_index(headers, ("tipo", "tipo empresa", "categoria", "vinculo", "classificacao"))
    flag_index = _header_index(headers, ("filial", "e filial", "is filial"))
    parent_index = _header_index(headers, ("empresa matriz", "nome da matriz", "matriz vinculada", "matriz"))
    phone_index = _header_index(
        headers,
        ("cobranca / whatsapp", "cobranca/whatsapp", "whatsapp", "telefone", "celular", "fone", "telefone b2b"),
    )
    email_index = _header_index(headers, ("email", "e-mail", "email empresa"))
    cnpj_index = _header_index(headers, ("cnpj", "cnpj empresa"))
    if cnpj_index is None:
        for index, header in enumerate(headers):
            if "cnpj" in _plain(header):
                cnpj_index = index
                break
    contact_index = _header_index(
        headers,
        ("contato", "nome contato", "nome do contato", "responsavel", "socio"),
        skip_words=("matriz",),
    )
    from app.services.raissa_company_sync import company_hidden_on_raissa_list, names_are_same_company

    empresas = []
    for item in sheet.get("clientes") or []:
        values = [item.get(header, "") for header in headers]
        empresa = values[name_index] if name_index is not None and name_index < len(values) else ""
        empresa = normalize_text(empresa)
        if not empresa:
            continue
        parent = values[parent_index] if parent_index is not None and parent_index < len(values) else ""
        tipo = values[tipo_index] if tipo_index is not None and tipo_index < len(values) else ""
        flag = values[flag_index] if flag_index is not None and flag_index < len(values) else ""
        kind = _company_kind(tipo, parent, flag)
        cnpj = normalize_text(values[cnpj_index]) if cnpj_index is not None and cnpj_index < len(values) else ""
        if company_hidden_on_raissa_list(empresa, cnpj):
            continue
        row_item = {
            "linha": item.get("linha") or 0,
            "empresa": empresa,
            "tipo": kind,
            "matriz": normalize_text(parent),
            "telefone": normalize_text(values[phone_index]) if phone_index is not None and phone_index < len(values) else "",
            "email": normalize_text(values[email_index]) if email_index is not None and email_index < len(values) else "",
            "cnpj": cnpj,
            "contato": normalize_text(values[contact_index]) if contact_index is not None and contact_index < len(values) else "",
        }
        duplicate_at = next(
            (
                index
                for index, current in enumerate(empresas)
                if names_are_same_company(current.get("empresa", ""), empresa)
            ),
            None,
        )
        if duplicate_at is not None:
            empresas[duplicate_at] = row_item
            continue
        empresas.append(row_item)
    return {
        "aba": sheet.get("aba") or "",
        "total": len(empresas),
        "empresas": empresas,
        "aviso": "" if empresas else "A aba Raissa não trouxe empresas.",
    }


def _find_raissa_worksheet(spreadsheet):
    by_title = {}
    for item in spreadsheet.worksheets():
        title = _plain(item.title)
        compact = title.replace(" ", "")
        by_title.setdefault(title, item)
        by_title.setdefault(compact, item)
    for name in TAB_NAMES:
        found = by_title.get(_plain(name)) or by_title.get(_plain(name).replace(" ", ""))
        if found is not None:
            return found
    for item in spreadsheet.worksheets():
        if "raissa" in _plain(item.title).replace(" ", ""):
            return item
    return None


_raissa_last_values: list[list[str]] | None = None
_raissa_last_title = "Raissa"
_raissa_quota_until = 0.0


def _raissa_snapshot_path():
    from app.services.storage_paths import get_storage_dir

    return get_storage_dir() / "raissa_snapshot.json"


def drop_company_from_raissa_cache(empresa: str, cnpj: str, raissa_row: int = 0) -> None:
    """Remove a empresa da cópia local da lista, sem esperar outra leitura do Google."""
    from app.services.raissa_company_sync import _raissa_rows_to_remove
    from app.services.sheet_read_cache import store_worksheet_values

    stored = _stored_raissa_values()
    if not stored:
        return
    title, values = stored
    drop_rows = set(_raissa_rows_to_remove(values, empresa, cnpj, raissa_row))
    if not drop_rows:
        return
    kept = [row for index, row in enumerate(values, start=1) if index not in drop_rows]
    _remember_raissa_values(title, kept)
    store_worksheet_values(title, kept)


def _remember_raissa_values(title: str, values: list[list[str]]) -> None:
    import json

    global _raissa_last_values, _raissa_last_title
    _raissa_last_title = title or "Raissa"
    _raissa_last_values = [list(row) for row in values]
    try:
        path = _raissa_snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"aba": _raissa_last_title, "values": _raissa_last_values}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _stored_raissa_values() -> tuple[str, list[list[str]]] | None:
    import json

    if _raissa_last_values:
        return _raissa_last_title, [list(row) for row in _raissa_last_values]
    path = _raissa_snapshot_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            values = data.get("values") if isinstance(data, dict) else None
            if isinstance(values, list) and values:
                return str(data.get("aba") or "Raissa"), values
        except (OSError, json.JSONDecodeError):
            pass
    try:
        from app.services.sheet_read_cache import peek_cached_worksheet_values

        for title in ("Raissa", "Raíssa", "Raisa"):
            cached = peek_cached_worksheet_values(title)
            if cached:
                return title, cached
    except Exception:
        pass
    try:
        from app.config import settings
        from app.services.legacy_core import get_last_good_sheet_values, hydrate_sheet_cache_from_disk

        tab = _plain(settings.worksheet_name).replace(" ", "")
        if "raissa" in tab or "raisa" in tab:
            values = get_last_good_sheet_values()
            if not values and hydrate_sheet_cache_from_disk():
                values = get_last_good_sheet_values()
            if values:
                return settings.worksheet_name, values
    except Exception:
        pass
    return None


def _quota_limited(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "quota" in text


def _raissa_sheet_from_values(title: str, values: list[list[str]]) -> dict:
    empty = {"aba": title, "total": 0, "colunas": [], "clientes": [], "aviso": ""}
    if not values:
        empty["aviso"] = "A aba Raissa está vazia."
        return empty
    header_at = 0
    if len(values) > 1:
        filled_first = sum(1 for cell in values[0] if normalize_text(cell))
        filled_second = sum(1 for cell in values[1] if normalize_text(cell))
        if filled_first <= 1 and filled_second >= 3:
            header_at = 1
    headers = []
    used = set()
    for index, cell in enumerate(values[header_at], start=1):
        name = normalize_text(cell) or f"Coluna {index}"
        key = name
        suffix = 2
        while key.lower() in used:
            key = f"{name} {suffix}"
            suffix += 1
        used.add(key.lower())
        headers.append(key)
    clientes = []
    for offset, raw in enumerate(values[header_at + 1 :], start=header_at + 2):
        row = [normalize_text(cell) for cell in raw]
        if not any(row):
            continue
        item = {"linha": offset}
        for index, header in enumerate(headers):
            item[header] = row[index] if index < len(row) else ""
        clientes.append(item)
    return {
        "aba": title,
        "total": len(clientes),
        "colunas": headers,
        "clientes": clientes,
        "aviso": "",
    }


def _fetch_raissa_values() -> tuple[str, list[list[str]]]:
    from app.config import settings
    from app.services.legacy_core import get_gsheet_client
    from app.services.sheet_read_cache import get_cached_worksheet_values

    client = get_gsheet_client()
    spreadsheet = client.open_by_key(settings.sheet_id)
    worksheet = None
    for title in ("Raissa", "Raíssa", "Raisa"):
        try:
            worksheet = spreadsheet.worksheet(title)
            break
        except Exception as exc:
            if _quota_limited(exc):
                raise
            worksheet = None
    if worksheet is None:
        worksheet = _find_raissa_company_worksheet(spreadsheet)
    if worksheet is None:
        raise RuntimeError("Aba Raissa não encontrada na planilha.")
    values = get_cached_worksheet_values(worksheet.title, worksheet.get_all_values)
    if not values:
        raise RuntimeError("Não consegui ler a aba Raissa.")
    return worksheet.title, values


def read_raissa_sheet() -> dict:
    """Lê a aba Raissa inteira, com as colunas como estão na planilha."""
    import time

    global _raissa_quota_until
    empty = {"aba": "", "total": 0, "colunas": [], "clientes": [], "aviso": ""}
    try:
        from app.config import settings

        if not settings.sheets_configured:
            empty["aviso"] = "Planilha não configurada."
            return empty
        if time.time() < _raissa_quota_until:
            stored = _stored_raissa_values()
            if stored:
                return _raissa_sheet_from_values(stored[0], stored[1])
            empty["aviso"] = (
                "O Google limitou a leitura da aba Raissa por um minuto. "
                "Os clientes continuam na planilha. Atualize de novo daqui a pouco."
            )
            return empty
        from app.services.sheet_read_cache import peek_fresh_worksheet_values

        for title in ("Raissa", "Raíssa", "Raisa"):
            fresh = peek_fresh_worksheet_values(title)
            if fresh:
                return _raissa_sheet_from_values(title, fresh)
        title, values = _fetch_raissa_values()
        _remember_raissa_values(title, values)
        return _raissa_sheet_from_values(title, values)
    except Exception as exc:
        if _quota_limited(exc):
            _raissa_quota_until = time.time() + 70
        stored = _stored_raissa_values()
        if stored:
            return _raissa_sheet_from_values(stored[0], stored[1])
        if _quota_limited(exc):
            empty["aviso"] = (
                "O Google limitou a leitura da aba Raissa por um minuto. "
                "Os clientes continuam na planilha. Atualize de novo daqui a pouco."
            )
            return empty
        detail = normalize_text(str(exc))[:140]
        empty["aviso"] = f"Não consegui ler a aba Raissa. {detail}".strip()
        return empty


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
        worksheet = _find_raissa_worksheet(spreadsheet)
        if worksheet is None:
            return [], "Aba de leads não encontrada na planilha."

        values = get_cached_worksheet_values(worksheet.title, worksheet.get_all_values)
        if not values or len(values) < 2:
            return [], ""
        header_row = [str(cell) for cell in values[0]]
        body = [[str(cell) for cell in raw] for raw in values[1:]]
        columns = _pick_columns(header_row)
        columns["lead_date"] = _detect_date_column(header_row, body)
        leads = []
        for offset, row in enumerate(body, start=2):
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
        return [], "Não consegui ler a aba de leads."


def count_raissa_leads(start: str, end: str) -> tuple[int, str]:
    leads, warning = read_raissa_leads()
    if warning and not leads:
        return 0, warning
    total = 0
    for lead in leads:
        day = lead.get("lead_date") or ""
        if not day or not (start <= day <= end):
            continue
        total += 1
    note = warning or "Leads novos neste período."
    return total, note


def _lead_key(phone: str, empresa: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) >= 8:
        return f"tel:{digits[-11:]}"
    name = normalize_text(empresa).lower()
    return f"nome:{name}" if name and name != "lead de campanha" else ""


def sync_campaign_leads(sector_name: str) -> int:
    """Cria o card só quando o lead tem data. Atualiza a data dos que já existem."""
    from app.services.service_orders import create_campaign_card

    leads, _warning = read_raissa_leads()
    if not leads:
        return 0
    init_store()
    created = 0
    with _lock, connect() as conn:
        existing = conn.execute(
            "SELECT id, sheet_row, order_id, phone, empresa, lead_date FROM campaign_leads"
        ).fetchall()
    by_row = {int(row["sheet_row"]): row for row in existing}
    by_key = {}
    for row in existing:
        key = _lead_key(row["phone"] or "", row["empresa"] or "")
        if key:
            by_key[key] = row
    for lead in leads:
        if not lead["lead_date"]:
            continue
        key = _lead_key(lead["phone"], lead["empresa"])
        current = by_row.get(int(lead["sheet_row"])) or (by_key.get(key) if key else None)
        if current is not None:
            _refresh_lead_date(current, lead)
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
        by_row[int(lead["sheet_row"])] = {"order_id": order_id, "lead_date": lead["lead_date"]}
        if key:
            by_key[key] = by_row[int(lead["sheet_row"])]
    return created


def _refresh_lead_date(current, lead: dict) -> None:
    with _lock, connect() as conn:
        if normalize_text(current["lead_date"]) != lead["lead_date"]:
            conn.execute(
                """
                UPDATE campaign_leads
                SET lead_date = ?, sheet_row = ?, phone = ?, empresa = ?
                WHERE id = ?
                """,
                (
                    lead["lead_date"],
                    int(lead["sheet_row"]),
                    lead["phone"],
                    lead["empresa"],
                    current["id"],
                ),
            )
        conn.execute(
            "UPDATE service_orders SET scheduled_date = ? WHERE id = ? AND scheduled_date != ?",
            (lead["lead_date"], current["order_id"], lead["lead_date"]),
        )


def open_lead_cadastro_url(order: dict) -> str:
    """Cadastro do lead ao concluir a OS no Comercial ou na Oppi Tech."""
    client = order.get("client") if isinstance(order.get("client"), dict) else {}
    enriched = dict(order)
    enriched["contact_name"] = order.get("contact_name") or client.get("contato") or ""
    enriched["phone"] = order.get("phone") or client.get("whatsapp") or client.get("telefone") or ""
    enriched["email"] = order.get("email") or client.get("email") or ""
    enriched["city"] = order.get("city") or client.get("cidade") or ""
    enriched["uf"] = order.get("uf") or client.get("uf") or ""
    enriched["order_id"] = order.get("order_id") or order.get("id") or ""
    url = finish_cadastro_url(enriched)
    if not url.startswith("/cadastro/"):
        return ""
    if "from=" not in url:
        url += ("&" if "?" in url else "?") + "from=activities"
    return url


def finish_cadastro_url(order: dict) -> str:
    """Endereço do cadastro ao concluir um lead comercial."""
    current = normalize_text(order.get("cadastro_url"))
    if current.startswith("/cadastro/"):
        return current
    text = order.get("description") or ""

    def line(label: str) -> str:
        match = re.search(rf"(?im)^{re.escape(label)}:\s*(.+)$", text)
        return normalize_text(match.group(1)) if match else ""

    return cadastro_url(
        {
            "empresa": order.get("empresa") or "",
            "contact_name": order.get("contact_name") or line("Contato"),
            "phone": order.get("phone") or line("WhatsApp"),
            "email": order.get("email") or line("E-mail"),
            "campaign": order.get("campaign") or line("Campanha"),
            "creative": order.get("creative") or line("Criativo") or order.get("subject") or "",
            "city": order.get("city") or "",
            "uf": order.get("uf") or "",
            "order_id": order.get("order_id") or order.get("id") or "",
        }
    )


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
        "os": link.get("order_id") or "",
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
        "lead_date": row["lead_date"] if "lead_date" in row.keys() else "",
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
        "lead_date": link.get("lead_date") or "",
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
        card["phone"] = link.get("phone") or ""
        if link.get("lead_date"):
            card["lead_date"] = link["lead_date"]
