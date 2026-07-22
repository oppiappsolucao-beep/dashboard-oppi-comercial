"""Cadastros locais pendentes de sincronização com a planilha Google."""
from __future__ import annotations

import logging
import time

import pandas as pd

from app.services.crm_local_db import (
    enqueue_pending_company,
    list_pending_companies,
    load_sheet_headers,
    mark_pending_company_error,
    mark_pending_company_synced,
    save_sheet_headers,
)
from app.services.legacy_core import normalize_text

log = logging.getLogger(__name__)


def remember_sheet_headers(headers: list[str]) -> None:
    if headers:
        try:
            save_sheet_headers([str(h) for h in headers])
        except Exception:
            pass


def resolve_registration_headers(cached_values: list[list[str]] | None) -> list[str]:
    if cached_values and cached_values[0]:
        headers = [str(h) for h in cached_values[0]]
        remember_sheet_headers(headers)
        return headers
    stored = load_sheet_headers()
    if stored:
        return [str(h) for h in stored]
    return []


def queue_company_registration(
    *,
    payload: dict,
    headers: list[str],
    row_values: list[str],
    last_error: str = "",
) -> int:
    remember_sheet_headers(headers)
    pending_id = enqueue_pending_company(
        empresa=normalize_text(payload.get("empresa")),
        payload=payload,
        headers=headers,
        row_values=row_values,
        last_error=last_error,
    )
    return -int(pending_id)


def merge_pending_companies_into_df(df: pd.DataFrame) -> pd.DataFrame:
    """Inclui cadastros locais ainda não sincronizados nas listagens do site."""
    pending = list_pending_companies("pending")
    if not pending:
        return df

    rows = []
    for item in pending:
        payload = item.get("payload") or {}
        empresa = normalize_text(payload.get("empresa") or item.get("empresa"))
        if not empresa:
            continue
        local_row = int(item.get("local_sheet_row") or -item["id"])
        if df is not None and not df.empty and "_empresa" in df.columns:
            exists = any(
                normalize_text(value).lower() == empresa.lower()
                for value in df["_empresa"].tolist()
            )
            if exists:
                continue
        rows.append({
            "_sheet_row": local_row,
            "_empresa": empresa,
            "_vendedor": normalize_text(payload.get("vendedor")) or "Sem vendedor",
            "_status_whatsapp_original": normalize_text(payload.get("status")) or "Novo Lead",
            "_status_ligacao_original": "",
            "_status_original": normalize_text(payload.get("status")) or "Novo Lead",
            "_status_grupo": normalize_text(payload.get("status")) or "Novo Lead",
            "_telefone": normalize_text(payload.get("telefone_b2b")),
            "_nicho": "Outros",
            "_estado": normalize_text(payload.get("uf")) or "—",
            "_capital_num": 0.0,
            "_valor_proposta_num": 0.0,
            "_pontuacao": 0,
            "_classificacao": "Baixo",
            "Nome Empresas": empresa,
            "CNPJ": normalize_text(payload.get("cnpj")),
            "Vendedor": normalize_text(payload.get("vendedor")),
            "Status": normalize_text(payload.get("status")) or "Novo Lead",
            "Status WhatsApp": normalize_text(payload.get("status")) or "Novo Lead",
            "Celular WhatsApp": normalize_text(payload.get("telefone_b2b")),
            "Sócio 1": normalize_text(payload.get("socio_1")),
            "Observações": normalize_text(payload.get("observacoes")),
            "_pending_local": True,
        })

    if not rows:
        return df

    pending_df = pd.DataFrame(rows)
    if df is None or df.empty:
        return pending_df.reset_index(drop=True)

    combined = pd.concat([df, pending_df], ignore_index=True, sort=False)
    return combined.reset_index(drop=True)


def sync_pending_companies_to_sheet(*, max_items: int = 20) -> dict:
    """Envia cadastros locais pendentes para a planilha."""
    from app.services.legacy_core import (
        get_gsheet_client,
        invalidate_sheet_cache,
        _open_worksheet,
    )
    from app.config import settings

    pending = list_pending_companies("pending")[:max_items]
    if not pending:
        return {"synced": 0, "failed": 0, "remaining": 0}

    synced = 0
    failed = 0
    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
    except Exception as error:
        log.warning("Sync pendentes: sem acesso à planilha (%s)", error)
        return {"synced": 0, "failed": len(pending), "remaining": len(pending)}

    for item in pending:
        try:
            worksheet.append_row(
                item["row_values"],
                value_input_option="USER_ENTERED",
                insert_data_option="INSERT_ROWS",
            )
            sheet_row = int(worksheet.row_count or 0)
            mark_pending_company_synced(item["id"], sheet_row or item["id"])
            synced += 1
            time.sleep(1.2)
        except Exception as error:
            mark_pending_company_error(item["id"], str(error))
            failed += 1
            message = str(error)
            if "429" in message or "Quota exceeded" in message:
                break
            time.sleep(1.5)

    if synced:
        invalidate_sheet_cache()

    remaining = len(list_pending_companies("pending"))
    return {"synced": synced, "failed": failed, "remaining": remaining}
