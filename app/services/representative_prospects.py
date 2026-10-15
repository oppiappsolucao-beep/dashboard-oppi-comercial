"""Aba semanal do representante (planilha nova) e o envio do cadastro salvo para a Raissa.

A planilha nova guarda só empresa, CNPJ, telefone e UF. Boleto, senha, login e status
nascem no cadastro e vão para a aba Raissa no salvar.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
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


def link_representative_sheet(person: dict) -> dict:
    """Copia a aba do representante para o banco local. Não grava na Raissa."""
    if normalize_text(person.get("kind")) != "representante":
        raise RepresentativeSheetError("Só representante tem aba de prospecção.")
    person_id = normalize_text(person.get("id"))
    if not person_id:
        raise RepresentativeSheetError("Representante sem identificador.")
    worksheets = _matching_worksheets(person)
    stamp = _now()
    stored: list[tuple] = []
    titles: list[str] = []
    for worksheet in worksheets:
        try:
            values = worksheet.get_all_values() or []
        except Exception as exc:
            raise RepresentativeSheetError(f"Não consegui ler a aba {worksheet.title}.") from exc
        titles.append(worksheet.title)
        for row in read_prospect_rows(values):
            stored.append(
                (
                    person_id,
                    worksheet.title,
                    int(row["sheet_row"]),
                    row["empresa"],
                    row["cnpj"],
                    row["telefone"],
                    row["uf"],
                    stamp,
                )
            )
    init_crm_local_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM representative_prospects WHERE person_id = ?", (person_id,))
        conn.executemany(
            """
            INSERT INTO representative_prospects
                (person_id, tab_title, sheet_row, empresa, cnpj, telefone, uf, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            stored,
        )
        conn.commit()
    return {"tabs": titles, "count": len(stored)}
