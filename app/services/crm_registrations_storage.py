"""Cadastros Empresas/Leads — Postgres SoT com espelho Folha1/LeadAcoes."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func

from database.connection import SessionLocal
from database.models import CrmRegistration

from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"
_TZ = ZoneInfo("America/Sao_Paulo")
_df_lock = threading.Lock()
_df_cache: pd.DataFrame | None = None
_df_columns: dict | None = None
_df_cached_at = 0.0
_DF_TTL_SEC = 20.0

REGISTRATION_FIELD_KEYS = (
    "empresa",
    "data_abertura",
    "capital",
    "cnpj",
    "endereco",
    "endereco_numero",
    "endereco_complemento",
    "cep",
    "bairro",
    "municipio",
    "uf",
    "email_empresa",
    "site",
    "telefone_b2b",
    "telefone_fixo",
    "telefone_alternativo",
    "socio_1",
    "cpf_socio_1",
    "email_socio_1",
    "telefone_socio_1",
    "socio_2",
    "telefone_socio_2",
    "cpf_socio_2",
    "socio_3",
    "telefone_socio_3",
    "cpf_socio_3",
    "instagram",
    "linkedin",
    "vendedor",
    "status",
    "data_chamado",
    "ultima_atualizacao",
    "observacoes",
    "servico",
    "valor_proposta",
    "colaboradores",
)

# Headers canônicos no DF sintético (identify_columns resolve esses nomes).
FIELD_TO_SHEET_HEADER = {
    "empresa": "Nome Empresas",
    "data_abertura": "Data de abertura",
    "capital": "Capital",
    "cnpj": "CNPJ",
    "endereco": "Endereço",
    "endereco_numero": "Número",
    "endereco_complemento": "Complemento",
    "cep": "CEP",
    "bairro": "Bairro",
    "municipio": "Município",
    "uf": "UF",
    "email_empresa": "Email",
    "site": "Site empresa",
    "telefone_b2b": "Celular WhatsApp",
    "telefone_fixo": "Telefone fixo",
    "telefone_alternativo": "Telefone lemitt",
    "socio_1": "Sócio 1",
    "cpf_socio_1": "CPF",
    "email_socio_1": "E-mail Sócio 1",
    "telefone_socio_1": "Telefone",
    "socio_2": "Sócio 2",
    "telefone_socio_2": "Telefone sócio 2",
    "cpf_socio_2": "CPF_2",
    "socio_3": "Sócio 3",
    "telefone_socio_3": "Telefone sócio 3",
    "cpf_socio_3": "CPF_3",
    "instagram": "Instagram",
    "linkedin": "Linkedin",
    "vendedor": "Vendedor",
    "status": "Status WhatsApp",
    "data_chamado": "Data do chamado",
    "ultima_atualizacao": "Última atualização",
    "observacoes": "Observações",
    "servico": "Serviços fechados",
    "valor_proposta": "Valor do serviço",
    "colaboradores": "Colaboradores",
}


def _now_iso() -> str:
    return datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def _now_sheet() -> str:
    return datetime.now(_TZ).strftime("%d/%m/%Y %H:%M")


def _json_loads(raw: str | None, default):
    try:
        data = json.loads(raw or "")
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def _json_dumps(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def invalidate_registrations_cache() -> None:
    global _df_cache, _df_columns, _df_cached_at
    with _df_lock:
        _df_cache = None
        _df_columns = None
        _df_cached_at = 0.0
    try:
        from app.dependencies import invalidate_merged_prepared_cache

        invalidate_merged_prepared_cache()
    except Exception:
        pass
    try:
        from app.services.legacy_core import invalidate_sheet_cache

        invalidate_sheet_cache()
    except Exception:
        pass


_CRM_PG_READY_CACHE: bool | None = None
_CRM_PG_READY_AT = 0.0
_CRM_PG_READY_TTL_SEC = 30.0


def invalidate_crm_postgres_ready_cache() -> None:
    global _CRM_PG_READY_CACHE, _CRM_PG_READY_AT
    _CRM_PG_READY_CACHE = None
    _CRM_PG_READY_AT = 0.0


def is_crm_postgres_ready() -> bool:
    """Flag de cutover CRM→Postgres (cache curto — evita SessionLocal em todo request)."""
    global _CRM_PG_READY_CACHE, _CRM_PG_READY_AT
    now = time.monotonic()
    if _CRM_PG_READY_CACHE is not None and (now - _CRM_PG_READY_AT) < _CRM_PG_READY_TTL_SEC:
        return bool(_CRM_PG_READY_CACHE)
    try:
        from database.models import AppMeta

        db = SessionLocal()
        try:
            meta = db.get(AppMeta, "crm_postgres_migrated")
            ready = bool(meta and (meta.value or "").strip() in {"1", "true", "yes"})
        finally:
            db.close()
    except Exception:
        ready = False
    _CRM_PG_READY_CACHE = ready
    _CRM_PG_READY_AT = now
    return ready


def count_registrations(tenant_id: str | None = None) -> int:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        return (
            db.query(CrmRegistration)
            .filter(CrmRegistration.tenant_id == tenant)
            .count()
        )
    finally:
        db.close()


def get_registration_by_sheet_row(
    sheet_row: int,
    *,
    tenant_id: str | None = None,
) -> CrmRegistration | None:
    if not sheet_row:
        return None
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        return (
            db.query(CrmRegistration)
            .filter(
                CrmRegistration.tenant_id == tenant,
                CrmRegistration.sheet_row == int(sheet_row),
            )
            .first()
        )
    finally:
        db.close()


def search_matriz_companies(
    query: str = "",
    *,
    exclude_sheet_row: int | None = None,
    limit: int = 20,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Empresas elegíveis como matriz: cadastro empresa ativo que não é filial."""
    db = SessionLocal()
    try:
        tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
        q = db.query(CrmRegistration).filter(
            CrmRegistration.tenant_id == tenant,
            CrmRegistration.cadastro_tipo == "empresa",
            CrmRegistration.cadastro_ativo.is_(True),
            CrmRegistration.is_filial.is_(False),
        )
        if exclude_sheet_row is not None:
            try:
                q = q.filter(CrmRegistration.sheet_row != int(exclude_sheet_row))
            except (TypeError, ValueError):
                pass
        term = normalize_text(query).lower()
        rows = q.order_by(CrmRegistration.updated_at.desc()).limit(200).all()
        items: list[dict[str, Any]] = []
        for row in rows:
            nome = normalize_text(row.empresa)
            if not nome:
                continue
            if term and term not in nome.lower() and term not in normalize_text(row.cnpj):
                continue
            items.append(
                {
                    "sheet_row": int(row.sheet_row),
                    "empresa": nome,
                    "cnpj": normalize_text(row.cnpj),
                }
            )
            if len(items) >= max(1, int(limit)):
                break
        return items
    finally:
        db.close()


