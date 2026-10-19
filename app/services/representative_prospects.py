"""Aba semanal do representante (planilha nova) e o envio do cadastro salvo para a Raissa.

A planilha nova guarda só empresa, CNPJ, telefone e UF. Boleto, senha, login e status
nascem no cadastro e vão para a aba Raissa no salvar.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.crm_local_db import _connect, _lock, init_crm_local_db
from app.services.legacy_core import normalize_text

RAISSA_NEW_STATUS = "1 Boleto Aguardando"

_COLUMN_FIELDS = {
    "empresa": ("nome da empresa", "empresa", "razao social"),
    "cnpj": ("cnpj",),
    "telefone": ("telefone", "celular", "whatsapp", "celular whatsapp"),
    "uf": ("uf", "estado"),
}


def _fold(value) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value).lower())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text).strip()


_UF_NAMES = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AP": "Amapá",
    "AM": "Amazonas",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais",
    "PA": "Pará",
    "PB": "Paraíba",
    "PR": "Paraná",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul",
    "RO": "Rondônia",
    "RR": "Roraima",
    "SC": "Santa Catarina",
    "SP": "São Paulo",
    "SE": "Sergipe",
    "TO": "Tocantins",
}
_NAME_TO_UF = { _fold(name): code for code, name in _UF_NAMES.items() }


class RepresentativeSheetError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None).isoformat(timespec="seconds")


def _tab_piece(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"[:\\/?*\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_tab_title(title: str) -> str:
    text = normalize_text(title).replace("—", "-").replace("–", "-")
    text = re.sub(r"\s*-\s*", "_", text)
    text = re.sub(r"\s*_\s*", "_", text)
    return text.strip("_ ")


def _core_tab_title(title: str) -> str:
    """Ceara_Raphael_01 para Raphael. continua sendo a aba Ceara_Raphael_01."""
    text = _clean_tab_title(title).strip(" .")
    match = re.match(r"^(.+?)_([^_\s]+)_(\d+)\b", text)
    if match:
        return f"{match.group(1)}_{match.group(2)}_{match.group(3)}"
    return text


def parse_prospect_tab(title: str) -> dict | None:
    """São Paulo_José_01 → estado, primeiro nome e lote. Também aceita hífen."""
    parts = [normalize_text(part) for part in _core_tab_title(title).split("_") if normalize_text(part)]
    if len(parts) == 3 and re.fullmatch(r"\d+", parts[2]):
        state, representative, batch = parts
    elif len(parts) == 2 and re.fullmatch(r"\d+", parts[1]):
        return None
    elif len(parts) == 2:
        state, representative, batch = parts[0], parts[1], "01"
    else:
        return None
    if len(state) < 2 or len(representative) < 2:
        return None
    return {"state": state, "representative": representative, "batch": batch, "title": normalize_text(title)}


def _state_keys(*labels: str) -> set[str]:
    keys: set[str] = set()
    for label in labels:
        folded = _fold(label)
        if not folded:
            continue
        keys.add(folded)
        code = folded.upper() if folded.upper() in _UF_NAMES else _NAME_TO_UF.get(folded, "")
        if not code and len(folded) == 2 and folded.upper() in _UF_NAMES:
            code = folded.upper()
        if code:
            keys.add(_fold(code))
            keys.add(_fold(_UF_NAMES[code]))
    return keys


def _first_name(value: str) -> str:
    parts = normalize_text(value).split()
    return parts[0] if parts else ""


def expected_tab_title(person: dict) -> str:
    """Nome da aba que o cadastro cria: Ceará_Lia_01."""
    region = normalize_text(person.get("region")).upper()
    state = _UF_NAMES.get(region) or normalize_text(person.get("state_name")) or normalize_text(person.get("city")) or "Estado"
    name = _first_name(person.get("name", "")) or "Representante"
    return f"{_tab_piece(state)}_{_tab_piece(name)}_01"


def accepted_tab_examples(person: dict) -> list[str]:
    titles = [expected_tab_title(person)]
    city = _tab_piece(person.get("city") or "")
    first = _tab_piece(_first_name(person.get("name") or ""))
    if city and first:
        city_title = f"{city}_{first}_01"
        if _fold(city_title) != _fold(titles[0]):
            titles.append(city_title)
    return titles


def _same_representative_name(tab_name: str, person_name: str) -> bool:
    rep = _fold(tab_name)
    first = _fold(_first_name(person_name))
    full = _fold(person_name)
    if not rep:
        return False
    if rep in {first, full}:
        return True
    return bool(first) and rep.startswith(first + " ")


_TAB_NOISE = {
    "aba",
    "planilha",
    "base",
    "lista",
    "leads",
    "lead",
    "rep",
    "representante",
    "de",
    "da",
    "do",
    "dos",
    "das",
    "para",
}


def _person_places(person: dict) -> set[str]:
    return _state_keys(person.get("state_name", ""), person.get("region", ""), person.get("city", ""))


def _title_tokens(title: str) -> list[str]:
    text = re.sub(r"[^\w\s_]", " ", _fold(_clean_tab_title(title)))
    return [part for part in re.split(r"[\s_]+", text) if part]


def tab_matches_representative(title: str, person: dict) -> bool:
    if normalize_text(person.get("kind")) not in {"", "representante"}:
        return False
    places = _person_places(person)
    parsed = parse_prospect_tab(title)
    if parsed is not None:
        same_place = bool(_state_keys(parsed["state"]) & places)
        return same_place and _same_representative_name(parsed["representative"], person.get("name", ""))
    tokens = _title_tokens(title)
    first = _fold(_first_name(person.get("name", "")))
    full_tokens = [part for part in _fold(person.get("name", "")).split() if part]
    if not tokens or not first or (first not in tokens and tokens != full_tokens):
        return False
    extras = [token for token in tokens if token not in set(full_tokens)]
    if all(re.fullmatch(r"\d+", token) or token in places or token in _TAB_NOISE for token in extras):
        return True
    return False


def read_prospect_rows(values: list[list[str]]) -> list[dict]:
    """Lê Nome da empresa, CNPJ, Telefone e UF. Ignora linha vazia."""
    if not values:
        return []
    headers = [_fold(cell) for cell in values[0]]
    indexes: dict[str, int] = {}
    for field, aliases in _COLUMN_FIELDS.items():
        for alias in aliases:
            if alias in headers:
                indexes[field] = headers.index(alias)
                break
    body = values[1:]
    if "empresa" not in indexes or "cnpj" not in indexes:
        has_data = any(any(normalize_text(cell) for cell in row) for row in body)
        if not has_data:
            return []
        raise RepresentativeSheetError(
            "A aba precisa das colunas Nome da empresa e CNPJ."
        )
    rows = []
    for offset, raw in enumerate(values[1:], start=2):
        def cell(field: str) -> str:
            index = indexes.get(field)
            if index is None or index >= len(raw):
                return ""
            return normalize_text(raw[index])

        empresa = cell("empresa")
        cnpj = cell("cnpj")
        if not empresa and not cnpj:
            continue
        rows.append(
            {
                "sheet_row": offset,
                "empresa": empresa,
                "cnpj": cnpj,
                "telefone": cell("telefone"),
                "uf": cell("uf").upper()[:2],
            }
        )
    return rows


def registration_fields_from_prospect(company: dict, representative_name: str) -> dict:
    """O que a lista semanal leva para o cadastro. O resto o representante preenche."""
    return {
        "empresa": normalize_text(company.get("empresa")),
        "cnpj": normalize_text(company.get("cnpj")),
        "telefone_b2b": normalize_text(company.get("telefone")),
        "uf": normalize_text(company.get("uf")).upper()[:2],
        "vendedor": normalize_text(representative_name),
    }


PROSPECT_QUEUES = (
    ("entrada", "Lista"),
    ("contato", "Em contato"),
    ("proposta", "Proposta"),
    ("concluida", "Concluído"),
)
_QUEUE_IDS = {queue_id for queue_id, _name in PROSPECT_QUEUES}


def _cnpj_digits(value: str) -> str:
    return re.sub(r"\D", "", normalize_text(value))


def _queue_id(value: str) -> str:
    queue = normalize_text(value).lower()
    return queue if queue in _QUEUE_IDS else "entrada"


def _prospect_key(row: dict) -> tuple[str, int]:
    try:
        sheet_row = int(row.get("sheet_row") or 0)
    except (TypeError, ValueError):
        sheet_row = 0
    return normalize_text(row.get("tab_title")), sheet_row


def merge_prospect_rows(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Atualiza a lista da aba e mantém a coluna e o fechamento já feitos."""
    by_pos = {_prospect_key(row): row for row in existing}
    by_cnpj = {}
    for row in existing:
        digits = _cnpj_digits(row.get("cnpj", ""))
        if digits:
            by_cnpj[digits] = row
    merged = []
    seen_pos = set()
    seen_cnpj = set()
    for row in incoming:
        key = _prospect_key(row)
        digits = _cnpj_digits(row.get("cnpj", ""))
        previous = by_pos.get(key) or (by_cnpj.get(digits) if digits else None) or {}
        merged.append(
            {
                "tab_title": key[0],
                "sheet_row": key[1],
                "empresa": normalize_text(row.get("empresa")),
                "cnpj": normalize_text(row.get("cnpj")),
                "telefone": normalize_text(row.get("telefone")),
                "uf": normalize_text(row.get("uf")).upper()[:2],
                "queue": _queue_id(previous.get("queue", "")),
                "registered": 1 if previous.get("registered") else 0,
                "registration_row": int(previous.get("registration_row") or 0),
                "order_id": normalize_text(previous.get("order_id")),
            }
        )
        seen_pos.add(key)
        if digits:
            seen_cnpj.add(digits)
    for row in existing:
        if not row.get("registered"):
            continue
        key = _prospect_key(row)
        digits = _cnpj_digits(row.get("cnpj", ""))
        if key in seen_pos or (digits and digits in seen_cnpj):
            continue
        merged.append(
            {
                "tab_title": key[0] or "cadastro",
                "sheet_row": key[1],
                "empresa": normalize_text(row.get("empresa")),
                "cnpj": normalize_text(row.get("cnpj")),
                "telefone": normalize_text(row.get("telefone")),
                "uf": normalize_text(row.get("uf")).upper()[:2],
                "queue": "concluida",
                "registered": 1,
                "registration_row": int(row.get("registration_row") or 0),
                "order_id": normalize_text(row.get("order_id")),
            }
        )
    return merged


