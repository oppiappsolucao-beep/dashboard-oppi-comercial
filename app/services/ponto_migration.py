"""Migração Oppi Ponto → CRM: clientes entram como empresa (não lead).

Fluxo: empresa → fechamento → financeiro → suporte.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from app.services.closed_services import save_closed_services
from app.services.legacy_core import (
    DuplicateRegistrationError,
    normalize_cnpj_for_duplicate,
    normalize_text,
)
from app.services.payment_history import save_payment_history
from app.services.registration import (
    save_access_fields,
    save_cadastro_ativo,
    save_cadastro_tipo,
    save_company_edit,
    save_new_company,
)
from app.services.lead_actions_storage import DEFAULT_TENANT_ID, save_lead_action

MIGRATED_STATUS = "Fechado"
MIGRATED_SERVICE_NAME = "Ponto Eletrônico Oppi"
MIGRATION_INDEX_FILE = "ponto_migration_index.json"


def _migration_index_path():
    from app.services.storage_paths import get_storage_dir

    return get_storage_dir() / MIGRATION_INDEX_FILE


def load_migration_index() -> dict:
    import json

    path = _migration_index_path()
    if not path.exists():
        return {"by_cnpj": {}, "by_ponto_id": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"by_cnpj": {}, "by_ponto_id": {}}
    if not isinstance(data, dict):
        return {"by_cnpj": {}, "by_ponto_id": {}}
    data.setdefault("by_cnpj", {})
    data.setdefault("by_ponto_id", {})
    return data


def remember_migrated_company(company: dict, *, sheet_row: int | None = None) -> None:
    """Índice local: CNPJ migrado do Ponto = sempre Empresa na listagem."""
    import json

    cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)
    if not cnpj and not ponto_id:
        return
    data = load_migration_index()
    entry = {
        "oppi_ponto_company_id": ponto_id,
        "empresa": normalize_text(company.get("razao_social")) or normalize_text(company.get("nome")),
        "cnpj": cnpj,
        "sheet_row": sheet_row,
        "cadastro_tipo": "empresa",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if cnpj:
        data["by_cnpj"][cnpj] = entry
    if ponto_id:
        data["by_ponto_id"][str(ponto_id)] = entry
    path = _migration_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_cnpj_migrated_empresa(cnpj: str) -> bool:
    digits = normalize_cnpj_for_duplicate(cnpj)
    if not digits:
        return False
    entry = (load_migration_index().get("by_cnpj") or {}).get(digits)
    return bool(entry and entry.get("cadastro_tipo") == "empresa")


def _format_money(value: str) -> str:
    raw = normalize_text(value)
    if not raw:
        return ""
    if raw.upper().startswith("R$"):
        return raw
    candidate = raw.replace("R$", "").strip()
    if "," in candidate and "." in candidate:
        candidate = candidate.replace(".", "").replace(",", ".")
    elif "," in candidate:
        candidate = candidate.replace(",", ".")
    try:
        amount = float(candidate)
        formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {formatted}"
    except ValueError:
        return f"R$ {raw}"


def _map_forma_pagamento(modalidade: str) -> str:
    key = normalize_text(modalidade).lower()
    if key in {"avista", "à vista", "a vista", "avulsa"}:
        return "À vista"
    if key in {"anual", "ano"}:
        return "Anual"
    return "Mensal"


def _map_payment_status(status: str) -> str:
    key = normalize_text(status).lower()
    if key in {"pago", "confirmado", "paid", "received"}:
        return "Pago"
    if key in {"vencido", "overdue", "atrasado"}:
        return "Atrasado"
    if key in {"cancelado", "cancelled", "canceled"}:
        return "Cancelado"
    return "Pendente"


def _format_cnpj_display(digits: str) -> str:
    d = normalize_cnpj_for_duplicate(digits)
    if len(d) != 14:
        return normalize_text(digits)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _iso_or_empty(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if type(value).__name__ in {"NaTType", "NaT"}:
        return ""
    if isinstance(value, datetime):
        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = normalize_text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def map_ponto_company_to_form(company: dict) -> dict:
    """Monta formulário de cadastro CRM (tipo empresa + status Fechado)."""
    cnpj_digits = normalize_cnpj_for_duplicate(company.get("cnpj"))
    empresa = normalize_text(company.get("razao_social")) or normalize_text(company.get("nome"))
    valor = _format_money(company.get("plano_valor", ""))
    whatsapp = normalize_text(company.get("whatsapp")) or normalize_text(company.get("telefone"))
    telefone = normalize_text(company.get("telefone"))
    created = company.get("created_at")
    # Lista de Empresas não filtra por 7 dias, mas mantemos data de migração = hoje
    # para aparecer em visões com período curto.
    data_chamado = date.today().strftime("%d/%m/%Y")
    _ = created

    colaboradores = company.get("funcionarios")
    colaboradores_txt = ""
    if colaboradores not in (None, ""):
        colaboradores_txt = f"{colaboradores} colaboradores"

    return {
        "cadastro_tipo": "empresa",
        "empresa": empresa,
        "cnpj": _format_cnpj_display(cnpj_digits) if cnpj_digits else normalize_text(company.get("cnpj")),
        "endereco": normalize_text(company.get("endereco")),
        "endereco_numero": normalize_text(company.get("numero")),
        "endereco_complemento": normalize_text(company.get("complemento")),
        "bairro": normalize_text(company.get("bairro")),
        "municipio": normalize_text(company.get("cidade")),
        "uf": normalize_text(company.get("estado")).upper()[:2],
        "cep": normalize_text(company.get("cep")),
        "telefone_b2b": whatsapp,
        "telefone_fixo": telefone if telefone != whatsapp else "",
        "email_empresa": normalize_text(company.get("email_cobranca")),
        "socio_1": normalize_text(company.get("responsavel_nome")) or normalize_text(company.get("admin_nome")),
        "email_socio_1": normalize_text(company.get("admin_email")),
        "telefone_socio_1": whatsapp,
        "vendedor": "Oppi",
        "observacoes": (
            f"Migrado do Oppi Ponto (company_id={company.get('oppi_ponto_company_id')}). "
            "Cadastro tipo Empresa — fluxo comercial fechado."
        ),
        "status": MIGRATED_STATUS,
        "data_chamado": data_chamado,
        "servico": MIGRATED_SERVICE_NAME,
        "valor_proposta": valor,
        "colaboradores": colaboradores_txt,
    }


def map_closed_services(company: dict) -> list[dict]:
    return [
        {
            "servico": MIGRATED_SERVICE_NAME,
            "valor": _format_money(company.get("plano_valor", "")),
            "forma_pagamento": _map_forma_pagamento(company.get("pagamento_modalidade", "recorrente")),
            "vencimento": _iso_or_empty(company.get("plano_vencimento")),
        }
    ]


def map_payment_history(company: dict) -> list[dict]:
    status = _map_payment_status(company.get("pagamento_status", "pendente"))
    valor = _format_money(company.get("plano_valor", ""))
    due = _iso_or_empty(company.get("plano_vencimento")) or date.today().isoformat()
    return [
        {
            "data": due,
            "descricao": "Plano Oppi Ponto (migração)",
            "valor": valor,
            "status": status,
            "forma_pagamento": _map_forma_pagamento(company.get("pagamento_modalidade", "recorrente")),
        }
    ]


def map_access_fields(company: dict) -> dict:
    return {
        "email_login_gestor": normalize_text(company.get("admin_email")),
        "email_confirmacao_admin": normalize_text(company.get("admin_email_verificacao"))
        or normalize_text(company.get("admin_email")),
        "email_cobranca": normalize_text(company.get("email_cobranca")),
        "senha_acesso": "",
    }


def _local_lead_actions() -> dict[str, dict]:
    """Lê lead_actions só do disco/SQLite — sem Google Sheets."""
    import json

    from app.services.storage_paths import get_storage_dir

    merged: dict[str, dict] = {}
    path = get_storage_dir() / "lead_actions.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        bucket = raw.get(DEFAULT_TENANT_ID) if isinstance(raw, dict) else {}
        if isinstance(bucket, dict):
            for key, value in bucket.items():
                if isinstance(value, dict):
                    merged[str(key)] = value
    except Exception:
        pass

    try:
        from app.services.crm_local_db import load_lead_actions_store

        db_store = load_lead_actions_store() or {}
        bucket = db_store.get(DEFAULT_TENANT_ID) if isinstance(db_store, dict) else {}
        if isinstance(bucket, dict):
            for key, value in bucket.items():
                if isinstance(value, dict):
                    merged[str(key)] = value
    except Exception:
        pass
    return merged


def _cnpj_index_from_snapshot() -> dict[str, int]:
    """Índice CNPJ → sheet_row a partir do snapshot local (sem API)."""
    try:
        from app.services.legacy_core import (
            get_last_good_sheet_values,
            hydrate_sheet_cache_from_disk,
            _load_folha1_snapshot_values,
        )

        hydrate_sheet_cache_from_disk()
        values = get_last_good_sheet_values() or _load_folha1_snapshot_values()
    except Exception:
        values = None
    if not values or len(values) < 2:
        return {}

    headers = [normalize_text(h).lower() for h in values[0]]
    try:
        cnpj_idx = headers.index("cnpj")
    except ValueError:
        return {}

    index: dict[str, int] = {}
    for offset, row in enumerate(values[1:], start=2):
        if cnpj_idx >= len(row):
            continue
        digits = normalize_cnpj_for_duplicate(row[cnpj_idx])
        if digits:
            index[digits] = offset
    return index


def find_sheet_row_by_cnpj(cnpj: str) -> int | None:
    digits = normalize_cnpj_for_duplicate(cnpj)
    if not digits:
        return None
    return _cnpj_index_from_snapshot().get(digits)


def find_sheet_row_by_ponto_id(company_id: int) -> int | None:
    if not company_id:
        return None
    for sheet_row_key, record in _local_lead_actions().items():
        if int(record.get("oppi_ponto_company_id") or 0) != int(company_id):
            continue
        try:
            return int(sheet_row_key)
        except (TypeError, ValueError):
            continue
    return None


def preview_company(company: dict) -> dict:
    cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)
    existing_by_id = find_sheet_row_by_ponto_id(ponto_id) if ponto_id else None
    existing_by_cnpj = find_sheet_row_by_cnpj(cnpj) if cnpj else None
    existing = existing_by_id or existing_by_cnpj

    if not cnpj:
        action = "skip_missing_cnpj"
    elif existing and existing > 0:
        action = "update"
    else:
        action = "create"

    return {
        "action": action,
        "oppi_ponto_company_id": ponto_id,
        "empresa": normalize_text(company.get("razao_social")) or normalize_text(company.get("nome")),
        "cnpj": cnpj,
        "sheet_row": existing,
        "pagamento_status": company.get("pagamento_status"),
        "ativo": company.get("ativo"),
    }


def _persist_extras(sheet_row: int, company: dict, *, ativo_crm: bool, sync_sheet: bool = True) -> None:
    closed = map_closed_services(company)
    payments = map_payment_history(company)
    access = map_access_fields(company)
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)
    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, "empresa")
    save_cadastro_ativo(DEFAULT_TENANT_ID, sheet_row, ativo_crm)
    save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed, sync_sheet=sync_sheet)
    save_payment_history(DEFAULT_TENANT_ID, sheet_row, payments)
    save_access_fields(DEFAULT_TENANT_ID, sheet_row, access)
    form = map_ponto_company_to_form(company)
    funcionarios = 0
    try:
        funcionarios = int(company.get("funcionarios") or 0)
    except (TypeError, ValueError):
        funcionarios = 0
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {
            "oppi_ponto_company_id": ponto_id,
            "migrated_from_ponto": True,
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
            "colaboradores": form.get("colaboradores", ""),
            "valor_proposta": form.get("valor_proposta", ""),
            "servico": form.get("servico") or MIGRATED_SERVICE_NAME,
            "oppi_ponto_funcionarios": funcionarios,
            "oppi_ponto_contrato_aceito": bool(company.get("contrato_aceito")),
            "oppi_ponto_bloqueado": bool(company.get("bloqueado_plataforma")),
            "oppi_ponto_plano_valor": form.get("valor_proposta", ""),
        },
    )
    remember_migrated_company(company, sheet_row=sheet_row)


def apply_company(company: dict, *, local_only: bool = True) -> dict:
    """Grava no CRM como empresa.

    local_only=True:
    - Sempre garante índice local (CNPJ = Empresa na listagem).
    - Se CNPJ já existe na planilha: promove lead_actions + cria pendente se ainda não houver
      (garante aparecer mesmo com cache da planilha incompleto).
    - Senão: cria pendente local.
    """
    preview = preview_company(company)
    action = preview["action"]
    if action == "skip_missing_cnpj":
        return {**preview, "ok": False, "message": "CNPJ inválido ou ausente — não migrado."}

    form = map_ponto_company_to_form(company)
    ativo_crm = bool(company.get("ativo")) and not bool(company.get("bloqueado_plataforma"))
    cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))

    def _ensure_pending() -> int | None:
        try:
            return _upsert_migration_pending(company)
        except Exception:
            return None

    existing_sheet = find_sheet_row_by_cnpj(cnpj) if cnpj else None
    if existing_sheet and existing_sheet > 0:
        try:
            _persist_extras(existing_sheet, company, ativo_crm=ativo_crm)
        except Exception as err:
            # Mesmo com falha parcial, grava índice e tenta pendente para ficar visível.
            remember_migrated_company(company, sheet_row=existing_sheet)
            pending_row = _ensure_pending()
            if pending_row:
                try:
                    _persist_extras(pending_row, company, ativo_crm=ativo_crm)
                except Exception:
                    remember_migrated_company(company, sheet_row=pending_row)
            return {
                **preview,
                "action": "promote",
                "sheet_row": existing_sheet,
                "ok": True,
                "message": f"Promovido com ressalva ({err}). Pendente={pending_row}.",
            }

        pending_row = _ensure_pending()
        if pending_row:
            try:
                _persist_extras(pending_row, company, ativo_crm=ativo_crm)
            except Exception:
                remember_migrated_company(company, sheet_row=pending_row)
        return {
            **preview,
            "action": "promote",
            "sheet_row": existing_sheet,
            "ok": True,
            "message": f"CNPJ na planilha (linha {existing_sheet}) promovido + pendente local para garantir lista.",
        }

    # Novo / sem snapshot: pendente local
    if local_only or action == "create":
        try:
            sheet_row = _ensure_pending()
            if not sheet_row:
                raise RuntimeError("Não foi possível criar pendente local")
            _persist_extras(sheet_row, company, ativo_crm=ativo_crm)
            return {
                **preview,
                "action": "create",
                "sheet_row": sheet_row,
                "ok": True,
                "message": f"Empresa enfileirada localmente (id {sheet_row}).",
            }
        except Exception as err:
            # Último recurso: só índice
            remember_migrated_company(company, sheet_row=None)
            return {**preview, "ok": False, "message": f"Falha local: {err}"}

    remember_migrated_company(company, sheet_row=preview.get("sheet_row"))
    return {**preview, "ok": False, "message": "Modo local obrigatório nesta migração."}


def build_migration_audit(payload: dict | list) -> dict:
    """Compara JSON do Ponto com o que já está no CRM (local/pendente/snapshot)."""
    if isinstance(payload, dict):
        companies = payload.get("companies") or []
    else:
        companies = payload
    if not isinstance(companies, list):
        raise ValueError("JSON inválido")

    from app.services.pending_companies import list_pending_companies

    pending = list_pending_companies("pending") or []
    pending_migration = []
    pending_cnpjs = set()
    pending_ponto_ids = set()
    for item in pending:
        pl = item.get("payload") or {}
        digits = normalize_cnpj_for_duplicate(pl.get("cnpj"))
        obs = normalize_text(pl.get("observacoes"))
        servico = normalize_text(pl.get("servico"))
        tipo = normalize_text(pl.get("cadastro_tipo")).lower()
        is_mig = (
            tipo == "empresa"
            or "Migrado do Oppi Ponto" in obs
            or "company_id=" in obs
            or "Ponto Eletrônico" in servico
            or normalize_text(pl.get("vendedor")).lower() == "oppi"
        )
        if is_mig:
            pending_migration.append(item)
        if digits:
            pending_cnpjs.add(digits)
        if "company_id=" in obs:
            try:
                pending_ponto_ids.add(int(obs.split("company_id=")[1].split(")")[0].split(".")[0].split()[0]))
            except Exception:
                pass

    local_actions = _local_lead_actions()
    migrated_ponto_ids = set()
    empresa_rows = set()
    for key, record in local_actions.items():
        pid = int(record.get("oppi_ponto_company_id") or 0)
        if pid:
            migrated_ponto_ids.add(pid)
        if normalize_text(record.get("cadastro_tipo")).lower() == "empresa":
            try:
                empresa_rows.add(int(key))
            except Exception:
                pass

    snapshot_index = _cnpj_index_from_snapshot()
    snapshot_cnpjs = set(snapshot_index.keys())

    found = []
    missing = []
    as_empresa = []
    for company in companies:
        if not isinstance(company, dict):
            continue
        ponto_id = int(company.get("oppi_ponto_company_id") or 0)
        cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
        nome = normalize_text(company.get("razao_social")) or normalize_text(company.get("nome"))
        reasons = []
        if ponto_id and ponto_id in migrated_ponto_ids:
            reasons.append("lead_actions")
        if ponto_id and ponto_id in pending_ponto_ids:
            reasons.append("pending_obs")
        if cnpj and cnpj in pending_cnpjs:
            reasons.append("pending_cnpj")
        sheet_row = snapshot_index.get(cnpj) if cnpj else None
        if cnpj and cnpj in snapshot_cnpjs:
            reasons.append("snapshot")
        visible = False
        if cnpj and cnpj in pending_cnpjs:
            visible = True
        if sheet_row and sheet_row in empresa_rows:
            visible = True
        if cnpj and is_cnpj_migrated_empresa(cnpj):
            visible = True
        if ponto_id and ponto_id in migrated_ponto_ids:
            for key, record in local_actions.items():
                if int(record.get("oppi_ponto_company_id") or 0) != ponto_id:
                    continue
                if normalize_text(record.get("cadastro_tipo")).lower() == "empresa":
                    visible = True
                    break
        row = {
            "oppi_ponto_company_id": ponto_id,
            "empresa": nome,
            "cnpj": cnpj,
            "sheet_row": sheet_row,
            "found": bool(reasons),
            "visible_as_empresa": visible,
            "where": reasons,
        }
        if visible:
            as_empresa.append(row)
        if reasons:
            found.append(row)
        else:
            missing.append(row)

    not_visible = [r for r in found + missing if not r.get("visible_as_empresa")]
    return {
        "expected": len([c for c in companies if isinstance(c, dict)]),
        "found_count": len(found),
        "missing_count": len(missing),
        "pending_count": len(pending),
        "pending_migration_count": len(pending_migration),
        "pending_migration_cnpjs": len({
            normalize_cnpj_for_duplicate((i.get("payload") or {}).get("cnpj"))
            for i in pending_migration
            if normalize_cnpj_for_duplicate((i.get("payload") or {}).get("cnpj"))
        }),
        "visible_as_empresa": len(as_empresa),
        "need_promote": len(not_visible),
        "found": found,
        "missing": missing,
        "not_visible": not_visible,
    }


def _sheet_rows_for_company(company: dict) -> list[int]:
    """Todas as linhas CRM (planilha + pendente) ligadas a esta empresa do Ponto."""
    rows: set[int] = set()
    cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)

    if ponto_id:
        found = find_sheet_row_by_ponto_id(ponto_id)
        if found:
            rows.add(int(found))
    if cnpj:
        found = find_sheet_row_by_cnpj(cnpj)
        if found:
            rows.add(int(found))
        index = load_migration_index().get("by_cnpj") or {}
        entry = index.get(cnpj) or {}
        if entry.get("sheet_row"):
            try:
                rows.add(int(entry["sheet_row"]))
            except (TypeError, ValueError):
                pass

    try:
        from app.services.pending_companies import list_pending_companies

        # pending + synced — migração antiga pode ter marcado synced
        for pend in list_pending_companies(None) or []:
            pl = pend.get("payload") or {}
            if cnpj and normalize_cnpj_for_duplicate(pl.get("cnpj")) == cnpj:
                rows.add(int(pend.get("local_sheet_row") or -pend["id"]))
                continue
            if ponto_id and f"company_id={ponto_id}" in normalize_text(pl.get("observacoes")):
                rows.add(int(pend.get("local_sheet_row") or -pend["id"]))
    except Exception:
        pass

    return sorted(rows)


def _upsert_migration_pending(company: dict) -> int:
    """Garante pendente local status=pending para a empresa (cria ou reabre synced)."""
    from app.services.crm_local_db import mark_pending_company_pending, update_pending_company_payload
    from app.services.pending_companies import enqueue_payload_locally, list_pending_companies

    form = map_ponto_company_to_form(company)
    closed = map_closed_services(company)
    valor = closed[0].get("valor", "") if closed else form.get("valor_proposta", "")
    form["valor_proposta"] = valor
    form["servico"] = form.get("servico") or MIGRATED_SERVICE_NAME
    form["cadastro_tipo"] = "empresa"
    form["status"] = MIGRATED_STATUS
    # Sempre data de hoje para não sumir em filtro de período.
    form["data_chamado"] = date.today().strftime("%d/%m/%Y")
    cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)

    best: dict | None = None
    for pend in list_pending_companies(None) or []:
        pl = pend.get("payload") or {}
        match = False
        if cnpj and normalize_cnpj_for_duplicate(pl.get("cnpj")) == cnpj:
            match = True
        if ponto_id and f"company_id={ponto_id}" in normalize_text(pl.get("observacoes")):
            match = True
        if not match:
            continue
        if best is None or int(pend.get("id") or 0) >= int(best.get("id") or 0):
            best = pend

    if best is not None:
        pending_id = int(best["id"])
        payload = dict(best.get("payload") or {})
        payload.update(form)
        payload["cadastro_tipo"] = "empresa"
        payload["status"] = MIGRATED_STATUS
        payload["colaboradores"] = form.get("colaboradores", "")
        payload["valor_proposta"] = valor
        payload["servico"] = form.get("servico") or MIGRATED_SERVICE_NAME
        payload["data_chamado"] = form["data_chamado"]
        update_pending_company_payload(pending_id, payload)
        mark_pending_company_pending(pending_id, last_error="Migração Oppi Ponto (visível local)")
        return int(best.get("local_sheet_row") or -pending_id)

    try:
        return int(enqueue_payload_locally(form, last_error="Migração Oppi Ponto (local)"))
    except Exception:
        # Fallback sem cache da planilha
        from app.services.crm_local_db import enqueue_pending_company

        headers = [
            "Nome Empresas", "CNPJ", "Data do chamado", "Status WhatsApp",
            "Observações", "Serviços fechados", "Valor do serviço", "Colaboradores", "Vendedor",
        ]
        row_values = [""] * len(headers)
        mapping = {
            "Nome Empresas": form.get("empresa"),
            "CNPJ": form.get("cnpj"),
            "Data do chamado": form.get("data_chamado"),
            "Status WhatsApp": form.get("status"),
            "Observações": form.get("observacoes"),
            "Serviços fechados": form.get("servico"),
            "Valor do serviço": form.get("valor_proposta"),
            "Colaboradores": form.get("colaboradores"),
            "Vendedor": form.get("vendedor") or "Oppi",
        }
        for idx, header in enumerate(headers):
            row_values[idx] = normalize_text(mapping.get(header))
        pending_id = enqueue_pending_company(
            empresa=form.get("empresa") or "Empresa",
            payload=form,
            headers=headers,
            row_values=row_values,
            last_error="Migração Oppi Ponto (fallback)",
        )
        return -int(pending_id)


def force_restore_all_companies(payload: dict | list) -> dict:
    """Restaura à força as 20 empresas do JSON como pendentes visíveis."""
    if isinstance(payload, dict):
        companies = payload.get("companies") or []
    else:
        companies = payload
    if not isinstance(companies, list):
        raise ValueError("JSON inválido")

    try:
        from app.services.pending_companies import reopen_synced_migration_pendings

        reopen_synced_migration_pendings()
    except Exception:
        pass

    results = []
    for company in companies:
        if not isinstance(company, dict):
            continue
        form = map_ponto_company_to_form(company)
        cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
        ponto_id = int(company.get("oppi_ponto_company_id") or 0)
        try:
            sheet_row = _upsert_migration_pending(company)
            _persist_extras(sheet_row, company, ativo_crm=True, sync_sheet=False)
            remember_migrated_company(company, sheet_row=sheet_row)
            results.append(
                {
                    "ok": True,
                    "action": "force_restore",
                    "oppi_ponto_company_id": ponto_id,
                    "empresa": form.get("empresa"),
                    "cnpj": cnpj,
                    "sheet_row": sheet_row,
                    "valor": form.get("valor_proposta"),
                    "colaboradores": form.get("colaboradores"),
                    "message": f"Restaurada na lista (linha local {sheet_row})",
                }
            )
        except Exception as err:
            results.append(
                {
                    "ok": False,
                    "action": "force_restore",
                    "oppi_ponto_company_id": ponto_id,
                    "empresa": form.get("empresa"),
                    "cnpj": cnpj,
                    "sheet_row": None,
                    "message": str(err),
                }
            )

    from app.services.pending_companies import list_pending_companies, _is_migration_empresa_payload

    pending_mig = [
        item for item in (list_pending_companies("pending") or [])
        if _is_migration_empresa_payload(item.get("payload") or {})
    ]
    return {
        "apply": True,
        "action": "force_restore",
        "total": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "pending_migration_visible": len(pending_mig),
        "results": results,
        "audit": build_migration_audit(payload),
    }


def reprocess_finance_from_export(payload: dict | list) -> dict:
    """Reaplica valor/colaboradores e GARANTE as 20 empresas como pendente local visível."""
    if isinstance(payload, dict):
        companies = payload.get("companies") or []
    else:
        companies = payload
    if not isinstance(companies, list):
        raise ValueError("JSON inválido: esperado lista em 'companies'.")

    results = []
    for company in companies:
        if not isinstance(company, dict):
            continue
        cnpj = normalize_cnpj_for_duplicate(company.get("cnpj"))
        ponto_id = int(company.get("oppi_ponto_company_id") or 0)
        form = map_ponto_company_to_form(company)
        closed = map_closed_services(company)
        valor = closed[0].get("valor", "") if closed else form.get("valor_proposta", "")
        colaboradores = form.get("colaboradores", "")
        # Sempre ativo no CRM comercial para aparecer na lista Empresas.
        # Bloqueio real fica no Oppi Ponto (botões bloquear/liberar).
        ativo_crm = True
        created = False
        errors: list[str] = []
        sheet_row: int | None = None

        if not cnpj:
            results.append(
                {
                    "ok": False,
                    "action": "skip_missing_cnpj",
                    "oppi_ponto_company_id": ponto_id,
                    "empresa": form.get("empresa"),
                    "cnpj": cnpj,
                    "sheet_row": None,
                    "valor": valor,
                    "colaboradores": colaboradores,
                    "message": "CNPJ inválido — pulado.",
                }
            )
            continue

        try:
            existing_rows = _sheet_rows_for_company(company)
            sheet_row = _upsert_migration_pending(company)
            if not existing_rows:
                created = True
            _persist_extras(sheet_row, company, ativo_crm=ativo_crm, sync_sheet=False)
            # Também atualiza outras linhas já ligadas (planilha / lead_actions)
            for extra_row in existing_rows:
                if int(extra_row) == int(sheet_row):
                    continue
                try:
                    _persist_extras(int(extra_row), company, ativo_crm=ativo_crm, sync_sheet=False)
                except Exception as err:
                    errors.append(f"row {extra_row}: {err}")
            remember_migrated_company(company, sheet_row=sheet_row)
        except Exception as err:
            errors.append(str(err))
            sheet_row = None

        ok = sheet_row is not None
        results.append(
            {
                "ok": ok,
                "action": "reprocess_created" if created else "reprocess_finance",
                "oppi_ponto_company_id": ponto_id,
                "empresa": form.get("empresa"),
                "cnpj": cnpj,
                "sheet_row": sheet_row,
                "rows": [sheet_row] if sheet_row is not None else [],
                "valor": valor,
                "colaboradores": colaboradores,
                "message": (
                    ("Criada/reaberta · " if created else "Atualizada · ")
                    + f"{valor or 'sem valor'} · {colaboradores or 'sem colaboradores'}"
                    + (f" | avisos: {'; '.join(errors)}" if errors else "")
                ),
            }
        )

    return {
        "apply": True,
        "action": "reprocess_finance",
        "total": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "created": sum(1 for r in results if r.get("action") == "reprocess_created"),
        "results": results,
        "audit": build_migration_audit(payload),
    }


def migrate_companies(payload: dict | list, *, apply: bool = False, local_only: bool = True) -> dict:
    if isinstance(payload, dict):
        companies = payload.get("companies") or []
    else:
        companies = payload
    if not isinstance(companies, list):
        raise ValueError("JSON inválido: esperado lista em 'companies'.")

    results = []
    for item in companies:
        if not isinstance(item, dict):
            continue
        if apply:
            results.append(apply_company(item, local_only=local_only))
        else:
            results.append(preview_company(item))

    summary = {
        "apply": apply,
        "local_only": local_only if apply else None,
        "total": len(results),
        "create": sum(1 for r in results if r.get("action") == "create"),
        "update": sum(1 for r in results if r.get("action") == "update"),
        "promote": sum(1 for r in results if r.get("action") == "promote"),
        "skip_already": sum(1 for r in results if r.get("action") == "skip_already"),
        "skip_missing_cnpj": sum(1 for r in results if r.get("action") == "skip_missing_cnpj"),
        "ok": sum(1 for r in results if r.get("ok")) if apply else None,
        "failed": sum(1 for r in results if apply and not r.get("ok")),
        "results": results,
        "audit": build_migration_audit(payload) if apply else None,
    }
    return summary