def get_registration_by_id(registration_id: int) -> CrmRegistration | None:
    if not registration_id:
        return None
    db = SessionLocal()
    try:
        return db.get(CrmRegistration, int(registration_id))
    finally:
        db.close()


def next_sheet_row(tenant_id: str | None = None) -> int:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        current = (
            db.query(func.max(CrmRegistration.sheet_row))
            .filter(CrmRegistration.tenant_id == tenant)
            .scalar()
        )
        return max(2, int(current or 1) + 1)
    finally:
        db.close()


def _apply_payload(row: CrmRegistration, payload: dict[str, Any]) -> None:
    for key in REGISTRATION_FIELD_KEYS:
        if key in payload:
            setattr(row, key, normalize_text(payload.get(key)))
    if "nicho" in payload:
        row.nicho = normalize_text(payload.get("nicho"))
    if "is_filial" in payload:
        raw = payload.get("is_filial")
        if isinstance(raw, bool):
            row.is_filial = raw
        else:
            text = normalize_text(raw).lower()
            row.is_filial = text in {"1", "true", "sim", "yes", "on"}
        if not row.is_filial:
            row.empresa_matriz_sheet_row = None
    if "empresa_matriz_sheet_row" in payload and getattr(row, "is_filial", False):
        try:
            matriz = int(payload.get("empresa_matriz_sheet_row") or 0)
        except (TypeError, ValueError):
            matriz = 0
        row.empresa_matriz_sheet_row = matriz if matriz > 0 else None
    if "cadastro_tipo" in payload:
        tipo = normalize_text(payload.get("cadastro_tipo")).lower()
        row.cadastro_tipo = "empresa" if tipo == "empresa" else "lead"
    if "cadastro_ativo" in payload:
        raw = payload.get("cadastro_ativo")
        if isinstance(raw, bool):
            row.cadastro_ativo = raw
        else:
            text = normalize_text(raw).lower()
            row.cadastro_ativo = text not in {
                "0",
                "false",
                "nao",
                "não",
                "inativo",
                "desativado",
                "off",
                "no",
            }
    if "payment_history" in payload:
        items = payload.get("payment_history")
        row.payment_history_json = _json_dumps(items if isinstance(items, list) else [])
    if "closed_services" in payload:
        items = payload.get("closed_services")
        row.closed_services_json = _json_dumps(items if isinstance(items, list) else [])
    if "extras" in payload and isinstance(payload.get("extras"), dict):
        extras = _json_loads(row.extras_json, {})
        extras.update(payload["extras"])
        row.extras_json = _json_dumps(extras)


