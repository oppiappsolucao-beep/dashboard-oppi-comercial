"""Vínculo Atendimentos ↔ CRM (Leads/Empresas)."""
from __future__ import annotations

from datetime import date

from app.dependencies import get_prepared_data
from app.services.legacy_core import (
    normalize_phone_for_duplicate,
    normalize_text,
    invalidate_sheet_cache,
)
from app.services.lead_actions_storage import DEFAULT_TENANT_ID
from app.services.registration import (
    save_cadastro_tipo,
    save_new_company,
)


def phones_strongly_match(left, right) -> bool:
    """Match forte de WhatsApp: igual ou mesmos 10/11 dígitos finais (com DDD).

    Nunca usa só os 8 últimos — isso misturava cadastros diferentes no CRM.
    """
    a = normalize_phone_for_duplicate(left)
    b = normalize_phone_for_duplicate(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 11 and len(b) >= 11 and a[-11:] == b[-11:]:
        return True
    if len(a) >= 10 and len(b) >= 10 and a[-10:] == b[-10:]:
        return True
    return False


def _phones_from_row(row, columns: dict) -> set[str]:
    phones: set[str] = set()
    for key in (
        "telefone_b2b",
        "telefone_fixo",
        "telefone_alternativo",
        "telefone_socio_1",
        "telefone_socio_2",
        "telefone_socio_3",
    ):
        column = columns.get(key)
        if column and column in row.index:
            normalized = normalize_phone_for_duplicate(row.get(column, ""))
            if normalized:
                phones.add(normalized)
    prepared = normalize_phone_for_duplicate(row.get("_telefone", ""))
    if prepared:
        phones.add(prepared)
    return phones


def find_sheet_row_by_phone(phone: str) -> int | None:
    target = normalize_phone_for_duplicate(phone)
    if not target:
        return None
    try:
        df, columns = get_prepared_data()
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None

    exact_hit: int | None = None
    strong_hit: int | None = None

    for _, row in df.iterrows():
        sheet_row = int(row.get("_sheet_row", 0) or 0)
        if not sheet_row:
            continue
        row_phones = _phones_from_row(row, columns)
        if not row_phones:
            continue
        if target in row_phones:
            primary = normalize_phone_for_duplicate(row.get("_telefone", ""))
            if primary == target:
                return sheet_row
            exact_hit = exact_hit or sheet_row
            continue
        if any(phones_strongly_match(target, existing) for existing in row_phones):
            strong_hit = strong_hit or sheet_row

    return exact_hit or strong_hit


def sheet_row_matches_phone(sheet_row: int | None, phone: str) -> bool:
    """Confere se o cadastro vinculado realmente tem o WhatsApp da conversa."""
    if not sheet_row:
        return False
    target = normalize_phone_for_duplicate(phone)
    if not target:
        return False
    try:
        df, columns = get_prepared_data()
    except Exception:
        return False
    if df is None or getattr(df, "empty", True):
        return False
    matches = df[df["_sheet_row"] == int(sheet_row)]
    if matches.empty:
        return False
    row_phones = _phones_from_row(matches.iloc[0], columns)
    return any(phones_strongly_match(target, existing) for existing in row_phones)


def create_lead_from_whatsapp(
    *,
    phone: str,
    contact_name: str = "",
    vendedor: str = "",
) -> int:
    digits = normalize_phone_for_duplicate(phone)
    display_phone = phone
    if digits:
        if len(digits) == 11:
            display_phone = f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
        elif len(digits) == 10:
            display_phone = f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    name = normalize_text(contact_name) or f"Lead WhatsApp {display_phone}"
    seller = normalize_text(vendedor) or "Sem vendedor"
    form = {
        "empresa": name,
        "telefone_b2b": display_phone,
        "status": "Novo Lead",
        "data_chamado": date.today().strftime("%d/%m/%Y"),
        "cadastro_tipo": "lead",
        "vendedor": seller,
        "observacoes": "Lead criado automaticamente pelo Atendimento WhatsApp.",
    }
    sheet_row = save_new_company(form)
    if sheet_row:
        save_cadastro_tipo(DEFAULT_TENANT_ID, int(sheet_row), "lead")
        try:
            invalidate_sheet_cache()
        except Exception:
            pass
    return int(sheet_row or 0)


def resolve_or_create_lead(
    *,
    phone: str,
    contact_name: str = "",
    vendedor: str = "",
) -> int | None:
    existing = find_sheet_row_by_phone(phone)
    if existing:
        return existing
    try:
        return create_lead_from_whatsapp(
            phone=phone,
            contact_name=contact_name,
            vendedor=vendedor,
        )
    except Exception:
        return None


def build_crm_panel(
    sheet_row: int | None,
    *,
    fallback_name: str = "",
    fallback_phone: str = "",
) -> dict:
    empty = {
        "sheet_row": None,
        "empresa": normalize_text(fallback_name) or "—",
        "contato": normalize_text(fallback_name) or "—",
        "telefone": normalize_text(fallback_phone) or "—",
        "vendedor": "—",
        "etapa": "—",
        "edit_href": "",
    }
    if not sheet_row:
        return empty
    try:
        df, columns = get_prepared_data()
    except Exception:
        return empty
    matches = df[df["_sheet_row"] == int(sheet_row)] if not df.empty else df
    if matches.empty:
        return empty
    row = matches.iloc[0]
    socio_col = columns.get("socio_1")
    socio = normalize_text(row.get(socio_col, "")) if socio_col else ""
    empresa = normalize_text(row.get("_empresa", "")) or "—"
    return {
        "sheet_row": int(sheet_row),
        "empresa": empresa,
        "contato": socio or empresa,
        "telefone": normalize_text(row.get("_telefone", "")) or normalize_text(fallback_phone) or "—",
        "vendedor": normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
        "etapa": normalize_text(row.get("_status_grupo") or row.get("_status_original")) or "Novo Lead",
        "edit_href": f"/cadastro/todos/{int(sheet_row)}/editar?from=attendances",
    }
