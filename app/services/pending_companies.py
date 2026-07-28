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
    backup_to_sheet: bool = True,
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
    if backup_to_sheet:
        _append_pending_to_sheet_tab(pending_id, empresa, payload, headers, row_values)
    return -int(pending_id)


def enqueue_payload_locally(payload: dict, *, last_error: str = "Migração local (quota Sheets)") -> int:
    """Enfileira cadastro só no SQLite/local — sem ler/gravar Google Sheets."""
    from app.services.legacy_core import (
        get_last_good_sheet_values,
        hydrate_sheet_cache_from_disk,
        _set_sheet_value_by_header,
        _apply_address_fields,
        _apply_commercial_fields,
    )

    hydrate_sheet_cache_from_disk()
    headers = resolve_registration_headers(get_last_good_sheet_values())
    if not headers:
        headers = [
            "Nome Empresas", "CNPJ", "Data de abertura", "Capital",
            "Endereço", "Número", "Complemento", "CEP", "Bairro", "Município", "UF",
            "Email", "Site empresa",
            "Celular WhatsApp", "Telefone fixo", "Telefone lemitt",
            "Sócio 1", "CPF", "E-mail Sócio 1", "Telefone",
            "Sócio 2", "Telefone sócio 2", "CPF_2",
            "Sócio 3", "Telefone sócio 3", "CPF_3",
            "Instagram", "Linkedin", "Vendedor",
            "Status WhatsApp", "Data do chamado", "Última atualização", "Observações",
            "Serviços fechados", "Valor do serviço", "Colaboradores",
        ]
        remember_sheet_headers(headers)

    row_values = [""] * len(headers)
    _set_sheet_value_by_header(row_values, headers, ["Nome Empresas", "Nome da empresa", "Empresa", "Nome Empresa", "Nome empresas", "Nome Empresa(s)"], payload.get("empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Data de abertura", "Data abertura"], payload.get("data_abertura"))
    _set_sheet_value_by_header(row_values, headers, ["Capital", "Capital social"], payload.get("capital"))
    _set_sheet_value_by_header(row_values, headers, ["CNPJ"], payload.get("cnpj"))
    _apply_address_fields(row_values, headers, payload)
    _set_sheet_value_by_header(row_values, headers, ["Email", "E-mail"], payload.get("email_empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Site empresa", "Site", "Website"], payload.get("site"))
    _set_sheet_value_by_header(row_values, headers, ["Celular WhatsApp", "Telefone (b2b)", "Telefone b2b"], payload.get("telefone_b2b"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone fixo", "Fixo"], payload.get("telefone_fixo"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone lemitt", "Telefone alternativo", "Outro telefone"], payload.get("telefone_alternativo"))
    _set_sheet_value_by_header(row_values, headers, ["Sócio 1", "Socio 1", "Sócio1", "Socio1"], payload.get("socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_1"), occurrence=1)
    _set_sheet_value_by_header(row_values, headers, ["E-mail Sócio 1", "Email Sócio 1", "E-mail Socio 1", "Email Socio 1"], payload.get("email_socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone"], payload.get("telefone_socio_1"), occurrence=1)
    _set_sheet_value_by_header(row_values, headers, ["Vendedor", "Responsável", "Responsavel"], payload.get("vendedor"))
    _set_sheet_value_by_header(row_values, headers, ["Status WhatsApp", "Status", "Etapa"], payload.get("status"))
    _set_sheet_value_by_header(row_values, headers, ["Data do chamado", "Data chamado"], payload.get("data_chamado"))
    _set_sheet_value_by_header(row_values, headers, ["Última atualização", "Ultima atualização", "Ultima atualizacao"], payload.get("ultima_atualizacao"))
    _set_sheet_value_by_header(row_values, headers, ["Observações", "Observacoes", "Observação", "Observacao"], payload.get("observacoes"))
    _apply_commercial_fields(row_values, headers, payload)
    _set_sheet_value_by_header(row_values, headers, ["Colaboradores", "Qtd colaboradores"], payload.get("colaboradores"))

    return queue_company_registration(
        payload=payload,
        headers=headers,
        row_values=row_values,
        last_error=last_error,
        backup_to_sheet=False,
    )


def _is_migration_empresa_payload(payload: dict) -> bool:
    tipo = normalize_text(payload.get("cadastro_tipo")).lower() or "lead"
    observacoes = normalize_text(payload.get("observacoes"))
    servico = normalize_text(payload.get("servico"))
    vendedor = normalize_text(payload.get("vendedor"))
    return (
        tipo == "empresa"
        or "Migrado do Oppi Ponto" in observacoes
        or "oppi_ponto_company_id" in observacoes
        or "company_id=" in observacoes
        or "Ponto Eletrônico" in servico
        or vendedor.lower() == "oppi"
    )


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
    # Migração Oppi: usa hoje para não sumir do filtro de período.
    if _is_migration_empresa_payload(payload):
        created_date = today
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
        "Serviços fechados": normalize_text(payload.get("servico")),
        "Valor do serviço": normalize_text(payload.get("valor_proposta")),
        "Colaboradores": normalize_text(payload.get("colaboradores")),
        "_pending_local": True,
        "_cadastro_tipo": normalize_text(payload.get("cadastro_tipo")).lower() or "lead",
    }


def reopen_synced_migration_pendings() -> int:
    """Reabre pendentes de migração marcados como synced (eles sumiam da lista Empresas)."""
    from app.services.crm_local_db import mark_pending_company_pending

    reopened = 0
    for item in list_pending_companies(None) or []:
        status = normalize_text(item.get("status")).lower()
        if status == "pending":
            continue
        payload = item.get("payload") or {}
        if not _is_migration_empresa_payload(payload):
            continue
        try:
            mark_pending_company_pending(
                int(item["id"]),
                last_error="Reaberto: migração Oppi Ponto fica local até sync manual",
            )
            reopened += 1
        except Exception:
            continue
    return reopened