def build_prospect_board(rows: list[dict]) -> list[dict]:
    """Kanban só com empresas ainda não cadastradas."""
    open_rows = [row for row in rows if not row.get("registered")]
    columns = []
    for queue_id, name in PROSPECT_QUEUES:
        cards = []
        for row in open_rows:
            if _queue_id(row.get("queue", "")) != queue_id:
                continue
            cards.append({**row, "queue": queue_id, "cadastro_url": prospect_cadastro_url(row)})
        columns.append({"id": queue_id, "name": name, "cards": cards})
    return columns


def closed_companies(rows: list[dict]) -> list[dict]:
    closed = [row for row in rows if row.get("registered")]
    closed.sort(key=lambda row: _fold(row.get("empresa")))
    return closed


def prospect_cadastro_url(row: dict) -> str:
    params = {
        "from": "activities",
        "empresa": normalize_text(row.get("empresa")),
        "cnpj": normalize_text(row.get("cnpj")),
        "telefone_b2b": normalize_text(row.get("telefone")),
        "uf": normalize_text(row.get("uf")).upper()[:2],
        "prospect_tab": normalize_text(row.get("tab_title")),
        "prospect_row": str(int(row.get("sheet_row") or 0)),
    }
    return "/cadastro/novo?" + urlencode(params)


