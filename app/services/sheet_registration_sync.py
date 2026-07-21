"""Sincroniza cadastros da planilha com colunas de endereço e serviços fechados."""
from __future__ import annotations

from app.services.closed_services import closed_services_sheet_values, load_closed_services
from app.services.lead_actions_storage import DEFAULT_TENANT_ID
from app.services.legacy_core import (
    ensure_registration_sheet_columns,
    identify_columns,
    invalidate_sheet_cache,
    load_sheet_data,
    prepare_data,
    resolve_address_form_values,
    row_field_value,
    update_company_registration_fields,
)


def _build_repair_payload(row, columns: dict, tenant_id: str | None, sheet_row: int) -> dict:
    payload: dict[str, str] = {}

    address = resolve_address_form_values(row, columns)
    if any(address.get(key) for key in address):
        payload.update(address)

    servico_sheet = row_field_value(row, columns, "servico")
    valor_sheet = row_field_value(row, columns, "valor_proposta")
    closed_items = load_closed_services(
        tenant_id,
        sheet_row,
        servico=servico_sheet,
        valor_proposta=valor_sheet,
    )
    mirror = closed_services_sheet_values(closed_items)
    if mirror["servico"] or mirror["valor_proposta"]:
        payload["servico"] = mirror["servico"]
        payload["valor_proposta"] = mirror["valor_proposta"]
    elif servico_sheet or valor_sheet:
        payload["servico"] = servico_sheet
        payload["valor_proposta"] = valor_sheet

    return payload


def sync_registration_rows(*, apply_changes: bool = False, limit: int | None = None) -> dict:
    from app.services.legacy_core import get_gsheet_client, settings, _open_worksheet

    client = get_gsheet_client()
    spreadsheet = client.open_by_key(settings.sheet_id)
    worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
    if apply_changes:
        ensure_registration_sheet_columns(worksheet)

    df = load_sheet_data(refresh=True)
    columns = identify_columns(df)
    prepared = prepare_data(df, columns)

    stats = {"rows_seen": 0, "rows_updated": 0}
    tenant_id = DEFAULT_TENANT_ID

    for _, row in prepared.iterrows():
        sheet_row = int(row.get("_sheet_row", 0) or 0)
        if sheet_row < 2:
            continue

        stats["rows_seen"] += 1
        if limit is not None and stats["rows_seen"] > limit:
            break

        payload = _build_repair_payload(row, columns, tenant_id, sheet_row)
        if not payload:
            continue

        stats["rows_updated"] += 1
        if apply_changes:
            update_company_registration_fields(sheet_row, payload)

    if apply_changes:
        invalidate_sheet_cache()

    return stats