def merge_pending_companies_into_df(df: pd.DataFrame) -> pd.DataFrame:
    """Inclui cadastros locais pendentes nas listagens.

    Regra: migração Oppi Ponto (pending ou synced) SEMPRE aparece por CNPJ.
    """
    from app.services.legacy_core import normalize_cnpj_for_duplicate

    try:
        reopen_synced_migration_pendings()
    except Exception:
        pass

    all_items = list_pending_companies(None) or []
    pending = []
    for item in all_items:
        status = normalize_text(item.get("status")).lower() or "pending"
        payload = item.get("payload") or {}
        if status == "pending" or _is_migration_empresa_payload(payload):
            pending.append(item)

    if not pending:
        return df if df is not None else pd.DataFrame()

    def _priority(item: dict) -> tuple[int, int]:
        payload = item.get("payload") or {}
        return (1 if _is_migration_empresa_payload(payload) else 0, int(item.get("id") or 0))

    by_cnpj: dict[str, dict] = {}
    by_name_only: list[dict] = []
    for item in pending:
        payload = item.get("payload") or {}
        empresa = normalize_text(payload.get("empresa") or item.get("empresa"))
        if not empresa:
            continue
        cnpj = normalize_cnpj_for_duplicate(payload.get("cnpj"))
        if cnpj:
            prev = by_cnpj.get(cnpj)
            if prev is None or _priority(item) >= _priority(prev):
                by_cnpj[cnpj] = item
        else:
            by_name_only.append(item)

    rows: list[dict] = []
    seen_names: set[str] = set()
    for _cnpj, item in by_cnpj.items():
        payload = item.get("payload") or {}
        if not _is_migration_empresa_payload(payload):
            continue
        row = _pending_row_dict(item)
        row["_cadastro_tipo"] = "empresa"
        rows.append(row)
        seen_names.add(normalize_text(row.get("_empresa")).lower())

    for item in by_name_only:
        payload = item.get("payload") or {}
        if normalize_text(payload.get("cadastro_tipo")).lower() == "empresa":
            row = _pending_row_dict(item)
            row["_cadastro_tipo"] = "empresa"
            name = normalize_text(row.get("_empresa")).lower()
            if name and name not in seen_names:
                rows.append(row)
                seen_names.add(name)

    if not rows:
        return df if df is not None else pd.DataFrame()

    pending_df = pd.DataFrame(rows)
    if df is None or df.empty:
        return pending_df.reset_index(drop=True)

    pending_cnpjs = {
        normalize_cnpj_for_duplicate(value)
        for value in pending_df.get("CNPJ", []).tolist()
        if normalize_cnpj_for_duplicate(value)
    }

    def _row_cnpj(row) -> str:
        cnpj = normalize_cnpj_for_duplicate(row.get("CNPJ") or row.get("cnpj") or "")
        if cnpj:
            return cnpj
        for key in row.index:
            if normalize_text(key).lower() == "cnpj":
                return normalize_cnpj_for_duplicate(row.get(key))
        return ""

    if pending_cnpjs:
        df = df[~df.apply(lambda row: _row_cnpj(row) in pending_cnpjs, axis=1)].copy()

    combined = pd.concat([pending_df, df], ignore_index=True, sort=False)
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
    """Envia cadastros locais pendentes para a Folha1.

    Empresas migradas do Oppi Ponto NÃO entram no sync automático —
    o sync marcava como synced e elas sumiam da lista Empresas.
    """
    from app.services.legacy_core import (
        get_gsheet_client,
        invalidate_sheet_cache,
        _open_worksheet,
    )
    from app.config import settings

    raw_pending = list_pending_companies("pending") or []
    pending = [
        item for item in raw_pending
        if not _is_migration_empresa_payload(item.get("payload") or {})
    ][:max_items]
    skipped_migration = len(raw_pending) - len([
        item for item in raw_pending
        if not _is_migration_empresa_payload(item.get("payload") or {})
    ])
    if not pending:
        return {
            "synced": 0,
            "failed": 0,
            "remaining": len(raw_pending),
            "skipped_migration": skipped_migration,
        }

    synced = 0
    failed = 0
    try:
        client = get_gsheet_client()
        spreadsheet = client.open_by_key(settings.sheet_id)
        worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
    except Exception as error:
        log.warning("Sync pendentes: sem acesso à planilha (%s)", error)
        return {
            "synced": 0,
            "failed": len(pending),
            "remaining": len(raw_pending),
            "skipped_migration": skipped_migration,
        }

    for item in pending:
        try:
            from app.services.legacy_core import _folha1_next_row, _write_folha1_row
            from app.services.lead_actions_storage import DEFAULT_TENANT_ID, get_lead_action, save_lead_action

            next_row = _folha1_next_row(worksheet, None)
            sheet_row = _write_folha1_row(worksheet, next_row, item["row_values"])
            local_row = int(item.get("local_sheet_row") or -item["id"])
            # Copia lead_actions do id local negativo para a linha real da planilha.
            try:
                local_action = get_lead_action(DEFAULT_TENANT_ID, local_row) or {}
                if local_action and sheet_row:
                    save_lead_action(DEFAULT_TENANT_ID, int(sheet_row), dict(local_action))
            except Exception:
                pass
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
    return {
        "synced": synced,
        "failed": failed,
        "remaining": remaining,
        "skipped_migration": skipped_migration,
    }
