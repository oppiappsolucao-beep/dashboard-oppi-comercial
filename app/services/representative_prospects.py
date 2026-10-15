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


def parse_prospect_tab(title: str) -> dict | None:
    """São Paulo_José_01 → estado, primeiro nome e lote."""
    parts = normalize_text(title).rsplit("_", 2)
    if len(parts) != 3:
        return None
    state, representative, batch = (normalize_text(part) for part in parts)
    if len(state) < 2 or len(representative) < 2 or not re.fullmatch(r"\d+", batch):
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


def tab_matches_representative(title: str, person: dict) -> bool:
    parsed = parse_prospect_tab(title)
    if parsed is None or normalize_text(person.get("kind")) not in {"", "representante"}:
        return False
    same_state = bool(
        _state_keys(parsed["state"])
        & _state_keys(person.get("state_name", ""), person.get("region", ""))
    )
    same_name = _fold(parsed["representative"]) == _fold(_first_name(person.get("name", "")))
    return same_state and same_name


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
    if "empresa" not in indexes or "cnpj" not in indexes:
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


def _matching_worksheets(person: dict):
    from app.services.legacy_core import get_gsheet_client

    if not settings.sheets_configured:
        raise RepresentativeSheetError("Planilha não configurada.")
    try:
        spreadsheet = get_gsheet_client().open_by_key(settings.representative_sheet_id)
        worksheets = spreadsheet.worksheets()
    except Exception as exc:
        email = _service_account_email()
        share = f" Compartilhe a planilha com {email}." if email else ""
        raise RepresentativeSheetError(f"Não consegui abrir a planilha de prospecção.{share}") from exc
    matched = [worksheet for worksheet in worksheets if tab_matches_representative(worksheet.title, person)]
    if not matched:
        state = normalize_text(person.get("state_name")) or normalize_text(person.get("region"))
        name = _first_name(person.get("name", ""))
        raise RepresentativeSheetError(
            f"Não encontrei aba no formato {state}_{name}_01 para {person.get('name') or name}."
        )
    return matched


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
    }


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
                (person_id, tab_title, sheet_row, empresa, cnpj, telefone, uf, queue, registered, registration_row, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    worksheets = _matching_worksheets(person)
    incoming = []
    titles: list[str] = []
    for worksheet in worksheets:
        try:
            values = worksheet.get_all_values() or []
        except Exception as exc:
            raise RepresentativeSheetError(f"Não consegui ler a aba {worksheet.title}.") from exc
        titles.append(worksheet.title)
        for row in read_prospect_rows(values):
            incoming.append({**row, "tab_title": worksheet.title})
    merged = merge_prospect_rows(list_prospects(person_id), incoming)
    _replace_prospects(person_id, merged)
    return {"tabs": titles, "count": len(incoming)}


def move_prospect(person_id: str, prospect_key: str, queue_id: str) -> dict:
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
    return {
        "ok": True,
        "queue": queue,
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