def registration_to_payload(row: CrmRegistration) -> dict[str, Any]:
    data = {key: normalize_text(getattr(row, key, "")) for key in REGISTRATION_FIELD_KEYS}
    data["nicho"] = normalize_text(row.nicho)
    data["cadastro_tipo"] = normalize_text(row.cadastro_tipo) or "lead"
    data["cadastro_ativo"] = bool(row.cadastro_ativo)
    data["is_filial"] = bool(getattr(row, "is_filial", False))
    matriz = getattr(row, "empresa_matriz_sheet_row", None)
    data["empresa_matriz_sheet_row"] = int(matriz) if matriz else None
    data["sheet_row"] = int(row.sheet_row) if row.sheet_row else None
    data["id"] = int(row.id)
    data["payment_history"] = _json_loads(row.payment_history_json, [])
    data["closed_services"] = _json_loads(row.closed_services_json, [])
    data["extras"] = _json_loads(row.extras_json, {})
    return data


def actions_from_registration(row: CrmRegistration) -> dict:
    actions = _json_loads(row.actions_json, {})
    actions.setdefault("cadastro_tipo", normalize_text(row.cadastro_tipo) or "lead")
    actions.setdefault("cadastro_ativo", bool(row.cadastro_ativo))
    if row.nicho:
        actions.setdefault("nicho", normalize_text(row.nicho))
    actions["payment_history"] = _json_loads(row.payment_history_json, [])
    actions["closed_services"] = _json_loads(row.closed_services_json, [])
    actions["updated_at"] = normalize_text(row.updated_at) or _now_iso()
    return actions


