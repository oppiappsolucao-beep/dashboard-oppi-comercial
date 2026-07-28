"""Bridge CRM ↔ Oppi Ponto: resolver company_id, bloquear/liberar, onboard."""
from __future__ import annotations

import logging
import secrets
import string
from typing import Any

from app.services.lead_actions_storage import DEFAULT_TENANT_ID, get_lead_action, save_lead_action
from app.services.legacy_core import normalize_cnpj_for_duplicate, normalize_text
from app.services.oppi_ponto_client import (
    OppiPontoError,
    get_company_by_cnpj,
    onboard_company,
    oppi_ponto_configured,
    release_payment,
    set_company_status,
)
from app.services.ponto_migration import load_migration_index, remember_migrated_company
from app.services.registration import load_access_fields, save_access_fields

log = logging.getLogger(__name__)


def generate_access_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(8, length)))


def refresh_ponto_snapshot(sheet_row: int, *, cnpj: str = "") -> dict:
    """Atualiza snapshot local (funcionários, contrato, bloqueio) a partir do Oppi Ponto."""
    snapshot = {
        "company_id": resolve_oppi_ponto_company_id(sheet_row, cnpj=cnpj, lookup_remote=False),
        "funcionarios": 0,
        "contrato_aceito": None,
        "bloqueado": False,
        "ativo": True,
        "plano_valor": "",
        "admin_nome": "",
        "admin_email": "",
    }
    stored = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    try:
        snapshot["funcionarios"] = int(stored.get("oppi_ponto_funcionarios") or 0)
    except (TypeError, ValueError):
        snapshot["funcionarios"] = 0
    if "oppi_ponto_contrato_aceito" in stored:
        snapshot["contrato_aceito"] = bool(stored.get("oppi_ponto_contrato_aceito"))
    snapshot["bloqueado"] = bool(stored.get("oppi_ponto_bloqueado"))
    snapshot["plano_valor"] = normalize_text(stored.get("valor_proposta") or stored.get("oppi_ponto_plano_valor"))

    if not oppi_ponto_configured():
        return snapshot

    digits = normalize_cnpj_for_duplicate(cnpj)
    try:
        remote = None
        if digits:
            remote = get_company_by_cnpj(digits)
        if isinstance(remote, dict) and remote.get("id"):
            company_id = int(remote["id"])
            funcionarios = int(remote.get("funcionarios") or 0)
            contrato = bool(remote.get("contrato_aceito"))
            bloqueado = bool(remote.get("bloqueado_plataforma"))
            ativo = bool(remote.get("ativo", True))
            plano_valor = normalize_text(remote.get("plano_valor"))
            save_lead_action(
                DEFAULT_TENANT_ID,
                sheet_row,
                {
                    "oppi_ponto_company_id": company_id,
                    "oppi_ponto_funcionarios": funcionarios,
                    "oppi_ponto_contrato_aceito": contrato,
                    "oppi_ponto_bloqueado": bloqueado,
                    "oppi_ponto_plano_valor": plano_valor,
                },
            )
            snapshot.update(
                {
                    "company_id": company_id,
                    "funcionarios": funcionarios,
                    "contrato_aceito": contrato,
                    "bloqueado": bloqueado,
                    "ativo": ativo,
                    "plano_valor": plano_valor,
                    "admin_nome": normalize_text(remote.get("admin_nome")),
                    "admin_email": normalize_text(remote.get("admin_email")),
                }
            )
    except OppiPontoError:
        pass
    except Exception:
        log.exception("Falha ao atualizar snapshot Oppi Ponto")
    return snapshot


def resolve_oppi_ponto_company_id(
    sheet_row: int,
    *,
    cnpj: str = "",
    lookup_remote: bool = True,
) -> int | None:
    stored = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    try:
        local_id = int(stored.get("oppi_ponto_company_id") or 0)
    except (TypeError, ValueError):
        local_id = 0
    if local_id > 0:
        return local_id

    digits = normalize_cnpj_for_duplicate(cnpj)
    if digits:
        entry = (load_migration_index().get("by_cnpj") or {}).get(digits) or {}
        try:
            indexed = int(entry.get("oppi_ponto_company_id") or 0)
        except (TypeError, ValueError):
            indexed = 0
        if indexed > 0:
            save_lead_action(DEFAULT_TENANT_ID, sheet_row, {"oppi_ponto_company_id": indexed})
            return indexed

    if lookup_remote and digits and oppi_ponto_configured():
        try:
            remote = get_company_by_cnpj(digits)
            remote_id = int(remote.get("id") or 0)
            if remote_id > 0:
                save_lead_action(DEFAULT_TENANT_ID, sheet_row, {"oppi_ponto_company_id": remote_id})
                remember_migrated_company(
                    {
                        "oppi_ponto_company_id": remote_id,
                        "cnpj": digits,
                        "razao_social": remote.get("razao_social") or remote.get("nome"),
                    },
                    sheet_row=sheet_row,
                )
                return remote_id
        except OppiPontoError as exc:
            if exc.status_code != 404:
                log.warning("Falha ao buscar empresa no Ponto por CNPJ: %s", exc)
    return None