def seller_for_representative(session: dict | None) -> str:
    data = session or {}
    if normalize_text(data.get("org_person_kind")) != "representante":
        return ""
    return normalize_text(data.get("org_person_name"))


def prospect_summaries() -> dict[str, dict]:
    """Leitura local. Não abre a planilha."""
    init_crm_local_db()
    with _connect() as conn:
        found = conn.execute(
            """
            SELECT person_id, tab_title, COUNT(*) AS total
            FROM representative_prospects
            GROUP BY person_id, tab_title
            ORDER BY tab_title
            """
        ).fetchall()
    grouped: dict[str, dict] = {}
    for row in found:
        item = grouped.setdefault(row["person_id"], {"tabs": [], "count": 0})
        item["tabs"].append(row["tab_title"])
        item["count"] += int(row["total"])
    return grouped


def _service_account_email() -> str:
    try:
        from app.services.legacy_core import _load_google_credentials_info

        return normalize_text(_load_google_credentials_info().get("client_email"))
    except Exception:
        return ""


PROSPECT_HEADERS = ["Nome da empresa", "CNPJ", "Telefone", "UF"]


def _missing_tab_message(person: dict) -> str:
    examples = " ou ".join(accepted_tab_examples(person))
    return (
        f"Não encontrei a aba {examples} na planilha de representantes "
        "nem na planilha principal."
    )