def upsert_registration_from_payload(
    payload: dict[str, Any],
    *,
    sheet_row: int | None = None,
    tenant_id: str | None = None,
    mirror_sheet: bool = True,
) -> int:
    """Cria/atualiza cadastro no Postgres. Retorna sheet_row."""
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        row = None
        target_row = int(sheet_row) if sheet_row else None
        if target_row:
            row = (
                db.query(CrmRegistration)
                .filter(
                    CrmRegistration.tenant_id == tenant,
                    CrmRegistration.sheet_row == target_row,
                )
                .first()
            )
        if row is None:
            if not target_row:
                target_row = next_sheet_row(tenant)
            row = CrmRegistration(
                tenant_id=tenant,
                sheet_row=target_row,
                created_at=_now_iso(),
            )
            db.add(row)
        _apply_payload(row, payload)
        if not normalize_text(row.ultima_atualizacao):
            row.ultima_atualizacao = _now_sheet()
        row.updated_at = _now_iso()
        # Sync typed financeiro/tipo from actions if present
        if "cadastro_tipo" in payload or "nicho" in payload or "cadastro_ativo" in payload:
            actions = _json_loads(row.actions_json, {})
            for key in ("cadastro_tipo", "nicho", "cadastro_ativo"):
                if key in payload:
                    actions[key] = payload[key]
            row.actions_json = _json_dumps(actions)
        db.commit()
        db.refresh(row)
        result_sheet_row = int(row.sheet_row or target_row)
        registration_id = int(row.id)
    finally:
        db.close()

    invalidate_registrations_cache()
    if mirror_sheet:
        _schedule_mirror_registration(result_sheet_row, tenant_id=tenant)
    return result_sheet_row if result_sheet_row else registration_id


def update_registration_actions(
    sheet_row: int,
    payload: dict,
    *,
    tenant_id: str | None = None,
    mirror_sheet: bool = True,
) -> dict:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        row = (
            db.query(CrmRegistration)
            .filter(
                CrmRegistration.tenant_id == tenant,
                CrmRegistration.sheet_row == int(sheet_row),
            )
            .first()
        )
        if row is None:
            row = CrmRegistration(
                tenant_id=tenant,
                sheet_row=int(sheet_row),
                empresa="",
                created_at=_now_iso(),
            )
            db.add(row)
        actions = _json_loads(row.actions_json, {})
        actions.update(payload or {})
        actions["updated_at"] = _now_iso()
        row.actions_json = _json_dumps(actions)
        if "cadastro_tipo" in payload:
            tipo = normalize_text(payload.get("cadastro_tipo")).lower()
            row.cadastro_tipo = "empresa" if tipo == "empresa" else "lead"
        if "cadastro_ativo" in payload:
            raw = payload.get("cadastro_ativo")
            row.cadastro_ativo = bool(raw) if isinstance(raw, bool) else normalize_text(raw).lower() not in {
                "0",
                "false",
                "nao",
                "não",
                "inativo",
                "off",
                "no",
            }
        if "nicho" in payload:
            row.nicho = normalize_text(payload.get("nicho"))
        if "payment_history" in payload:
            items = payload.get("payment_history")
            row.payment_history_json = _json_dumps(items if isinstance(items, list) else [])
        if "closed_services" in payload:
            items = payload.get("closed_services")
            row.closed_services_json = _json_dumps(items if isinstance(items, list) else [])
            if isinstance(items, list) and items:
                servicos = [
                    normalize_text(item.get("servico"))
                    for item in items
                    if isinstance(item, dict) and normalize_text(item.get("servico"))
                ]
                valores = [
                    normalize_text(item.get("valor"))
                    for item in items
                    if isinstance(item, dict) and normalize_text(item.get("valor"))
                ]
                if servicos:
                    row.servico = " | ".join(servicos)
                if valores:
                    row.valor_proposta = " | ".join(valores)
        if "stage_override" in payload or "opportunity_status" in payload:
            stage = normalize_text(
                payload.get("stage_override") or payload.get("opportunity_status") or ""
            )
            if stage:
                row.status = stage
        row.updated_at = _now_iso()
        db.commit()
        db.refresh(row)
        result = actions_from_registration(row)
    finally:
        db.close()

    invalidate_registrations_cache()
    if mirror_sheet:
        _schedule_mirror_registration(int(sheet_row), tenant_id=tenant)
        _schedule_mirror_lead_actions(tenant_id=tenant)
    return result