def block_company_on_ponto(sheet_row: int, *, cnpj: str = "") -> dict:
    company_id = resolve_oppi_ponto_company_id(sheet_row, cnpj=cnpj)
    if not company_id:
        raise OppiPontoError("Empresa ainda não está vinculada ao Oppi Ponto (sem company_id / CNPJ).")
    result = set_company_status(company_id, ativo=False, bloqueado=True)
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {"oppi_ponto_bloqueado": True, "oppi_ponto_company_id": company_id},
    )
    return {"ok": True, "action": "block", "company_id": company_id, "result": result}


def unblock_company_on_ponto(sheet_row: int, *, cnpj: str = "") -> dict:
    company_id = resolve_oppi_ponto_company_id(sheet_row, cnpj=cnpj)
    if not company_id:
        raise OppiPontoError("Empresa ainda não está vinculada ao Oppi Ponto (sem company_id / CNPJ).")
    result = set_company_status(company_id, ativo=True, bloqueado=False)
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {"oppi_ponto_bloqueado": False, "oppi_ponto_company_id": company_id},
    )
    return {"ok": True, "action": "unblock", "company_id": company_id, "result": result}


def release_payment_on_ponto(
    sheet_row: int,
    *,
    cnpj: str = "",
    motivo: str = "Liberação manual pelo CRM Comercial",
    plano_vencimento: str | None = None,
) -> dict:
    company_id = resolve_oppi_ponto_company_id(sheet_row, cnpj=cnpj)
    if not company_id:
        raise OppiPontoError("Empresa ainda não está vinculada ao Oppi Ponto (sem company_id / CNPJ).")
    result = release_payment(
        company_id,
        motivo=motivo or "Liberação manual pelo CRM Comercial",
        plano_vencimento=plano_vencimento or None,
        aplicar_filiais=True,
    )
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {"oppi_ponto_bloqueado": False, "oppi_ponto_company_id": company_id},
    )
    return {"ok": True, "action": "release_payment", "company_id": company_id, "result": result}


def _pick_email(*candidates: str) -> str:
    for value in candidates:
        text = normalize_text(value)
        if text and "@" in text:
            return text
    return ""


