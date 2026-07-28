"""Migração Oppi Ponto → CRM: clientes entram como empresa (não lead).

Fluxo: empresa → fechamento → financeiro → suporte.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

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
    if isinstance(value, datetime):
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


def _persist_extras(sheet_row: int, company: dict, *, ativo_crm: bool) -> None:
    closed = map_closed_services(company)
    payments = map_payment_history(company)
    access = map_access_fields(company)
    ponto_id = int(company.get("oppi_ponto_company_id") or 0)
    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, "empresa")
    save_cadastro_ativo(DEFAULT_TENANT_ID, sheet_row, ativo_crm)
    save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed)
    save_payment_history(DEFAULT_TENANT_ID, sheet_row, payments)
    save_access_fields(DEFAULT_TENANT_ID, sheet_row, access)
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {
            "oppi_ponto_company_id": ponto_id,
            "migrated_from_ponto": True,
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def apply_company(company: dict, *, local_only: bool = True) -> dict:
    """Grava no CRM como empresa.

    local_only=True (padrão na migração): enfileira local sem bater na cota Google Sheets.
    As empresas aparecem em Empresas imediatamente e sincronizam depois.
    """
    preview = preview_company(company)
    action = preview["action"]
    if action == "skip_missing_cnpj":
        return {**preview, "ok": False, "message": "CNPJ inválido ou ausente — não migrado."}

    form = map_ponto_company_to_form(company)
    ativo_crm = bool(company.get("ativo")) and not bool(company.get("bloqueado_plataforma"))

    if local_only or action == "create":
        try:
            from app.services.pending_companies import enqueue_payload_locally

            sheet_row = enqueue_payload_locally(form, last_error="Migração Oppi Ponto (local)")
            _persist_extras(sheet_row, company, ativo_crm=ativo_crm)
            return {
                **preview,
                "action": "create",
                "sheet_row": sheet_row,
                "ok": True,
                "message": f"Empresa enfileirada localmente (id {sheet_row}). Aparece em Empresas agora.",
            }
        except Exception as err:
            return {**preview, "ok": False, "message": f"Falha local: {err}"}

    # Caminho legado (update na planilha) — só se explicitamente local_only=False
    try:
        if action == "update":
            sheet_row = int(preview["sheet_row"])
            save_company_edit(sheet_row, form)
        else:
            sheet_row = save_new_company(form)
    except DuplicateRegistrationError as err:
        sheet_row = find_sheet_row_by_cnpj(form.get("cnpj", ""))
        if not sheet_row:
            return {**preview, "ok": False, "message": str(err)}
        try:
            save_company_edit(sheet_row, form)
        except Exception as edit_err:
            if "429" in str(edit_err) or "Quota" in str(edit_err):
                return {**preview, "ok": False, "message": "Cota Google Sheets esgotada. Aguarde 2 minutos e tente de novo."}
            return {**preview, "ok": False, "message": str(edit_err)}
        action = "update"
    except Exception as err:
        if "429" in str(err) or "Quota" in str(err):
            return {**preview, "ok": False, "message": "Cota Google Sheets esgotada. Aguarde 2 minutos e tente de novo."}
        return {**preview, "ok": False, "message": str(err)}

    _persist_extras(sheet_row, company, ativo_crm=ativo_crm)
    return {
        **preview,
        "action": action,
        "sheet_row": sheet_row,
        "ok": True,
        "message": f"{'Atualizada' if action == 'update' else 'Criada'} como empresa (linha {sheet_row}).",
    }


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
    pending_cnpjs = set()
    pending_ponto_ids = set()
    for item in pending:
        pl = item.get("payload") or {}
        digits = normalize_cnpj_for_duplicate(pl.get("cnpj"))
        if digits:
            pending_cnpjs.add(digits)
        obs = normalize_text(pl.get("observacoes"))
        if "company_id=" in obs:
            try:
                pending_ponto_ids.add(int(obs.split("company_id=")[1].split(")")[0].split(".")[0].split()[0]))
            except Exception:
                pass

    local_actions = _local_lead_actions()
    migrated_ponto_ids = set()
    for record in local_actions.values():
        pid = int(record.get("oppi_ponto_company_id") or 0)
        if pid:
            migrated_ponto_ids.add(pid)

    snapshot_cnpjs = set(_cnpj_index_from_snapshot().keys())

    found = []
    missing = []
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
        if cnpj and cnpj in snapshot_cnpjs:
            reasons.append("snapshot")
        row = {
            "oppi_ponto_company_id": ponto_id,
            "empresa": nome,
            "cnpj": cnpj,
            "found": bool(reasons),
            "where": reasons,
        }
        if reasons:
            found.append(row)
        else:
            missing.append(row)

    return {
        "expected": len(companies),
        "found_count": len(found),
        "missing_count": len(missing),
        "pending_count": len(pending),
        "found": found,
        "missing": missing,
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
            # Idempotente: se já migrado por ponto_id, não duplica pending.
            ponto_id = int(item.get("oppi_ponto_company_id") or 0)
            existing_id = find_sheet_row_by_ponto_id(ponto_id) if ponto_id else None
            cnpj = normalize_cnpj_for_duplicate(item.get("cnpj"))
            already_pending = False
            if cnpj:
                try:
                    from app.services.pending_companies import list_pending_companies

                    for pend in list_pending_companies("pending") or []:
                        if normalize_cnpj_for_duplicate((pend.get("payload") or {}).get("cnpj")) == cnpj:
                            already_pending = True
                            existing_id = int(pend.get("local_sheet_row") or -pend["id"])
                            break
                except Exception:
                    pass
            if existing_id or already_pending:
                sheet_row = int(existing_id or 0)
                ativo_crm = bool(item.get("ativo")) and not bool(item.get("bloqueado_plataforma"))
                try:
                    if sheet_row:
                        _persist_extras(sheet_row, item, ativo_crm=ativo_crm)
                except Exception:
                    pass
                results.append(
                    {
                        "action": "skip_already",
                        "oppi_ponto_company_id": ponto_id,
                        "empresa": normalize_text(item.get("razao_social")) or normalize_text(item.get("nome")),
                        "cnpj": cnpj,
                        "sheet_row": sheet_row or None,
                        "ok": True,
                        "message": "Já existia — extras atualizados, sem duplicar.",
                    }
                )
                continue
            results.append(apply_company(item, local_only=local_only))
        else:
            results.append(preview_company(item))

    summary = {
        "apply": apply,
        "local_only": local_only if apply else None,
        "total": len(results),
        "create": sum(1 for r in results if r.get("action") == "create"),
        "update": sum(1 for r in results if r.get("action") == "update"),
        "skip_already": sum(1 for r in results if r.get("action") == "skip_already"),
        "skip_missing_cnpj": sum(1 for r in results if r.get("action") == "skip_missing_cnpj"),
        "ok": sum(1 for r in results if r.get("ok")) if apply else None,
        "failed": sum(1 for r in results if apply and not r.get("ok")),
        "results": results,
        "audit": build_migration_audit(payload) if apply else None,
    }
    return summary