def delete_registration(
    sheet_row: int,
    *,
    tenant_id: str | None = None,
    mirror_sheet: bool = True,
) -> None:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        row = (
            db.query(CrmRegistration)
            .filter(
                CrmRegistration.tenant_id == tenant,
                CrmRegistration.sheet_row == int(sheet_row),
            )
            .first()
        )
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    invalidate_registrations_cache()
    if mirror_sheet:
        try:
            from app.services.legacy_core import delete_company_from_sheet

            delete_company_from_sheet(int(sheet_row))
        except Exception:
            logger.exception("Falha ao espelhar exclusão Folha1 row=%s", sheet_row)
        _schedule_mirror_lead_actions(tenant_id=tenant)


def get_all_actions(tenant_id: str | None = None) -> dict[str, dict]:
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    db = SessionLocal()
    try:
        rows = (
            db.query(CrmRegistration)
            .filter(CrmRegistration.tenant_id == tenant)
            .all()
        )
        result: dict[str, dict] = {}
        for row in rows:
            if not row.sheet_row:
                continue
            result[str(int(row.sheet_row))] = actions_from_registration(row)
        return result
    finally:
        db.close()


def find_sheet_row_by_phone(phone: str, *, tenant_id: str | None = None) -> int | None:
    hit = find_registration_by_phone(phone, tenant_id=tenant_id)
    if not hit:
        return None
    return int(hit["sheet_row"]) if hit.get("sheet_row") else None