def build_onboard_payload_from_crm(
    *,
    values: dict,
    access: dict | None = None,
    closed_services: list[dict] | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    access = access or {}
    closed = closed_services or []
    primary = closed[0] if closed else {}
    password = password or normalize_text(access.get("senha_acesso")) or generate_access_password()
    email_login = _pick_email(
        access.get("email_login_gestor"),
        values.get("email_socio_1"),
        values.get("email_empresa"),
        values.get("email"),
    )
    email_cobranca = _pick_email(
        access.get("email_cobranca"),
        values.get("email_empresa"),
        values.get("email"),
        email_login,
    )
    email_verificacao = _pick_email(
        access.get("email_confirmacao_admin"),
        email_login,
        email_cobranca,
    )
    admin_nome = (
        normalize_text(values.get("socio_1"))
        or normalize_text(values.get("empresa"))
        or "Gestor"
    )
    valor = normalize_text(primary.get("valor")) or normalize_text(values.get("valor_proposta"))
    vencimento = normalize_text(primary.get("vencimento")) or ""
    whatsapp = normalize_text(values.get("telefone_b2b")) or normalize_text(values.get("telefone_socio_1"))
    telefone = normalize_text(values.get("telefone_fixo")) or whatsapp

    return {
        "admin_nome": admin_nome,
        "responsavel_nome": admin_nome,
        "cnpj": normalize_cnpj_for_duplicate(values.get("cnpj")),
        "razao_social": normalize_text(values.get("empresa")) or "Empresa",
        "telefone": telefone,
        "whatsapp": whatsapp,
        "email_verificacao": email_verificacao,
        "email_cobranca": email_cobranca,
        "email_login": email_login,
        "password": password,
        "password_confirm": password,
        "plano_vencimento": vencimento or None,
        "plano_valor": valor.replace("R$", "").strip() if valor else "",
        # Evita Asaas automático no fechamento comercial; cobrança fica no CRM.
        "pagamento_modalidade": "manual",
        "vincular_gestor_existente": False,
        "_generated_password": password,
    }


def sync_or_onboard_company(
    sheet_row: int,
    *,
    values: dict,
    access: dict | None = None,
    closed_services: list[dict] | None = None,
    force_create: bool = False,
) -> dict:
    """Se CNPJ já existe no Ponto, só vincula. Senão, faz onboard."""
    if not oppi_ponto_configured():
        raise OppiPontoError(
            "Oppi Ponto não configurado. Defina OPPI_PONTO_API_URL e OPPI_PONTO_CRM_API_KEY."
        )

    cnpj = normalize_cnpj_for_duplicate(values.get("cnpj"))
    if len(cnpj) != 14:
        raise OppiPontoError("CNPJ inválido — não é possível liberar acesso no Oppi Ponto.")

    existing_id = resolve_oppi_ponto_company_id(sheet_row, cnpj=cnpj, lookup_remote=True)
    if existing_id and not force_create:
        return {
            "ok": True,
            "action": "linked_existing",
            "company_id": existing_id,
            "message": f"Empresa já existe no Oppi Ponto (#{existing_id}). Vinculada ao CRM.",
            "password": None,
        }

    access = access if access is not None else load_access_fields(DEFAULT_TENANT_ID, sheet_row)
    payload = build_onboard_payload_from_crm(
        values=values,
        access=access,
        closed_services=closed_services,
    )
    if not payload.get("email_login") or not payload.get("email_cobranca"):
        raise OppiPontoError(
            "Informe e-mail de login do gestor e e-mail de cobrança antes de liberar no Oppi Ponto."
        )

    password = payload.pop("_generated_password", None)
    try:
        created = onboard_company(payload)
    except OppiPontoError as exc:
        # CNPJ já cadastrado: tenta vincular
        detail = str(exc.payload or exc)
        if exc.status_code == 400 and "CNPJ" in str(detail).upper():
            remote = get_company_by_cnpj(cnpj)
            remote_id = int(remote.get("id") or 0)
            if remote_id:
                save_lead_action(
                    DEFAULT_TENANT_ID,
                    sheet_row,
                    {"oppi_ponto_company_id": remote_id, "oppi_ponto_onboarded": True},
                )
                remember_migrated_company(
                    {
                        "oppi_ponto_company_id": remote_id,
                        "cnpj": cnpj,
                        "razao_social": values.get("empresa"),
                    },
                    sheet_row=sheet_row,
                )
                return {
                    "ok": True,
                    "action": "linked_existing",
                    "company_id": remote_id,
                    "message": f"CNPJ já existia no Ponto (#{remote_id}). Vinculado.",
                    "password": None,
                }
        raise

    company_id = int(created.get("id") or 0)
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {
            "oppi_ponto_company_id": company_id,
            "oppi_ponto_onboarded": True,
            "oppi_ponto_onboarded_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        },
    )
    save_access_fields(
        DEFAULT_TENANT_ID,
        sheet_row,
        {
            "email_login_gestor": payload.get("email_login", ""),
            "email_confirmacao_admin": payload.get("email_verificacao", ""),
            "email_cobranca": payload.get("email_cobranca", ""),
            "senha_acesso": password or "",
        },
    )
    remember_migrated_company(
        {
            "oppi_ponto_company_id": company_id,
            "cnpj": cnpj,
            "razao_social": values.get("empresa"),
        },
        sheet_row=sheet_row,
    )
    return {
        "ok": True,
        "action": "onboarded",
        "company_id": company_id,
        "message": f"Acesso liberado no Oppi Ponto (empresa #{company_id}).",
        "password": password,
        "result": created,
    }


def maybe_auto_onboard_on_empresa(
    sheet_row: int,
    *,
    values: dict,
    previous_tipo: str = "",
    new_tipo: str = "",
    status: str = "",
) -> dict | None:
    """Dispara onboard ao virar Empresa ou fechar negócio (idempotente)."""
    if not oppi_ponto_configured():
        return None
    tipo = normalize_text(new_tipo).lower()
    prev = normalize_text(previous_tipo).lower()
    status_n = normalize_text(status).lower()
    should = False
    if tipo == "empresa" and prev != "empresa":
        should = True
    if tipo == "empresa" and status_n in {"fechado", "cliente", "ganho", "closed"}:
        should = True
    if not should:
        return None
    try:
        from app.services.closed_services import load_closed_services

        closed = load_closed_services(
            DEFAULT_TENANT_ID,
            sheet_row,
            servico=values.get("servico", ""),
            valor_proposta=values.get("valor_proposta", ""),
        )
        return sync_or_onboard_company(
            sheet_row,
            values=values,
            closed_services=closed,
        )
    except OppiPontoError as exc:
        log.warning("Auto-onboard Oppi Ponto falhou (sheet_row=%s): %s", sheet_row, exc)
        return {"ok": False, "action": "onboard_failed", "message": str(exc)}
    except Exception as exc:
        log.exception("Auto-onboard Oppi Ponto inesperado")
        return {"ok": False, "action": "onboard_failed", "message": str(exc)}