def _prospect_sheet_ids() -> list[str]:
    found = []
    for sheet_id in (settings.representative_sheet_id, settings.sheet_id):
        clean = normalize_text(sheet_id)
        if clean and clean not in found:
            found.append(clean)
    return found


def _open_prospect_spreadsheets():
    from app.services.legacy_core import get_gsheet_client

    if not settings.sheets_configured:
        raise RepresentativeSheetError("Planilha não configurada.")
    client = get_gsheet_client()
    opened = []
    last_error = None
    for sheet_id in _prospect_sheet_ids():
        try:
            opened.append(client.open_by_key(sheet_id))
        except Exception as exc:
            last_error = exc
    if opened:
        return opened
    email = _service_account_email()
    share = f" Compartilhe a planilha com {email}." if email else ""
    raise RepresentativeSheetError(f"Não consegui abrir a planilha de prospecção.{share}") from last_error


def _candidate_worksheets(person: dict) -> tuple[list, list[str]]:
    matched = []
    titles: list[str] = []
    seen = set()
    for spreadsheet in _open_prospect_spreadsheets():
        for worksheet in spreadsheet.worksheets():
            titles.append(worksheet.title)
            if not tab_matches_representative(worksheet.title, person):
                continue
            key = (_fold(getattr(spreadsheet, "id", "")), _fold(worksheet.title))
            if key in seen:
                continue
            seen.add(key)
            matched.append(worksheet)
    return matched, titles


def _nearby_tab_titles(person: dict, titles: list[str]) -> list[str]:
    first = _fold(_first_name(person.get("name", "")))
    if not first:
        return []
    found = []
    for title in titles:
        if first in _title_tokens(title) and title not in found:
            found.append(title)
    return found[:8]


def _short_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", normalize_text(str(exc)))
    return text[:180]


def _ensure_representative_worksheet(person: dict):
    """Cria Ceará_Nome_01 na planilha de representantes quando a aba ainda não existe."""
    from app.services.legacy_core import get_gsheet_client

    title = expected_tab_title(person)
    spreadsheet = get_gsheet_client().open_by_key(settings.representative_sheet_id)
    for worksheet in spreadsheet.worksheets():
        if tab_matches_representative(worksheet.title, person):
            return worksheet
    worksheet = spreadsheet.add_worksheet(title, rows=300, cols=len(PROSPECT_HEADERS))
    worksheet.update([PROSPECT_HEADERS], "A1", value_input_option="USER_ENTERED")
    return worksheet


def _row_dict(row) -> dict:
    return {
        "person_id": row["person_id"],
        "tab_title": row["tab_title"],
        "sheet_row": int(row["sheet_row"]),
        "empresa": row["empresa"] or "",
        "cnpj": row["cnpj"] or "",
        "telefone": row["telefone"] or "",
        "uf": row["uf"] or "",
        "queue": _queue_id(row["queue"] if "queue" in row.keys() else ""),
        "registered": int(row["registered"] or 0) if "registered" in row.keys() else 0,
        "registration_row": int(row["registration_row"] or 0) if "registration_row" in row.keys() else 0,
        "order_id": normalize_text(row["order_id"]) if "order_id" in row.keys() else "",
    }