def find_registration_by_phone(
    phone: str,
    *,
    ignore_sheet_row: int | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    from app.services.legacy_core import normalize_phone_for_duplicate, phones_match_for_duplicate

    target = normalize_phone_for_duplicate(phone)
    if not target:
        return None
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    ignore = int(ignore_sheet_row) if ignore_sheet_row else None
    db = SessionLocal()
    try:
        rows = (
            db.query(CrmRegistration)
            .filter(CrmRegistration.tenant_id == tenant)
            .all()
        )
        for row in rows:
            sheet_row = int(row.sheet_row) if row.sheet_row else 0
            if ignore and sheet_row == ignore:
                continue
            for candidate in (
                row.telefone_b2b,
                row.telefone_fixo,
                row.telefone_alternativo,
                row.telefone_socio_1,
                row.telefone_socio_2,
                row.telefone_socio_3,
            ):
                if phones_match_for_duplicate(candidate, target):
                    return {
                        "sheet_row": sheet_row or None,
                        "empresa": normalize_text(row.empresa),
                        "telefone": normalize_phone_for_duplicate(candidate) or target,
                    }
    finally:
        db.close()
    return None


def find_registration_by_cnpj(
    cnpj: str,
    *,
    ignore_sheet_row: int | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    from app.services.legacy_core import normalize_cnpj_for_duplicate

    target = normalize_cnpj_for_duplicate(cnpj)
    if not target:
        return None
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    ignore = int(ignore_sheet_row) if ignore_sheet_row else None
    db = SessionLocal()
    try:
        rows = (
            db.query(CrmRegistration)
            .filter(CrmRegistration.tenant_id == tenant)
            .all()
        )
        for row in rows:
            sheet_row = int(row.sheet_row) if row.sheet_row else 0
            if ignore and sheet_row == ignore:
                continue
            existing = normalize_cnpj_for_duplicate(row.cnpj)
            if existing and existing == target:
                return {
                    "sheet_row": sheet_row or None,
                    "empresa": normalize_text(row.empresa),
                    "cnpj": existing,
                }
    finally:
        db.close()
    return None


def build_prepared_dataframe() -> tuple[pd.DataFrame, dict]:
    """Monta DF no formato prepare_data a partir do Postgres."""
    import time

    global _df_cache, _df_columns, _df_cached_at
    now = time.monotonic()
    with _df_lock:
        if _df_cache is not None and (now - _df_cached_at) < _DF_TTL_SEC:
            return _df_cache.copy(), dict(_df_columns or {})

    from app.services.legacy_core import identify_columns, prepare_data

    db = SessionLocal()
    try:
        rows = db.query(CrmRegistration).order_by(CrmRegistration.sheet_row.asc()).all()
    finally:
        db.close()

    records = []
    for row in rows:
        if not row.sheet_row:
            continue
        if not normalize_text(row.empresa):
            continue
        record = {"_sheet_row": int(row.sheet_row), "_registration_id": int(row.id)}
        for key, header in FIELD_TO_SHEET_HEADER.items():
            record[header] = normalize_text(getattr(row, key, ""))
        records.append(record)

    if not records:
        empty = pd.DataFrame()
        with _df_lock:
            _df_cache = empty
            _df_columns = {}
            _df_cached_at = time.monotonic()
        return empty.copy(), {}

    df = pd.DataFrame(records)
    columns = identify_columns(df)
    prepared = prepare_data(df, columns)
    with _df_lock:
        _df_cache = prepared
        _df_columns = dict(columns or {})
        _df_cached_at = time.monotonic()
    return prepared.copy(), dict(columns or {})


def _schedule_mirror_registration(sheet_row: int, *, tenant_id: str | None = None) -> None:
    def _run() -> None:
        try:
            _mirror_registration_to_folha1(int(sheet_row), tenant_id=tenant_id)
        except Exception:
            logger.exception("Falha espelho Folha1 sheet_row=%s", sheet_row)

    threading.Thread(target=_run, daemon=True).start()


def _schedule_mirror_lead_actions(*, tenant_id: str | None = None) -> None:
    def _run() -> None:
        try:
            _mirror_all_lead_actions(tenant_id=tenant_id)
        except Exception:
            logger.exception("Falha espelho LeadAcoes")

    threading.Thread(target=_run, daemon=True).start()


def _mirror_registration_to_folha1(sheet_row: int, *, tenant_id: str | None = None) -> None:
    from app.config import settings
    from app.services.legacy_core import append_company_to_sheet, update_company_in_sheet

    if not settings.sheets_configured:
        return
    row = get_registration_by_sheet_row(sheet_row, tenant_id=tenant_id)
    if not row:
        return
    payload = registration_to_payload(row)
    # Prefer update; if Folha1 missing this row, append and remount sheet_row.
    try:
        update_company_in_sheet(int(sheet_row), payload)
    except Exception:
        try:
            new_row = append_company_to_sheet(payload)
            if int(new_row) != int(sheet_row):
                db = SessionLocal()
                try:
                    current = (
                        db.query(CrmRegistration)
                        .filter(CrmRegistration.id == row.id)
                        .first()
                    )
                    if current:
                        # Keep local sheet_row stable for URLs; append may create a new Folha1 line.
                        # Store actual Folha1 row in extras for reconciliation.
                        extras = _json_loads(current.extras_json, {})
                        extras["folha1_sheet_row"] = int(new_row)
                        current.extras_json = _json_dumps(extras)
                        db.commit()
                finally:
                    db.close()
        except Exception:
            logger.exception("Não foi possível espelhar cadastro sheet_row=%s", sheet_row)


def _mirror_all_lead_actions(*, tenant_id: str | None = None) -> None:
    from app.config import settings
    from app.services.sheet_crm_storage import CRM_STORAGE_TABS, get_worksheet
    from app.services.sheet_read_cache import invalidate_worksheet_cache

    if not settings.sheets_configured:
        return
    worksheet = get_worksheet("LeadAcoes")
    if worksheet is None:
        return
    tenant = normalize_text(tenant_id) or DEFAULT_TENANT_ID
    headers = CRM_STORAGE_TABS["LeadAcoes"]
    store = get_all_actions(tenant)
    rows = [headers]
    for key in sorted(store.keys(), key=lambda value: int(value) if str(value).isdigit() else 0):
        record = store[key]
        rows.append(
            [
                tenant,
                str(int(key)),
                normalize_text(record.get("updated_at")) or _now_iso(),
                json.dumps(record, ensure_ascii=False, default=str),
            ]
        )
    worksheet.clear()
    worksheet.update(rows, value_input_option="USER_ENTERED")
    invalidate_worksheet_cache("LeadAcoes")
