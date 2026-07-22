"""Cadastros locais pendentes de sincronização com a planilha Google."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime

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

PENDENTES_TAB = "CadastrosPendentes"


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


def _append_pending_to_sheet_tab(pending_id: int, empresa: str, payload: dict, headers: list[str], row_values: list[str]) -> None:
    """Backup durável na aba CadastrosPendentes (gravação, não leitura)."""
    try:
        from app.services.sheet_crm_storage import get_worksheet, ensure_crm_storage_tabs

        ensure_crm_storage_tabs()
        worksheet = get_worksheet(PENDENTES_TAB)
        if worksheet is None:
            return
        worksheet.append_row(
            [
                str(pending_id),
                empresa,
                datetime.now().isoformat(timespec="seconds"),
                "pending",
                json.dumps(payload, ensure_ascii=False, default=str),
                json.dumps(headers, ensure_ascii=False),
                json.dumps(row_values, ensure_ascii=False, default=str),
            ],
            value_input_option="USER_ENTERED",
            insert_data_option="INSERT_ROWS",
        )
    except Exception as error:
        log.warning("Backup CadastrosPendentes falhou: %s", error)


def queue_company_registration(
    *,
    payload: dict,
    headers: list[str],
    row_values: list[str],
    last_error: str = "",
) -> int:
    remember_sheet_headers(headers)
    empresa = normalize_text(payload.get("empresa"))
    pending_id = enqueue_pending_company(
        empresa=empresa,
        payload=payload,
        headers=headers,
        row_values=row_values,
        last_error=last_error,
    )
    _append_pending_to_sheet_tab(pending_id, empresa, payload, headers, row_values)
    return -int(pending_id)


def _pending_row_dict(item: dict) -> dict:
    payload = item.get("payload") or {}
    empresa = normalize_text(payload.get("empresa") or item.get("empresa"))
    local_row = int(item.get("local_sheet_row") or -item["id"])
    today = date.today()
    created = normalize_text(item.get("created_at")) or today.isoformat()
    try:
        created_date = datetime.fromisoformat(created.replace("Z", "")).date()
    except ValueError:
        created_date = today
    status = normalize_text(payload.get("status")) or "Novo Lead"
    return {
        "_sheet_row": local_row,
        "_empresa": empresa,
        "_vendedor": normalize_text(payload.get("vendedor")) or "Sem vendedor",
        "_status_whatsapp_original": status,
        "_status_ligacao_original": "",
        "_status_original": status,
        "_status_grupo": status,
        "_telefone": normalize_text(payload.get("telefone_b2b")),
        "_nicho": "Outros",
        "_estado": normalize_text(payload.get("uf")) or "—",
        "_capital_num": 0.0,
        "_valor_proposta_num": 0.0,
        "_pontuacao": 0,
        "_classificacao": "Baixo",
        "_data_chamado": pd.Timestamp(created_date),
        "_data_abertura": pd.Timestamp(created_date),
        "_ultima_atualizacao": pd.Timestamp(created_date),
        "Nome Empresas": empresa,
        "CNPJ": normalize_text(payload.get("cnpj")),
        "Vendedor": normalize_text(payload.get("vendedor")),
        "Status": status,
        "Status WhatsApp": status,
        "Celular WhatsApp": normalize_text(payload.get("telefone_b2b")),
        "Sócio 1": normalize_text(payload.get("socio_1")),
        "Observações": normalize_text(payload.get("observacoes")),
        "Data do chamado": created_date.strftime("%d/%m/%Y"),
        "_pending_local": True,
        "_cadastro_tipo": normalize_text(payload.get("cadastro_tipo")).lower() or "lead",
    }


def merge_pending_companies_into_df(df: pd.DataFrame) -> pd.DataFrame:
    """Inclui cadastros locais ainda não sincronizados nas listagens do site."""
    pending = list_pending_companies("pending")
    if not pending:
        return df

    rows = []
    for item in pending:
        empresa = normalize_text((item.get("payload") or {}).get("empresa") or item.get("empresa"))
        if not empresa:
            continue
        if df is not None and not df.empty and "_empresa" in df.columns:
            exists = any(
                normalize_text(value).lower() == empresa.lower()
                for value in df["_empresa"].tolist()
            )
            if exists:
                continue
        rows.append(_pending_row_dict(item))

    if not rows:
        return df

    pending_df = pd.DataFrame(rows)
    if df is None or df.empty:
        return pending_df.reset_index(drop=True)

    combined = pd.concat([df, pending_df], ignore_index=True, sort=False)
    return combined.reset_index(drop=True)


def recover_pending_from_sheet_tab() -> int:
    """Reimporta CadastrosPendentes → SQLite após rebuild (quando o disco local some)."""
    try:
        from app.services.sheet_crm_storage import get_worksheet, ensure_crm_storage_tabs

        ensure_crm_storage_tabs()
        worksheet = get_worksheet(PENDENTES_TAB)
        if worksheet is None:
            return 0
        values = worksheet.get_all_values()
    except Exception as error:
        log.warning("Recuperar CadastrosPendentes falhou: %s", error)
        return 0

    if not values or len(values) < 2:
        return 0

    existing = {
        normalize_text(item.get("empresa")).lower()
        for item in list_pending_companies("pending")
        if normalize_text(item.get("empresa"))
    }
    recovered = 0
    for row in values[1:]:
        if len(row) < 7:
            continue
        status = normalize_text(row[3]).lower()
        if status and status != "pending":
            continue
        empresa = normalize_text(row[1])
        if not empresa or empresa.lower() in existing:
            continue
        try:
            payload = json.loads(row[4] or "{}")
            headers = json.loads(row[5] or "[]")
            row_values = json.loads(row[6] or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or not isinstance(headers, list) or not isinstance(row_values, list):
            continue
        if not headers or not row_values:
            continue
        enqueue_pending_company(
            empresa=empresa,
            payload=payload,
            headers=headers,
            row_values=row_values,
            last_error="recuperado da aba CadastrosPendentes",
        )
        existing.add(empresa.lower())
        recovered += 1
    if recovered:
        log.info("Recuperados %s cadastros pendentes da planilha", recovered)
    return recovered


def sync_pending_companies_to_sheet(*, max_items: int = 20) -> dict:
    """Envia cadastros locais pendentes para a Folha1."""
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
        try:
            from app.services.legacy_core import load_sheet_data

            load_sheet_data()
        except Exception:
            pass

    remaining = len(list_pending_companies("pending"))
    return {"synced": synced, "failed": failed, "remaining": remaining}