def prospect_by_order(order_id: str) -> dict | None:
    """Dados da aba ligados a esta OS: empresa, CNPJ, telefone e UF."""
    clean = normalize_text(order_id)
    if not clean:
        return None
    init_crm_local_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM representative_prospects
            WHERE order_id = ?
            LIMIT 1
            """,
            (clean,),
        ).fetchone()
    return _row_dict(row) if row is not None else None


def list_prospects(person_id: str) -> list[dict]:
    init_crm_local_db()
    with _connect() as conn:
        found = conn.execute(
            """
            SELECT * FROM representative_prospects
            WHERE person_id = ?
            ORDER BY tab_title, sheet_row
            """,
            (normalize_text(person_id),),
        ).fetchall()
    return [_row_dict(row) for row in found]


def _replace_prospects(person_id: str, rows: list[dict]) -> None:
    stamp = _now()
    stored = [
        (
            person_id,
            normalize_text(row.get("tab_title")) or "cadastro",
            int(row.get("sheet_row") or 0),
            normalize_text(row.get("empresa")),
            normalize_text(row.get("cnpj")),
            normalize_text(row.get("telefone")),
            normalize_text(row.get("uf")).upper()[:2],
            _queue_id(row.get("queue", "")),
            1 if row.get("registered") else 0,
            int(row.get("registration_row") or 0),
            normalize_text(row.get("order_id")),
            stamp,
        )
        for row in rows
        if int(row.get("sheet_row") or 0) > 0 or normalize_text(row.get("empresa"))
    ]
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM representative_prospects WHERE person_id = ?", (person_id,))
        conn.executemany(
            """
            INSERT INTO representative_prospects
                (person_id, tab_title, sheet_row, empresa, cnpj, telefone, uf, queue, registered, registration_row, order_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            stored,
        )
        conn.commit()


def link_representative_sheet(person: dict) -> dict:
    """Copia a aba do representante para o banco local. Não grava na Raissa."""
    if normalize_text(person.get("kind")) != "representante":
        raise RepresentativeSheetError("Só representante tem aba de prospecção.")
    person_id = normalize_text(person.get("id"))
    if not person_id:
        raise RepresentativeSheetError("Representante sem identificador.")
    worksheets, seen_titles = _candidate_worksheets(person)
    if not worksheets:
        try:
            worksheets = [_ensure_representative_worksheet(person)]
        except Exception as exc:
            email = _service_account_email()
            nearby = _nearby_tab_titles(person, seen_titles)
            detail = _short_error(exc)
            similar = f" Abas parecidas que encontrei: {', '.join(nearby)}." if nearby else ""
            editor = (
                f" Para eu criar a aba, compartilhe a planilha de representantes como editor com {email}."
                if email
                else ""
            )
            reason = f" Motivo: {detail}." if detail else ""
            raise RepresentativeSheetError(f"{_missing_tab_message(person)}{similar}{editor}{reason}") from exc
    incoming = []
    titles: list[str] = []
    for worksheet in worksheets:
        try:
            values = worksheet.get_all_values() or []
        except Exception as exc:
            raise RepresentativeSheetError(f"Não consegui ler a aba {worksheet.title}.") from exc
        if not any(normalize_text(cell) for row in values for cell in row):
            try:
                worksheet.update([PROSPECT_HEADERS], "A1", value_input_option="USER_ENTERED")
                values = [PROSPECT_HEADERS]
            except Exception:
                values = []
        titles.append(worksheet.title)
        for row in read_prospect_rows(values):
            incoming.append({**row, "tab_title": worksheet.title})
    merged = merge_prospect_rows(list_prospects(person_id), incoming)
    _replace_prospects(person_id, merged)
    return {"tabs": titles, "count": len(incoming)}


