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
    is_cadastro_ativo,
    save_cadastro_tipo,
    save_new_company,
)


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

    for _, row in df.iterrows():
        sheet_row = int(row.get("_sheet_row", 0) or 0)
        if not sheet_row:
            continue
        if not is_cadastro_ativo(DEFAULT_TENANT_ID, sheet_row):
            # ainda vincula se o número bater (não recria lead)
            pass
        row_phones = _phones_from_row(row, columns)
        if target in row_phones:
            return sheet_row
        # também compara com DDI 55
        if f"55{target}"[-11:] in {p[-11:] for p in row_phones if len(p) >= 10}:
            return sheet_row
        for existing in row_phones:
            if existing[-8:] == target[-8:] and len(target) >= 8 and len(existing) >= 8:
                return sheet_row
    return None


def create_lead_from_whatsapp(*, phone: str, contact_name: str = "") -> int:
    digits = normalize_phone_for_duplicate(phone)
    display_phone = phone
    if digits:
        if len(digits) == 11:
            display_phone = f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
        elif len(digits) == 10:
            display_phone = f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    name = normalize_text(contact_name) or f"Lead WhatsApp {display_phone}"
    form = {
        "empresa": name,
        "telefone_b2b": display_phone,
        "status": "Novo Lead",
        "data_chamado": date.today().strftime("%d/%m/%Y"),
        "cadastro_tipo": "lead",
        "vendedor": "Sem vendedor",
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


def resolve_or_create_lead(*, phone: str, contact_name: str = "") -> int | None:
    existing = find_sheet_row_by_phone(phone)
    if existing:
        return existing
    try:
        return create_lead_from_whatsapp(phone=phone, contact_name=contact_name)
    except Exception:
        return None


def build_crm_panel(sheet_row: int | None) -> dict:
    empty = {
        "sheet_row": None,
        "empresa": "—",
        "contato": "—",
        "telefone": "—",
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
        "telefone": normalize_text(row.get("_telefone", "")) or "—",
        "vendedor": normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
        "etapa": normalize_text(row.get("_status_grupo") or row.get("_status_original")) or "Novo Lead",
        "edit_href": f"/cadastro/todos/{int(sheet_row)}/editar?from=attendances",
    }
