#!/usr/bin/env python3
"""Sincroniza cadastros existentes com as colunas corretas da planilha."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    headers = ensure_registration_sheet_columns(worksheet) if apply_changes else worksheet.row_values(1)

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Corrige endereços e serviços na planilha comercial.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Grava as correções na planilha. Sem essa flag, apenas simula.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita a quantidade de linhas processadas (útil para teste).",
    )
    args = parser.parse_args()

    stats = sync_registration_rows(apply_changes=args.apply, limit=args.limit)
    mode = "APLICADO" if args.apply else "SIMULAÇÃO"
    print(f"[{mode}] Linhas analisadas: {stats['rows_seen']}")
    print(f"[{mode}] Linhas com correção: {stats['rows_updated']}")
    if not args.apply:
        print("Execute novamente com --apply para gravar na planilha.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