def ensure_prospect_orders(person: dict, rows: list[dict]) -> list[dict]:
    """Cada empresa da aba vira uma OS, para o card abrir como no Kanban comercial."""
    from app.services.service_orders import create_representative_order

    person_id = normalize_text(person.get("id"))
    name = normalize_text(person.get("name"))
    if not person_id:
        return rows
    changed = False
    for row in rows:
        if row.get("registered") or normalize_text(row.get("order_id")):
            continue
        if not normalize_text(row.get("empresa")) and not normalize_text(row.get("telefone")):
            continue
        try:
            row["order_id"] = create_representative_order(
                empresa=row.get("empresa") or "",
                phone=row.get("telefone") or "",
                cnpj=row.get("cnpj") or "",
                uf=row.get("uf") or "",
                tab_title=row.get("tab_title") or "",
                representative_name=name,
            )
            changed = True
        except Exception:
            continue
    if changed:
        _replace_prospects(person_id, rows)
    return rows


def move_prospect(person_id: str, prospect_key: str, queue_id: str, *, author: str = "") -> dict:
    queue = _queue_id(queue_id)
    if queue != normalize_text(queue_id).lower():
        raise RepresentativeSheetError("Coluna inválida.")
    tab_title, _, raw_row = normalize_text(prospect_key).rpartition("|")
    try:
        sheet_row = int(raw_row)
    except ValueError:
        sheet_row = 0
    if not tab_title or sheet_row <= 0:
        raise RepresentativeSheetError("Empresa não encontrada.")
    rows = list_prospects(person_id)
    target = None
    for row in rows:
        if row["tab_title"] == tab_title and int(row["sheet_row"]) == sheet_row and not row["registered"]:
            row["queue"] = queue
            target = row
            break
    if target is None:
        raise RepresentativeSheetError("Empresa não encontrada na lista.")
    _replace_prospects(person_id, rows)
    order_id = normalize_text(target.get("order_id"))
    if queue == "concluida" and order_id:
        try:
            from app.services.service_orders import move_service_order

            move_service_order(order_id, "concluida", "", "Representação", author=author or "Representante")
        except ValueError:
            pass
    return {
        "ok": True,
        "queue": queue,
        "order_id": order_id,
        "cadastro_url": prospect_cadastro_url(target) if queue == "concluida" else "",
    }


def remember_closed_company(
    person_id: str,
    *,
    empresa: str,
    cnpj: str = "",
    telefone: str = "",
    uf: str = "",
    registration_row: int = 0,
    tab_title: str = "",
    sheet_row: int = 0,
) -> None:
    """Tira a empresa do Kanban e deixa ela na tela Empresas desse representante."""
    clean_id = normalize_text(person_id)
    if not clean_id or not normalize_text(empresa):
        return
    try:
        wanted_row = int(sheet_row or 0)
    except (TypeError, ValueError):
        wanted_row = 0
    try:
        saved_row = int(registration_row or 0)
    except (TypeError, ValueError):
        saved_row = 0
    digits = _cnpj_digits(cnpj)
    name = _fold(empresa)
    rows = list_prospects(clean_id)
    matched = False
    for row in rows:
        same_pos = bool(tab_title) and row["tab_title"] == normalize_text(tab_title) and int(row["sheet_row"]) == wanted_row
        same_cnpj = bool(digits) and _cnpj_digits(row.get("cnpj", "")) == digits
        same_name = _fold(row.get("empresa")) == name
        if not (same_pos or same_cnpj or same_name):
            continue
        row["registered"] = 1
        row["queue"] = "concluida"
        if saved_row:
            row["registration_row"] = saved_row
        if normalize_text(telefone):
            row["telefone"] = normalize_text(telefone)
        if normalize_text(uf):
            row["uf"] = normalize_text(uf).upper()[:2]
        matched = True
        break
    if not matched:
        rows.append(
            {
                "tab_title": "cadastro",
                "sheet_row": saved_row if saved_row > 0 else max((int(row["sheet_row"]) for row in rows), default=0) + 1,
                "empresa": normalize_text(empresa),
                "cnpj": normalize_text(cnpj),
                "telefone": normalize_text(telefone),
                "uf": normalize_text(uf).upper()[:2],
                "queue": "concluida",
                "registered": 1,
                "registration_row": saved_row,
            }
        )
    _replace_prospects(clean_id, rows)
