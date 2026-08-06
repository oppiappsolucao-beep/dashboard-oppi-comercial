"""Provisionamento via API: cadastro Comercial → financeiro → Oppi Ponto."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from app.services.closed_services import save_closed_services
from app.services.lead_actions_storage import DEFAULT_TENANT_ID, get_lead_action, save_lead_action
from app.services.legacy_core import (
    DuplicateRegistrationError,
    normalize_cnpj_for_duplicate,
    normalize_phone_for_duplicate,
    normalize_text,
)
from app.services.oppi_ponto_bridge import sync_or_onboard_company
from app.services.oppi_ponto_client import OppiPontoError, oppi_ponto_configured
from app.services.payment_history import save_payment_history
from app.services.registration import (
    save_access_fields,
    save_cadastro_tipo,
    save_new_company,
)

log = logging.getLogger(__name__)

SERVICE_NAME = "Ponto Eletrônico Oppi"

TIPO_NOVO_CLIENTE = "novo_cliente"
TIPO_NOVA_FILIAL = "nova_filial"

PAGAMENTO_MODALIDADES = {
    "boleto": "boleto",
    "cartao": "cartao_recorrente",
    "cartão": "cartao_recorrente",
    "cartao_recorrente": "cartao_recorrente",
    "cartão_recorrente": "cartao_recorrente",
    "cartao recorrente": "cartao_recorrente",
    "manual": "manual",
}

FORMA_PAGAMENTO_CRM = {
    "boleto": "Boleto",
    "cartao_recorrente": "Cartão",
    "manual": "Mensal",
}


class ProvisioningError(ValueError):
    """Erro de validação / regra de negócio no provisionamento."""


def normalize_pagamento_modalidade(value: str) -> str:
    key = normalize_text(value).lower().replace("-", "_")
    key = re.sub(r"\s+", "_", key)
    mapped = PAGAMENTO_MODALIDADES.get(key) or PAGAMENTO_MODALIDADES.get(key.replace("_", " "))
    if not mapped:
        raise ProvisioningError(
            "pagamento_modalidade inválida. Use: boleto, cartao_recorrente ou manual."
        )
    return mapped


def parse_plano_vencimento(value: str) -> str:
    """Aceita ISO (yyyy-mm-dd), dd-mm-aaaa ou dd/mm/aaaa → ISO."""
    raw = normalize_text(value)
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw[:10] if fmt.startswith("%Y") else raw, fmt).date().isoformat()
        except ValueError:
            continue
    # dd-mm-aaaa com hífen de 10 chars
    if len(raw) == 10 and raw[2] in "-/." and raw[5] in "-/.":
        try:
            day, month, year = int(raw[0:2]), int(raw[3:5]), int(raw[6:10])
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    raise ProvisioningError("plano_vencimento inválido. Use dd-mm-aaaa ou yyyy-mm-dd.")


def format_money(value: Any) -> str:
    raw = normalize_text(str(value) if value is not None else "")
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
    except ValueError as exc:
        raise ProvisioningError("plano_valor inválido.") from exc
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _digits_phone(value: str) -> str:
    return normalize_phone_for_duplicate(value) or re.sub(r"\D", "", value or "")


def _format_phone_display(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return normalize_text(value)


def _format_cnpj_display(digits: str) -> str:
    d = normalize_cnpj_for_duplicate(digits)
    if len(d) != 14:
        return normalize_text(digits)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def resolve_matriz_sheet_row(
    *,
    matriz_cnpj: str = "",
    gestor_email: str = "",
) -> int | None:
    """Localiza empresa matriz por CNPJ ou e-mail de login do gestor."""
    cnpj = normalize_cnpj_for_duplicate(matriz_cnpj)
    email = normalize_text(gestor_email).lower()

    if cnpj:
        try:
            from app.services.crm_registrations_storage import (
                find_registration_by_cnpj,
                is_crm_postgres_ready,
            )

            if is_crm_postgres_ready():
                hit = find_registration_by_cnpj(cnpj)
                if hit and getattr(hit, "sheet_row", None):
                    return int(hit.sheet_row)
        except Exception:
            log.exception("Falha ao buscar matriz por CNPJ no Postgres")

        try:
            from app.services.ponto_migration import load_migration_index

            entry = (load_migration_index().get("by_cnpj") or {}).get(cnpj) or {}
            row = int(entry.get("sheet_row") or 0)
            if row > 0:
                return row
        except Exception:
            pass

    if email:
        # Busca em lead_actions locais (e-mails de acesso)
        try:
            from app.services.crm_local_db import load_lead_actions_store

            store = load_lead_actions_store() or {}
            tenant_store = store.get(DEFAULT_TENANT_ID) or store.get("default") or {}
            if isinstance(tenant_store, dict):
                for sheet_key, payload in tenant_store.items():
                    if not isinstance(payload, dict):
                        continue
                    login = normalize_text(payload.get("email_login_gestor")).lower()
                    cobranca = normalize_text(payload.get("email_cobranca")).lower()
                    confirm = normalize_text(payload.get("email_confirmacao_admin")).lower()
                    if email in {login, cobranca, confirm}:
                        try:
                            row = int(sheet_key)
                        except (TypeError, ValueError):
                            continue
                        if row > 0:
                            return row
        except Exception:
            log.exception("Falha ao buscar matriz por e-mail do gestor")

    return None


def build_registration_form_from_api(payload: dict[str, Any]) -> dict[str, Any]:
    tipo = normalize_text(payload.get("tipo_cadastro")).lower() or TIPO_NOVO_CLIENTE
    if tipo not in {TIPO_NOVO_CLIENTE, TIPO_NOVA_FILIAL}:
        raise ProvisioningError("tipo_cadastro inválido. Use novo_cliente ou nova_filial.")

    razao = normalize_text(payload.get("razao_social") or payload.get("empresa"))
    cnpj = normalize_cnpj_for_duplicate(payload.get("cnpj"))
    whatsapp = normalize_text(payload.get("whatsapp") or payload.get("telefone"))
    telefone = normalize_text(payload.get("telefone") or whatsapp)
    responsavel = normalize_text(
        payload.get("responsavel_nome")
        or payload.get("admin_nome")
        or payload.get("gestor_login")
        or "Gestor"
    )
    email_login = normalize_text(payload.get("email_login") or payload.get("gestor_email"))
    email_cobranca = normalize_text(payload.get("email_cobranca") or email_login)
    email_verificacao = normalize_text(payload.get("email_verificacao") or email_login)

    if not razao:
        raise ProvisioningError("razao_social é obrigatória.")
    if len(cnpj) != 14:
        raise ProvisioningError("cnpj inválido — informe 14 dígitos.")
    if not whatsapp:
        raise ProvisioningError("whatsapp (ou telefone) é obrigatório.")
    if not _digits_phone(whatsapp):
        raise ProvisioningError("whatsapp inválido.")

    is_filial = tipo == TIPO_NOVA_FILIAL
    matriz_row = None
    if is_filial:
        matriz_row = resolve_matriz_sheet_row(
            matriz_cnpj=normalize_text(payload.get("matriz_cnpj")),
            gestor_email=normalize_text(payload.get("gestor_email") or email_login),
        )
        if not matriz_row:
            try:
                matriz_row = int(payload.get("empresa_matriz_sheet_row") or 0) or None
            except (TypeError, ValueError):
                matriz_row = None
        if not matriz_row:
            raise ProvisioningError(
                "Nova filial exige gestor existente: informe gestor_email, matriz_cnpj "
                "ou empresa_matriz_sheet_row."
            )
        if not email_login:
            # tenta herdar e-mail da matriz
            access = {}
            try:
                from app.services.registration import load_access_fields

                access = load_access_fields(DEFAULT_TENANT_ID, matriz_row)
            except Exception:
                pass
            email_login = access.get("email_login_gestor") or ""
            email_cobranca = email_cobranca or access.get("email_cobranca") or email_login
            email_verificacao = email_verificacao or access.get("email_confirmacao_admin") or email_login

    if not email_login or "@" not in email_login:
        raise ProvisioningError("email_login é obrigatório.")
    if not email_cobranca or "@" not in email_cobranca:
        raise ProvisioningError("email_cobranca é obrigatório.")

    cargo = normalize_text(payload.get("cargo_gestor"))
    observacoes_parts = [
        f"Provisionado via API Comercial ({tipo}).",
    ]
    if cargo:
        observacoes_parts.append(f"Cargo do gestor: {cargo}.")
    if normalize_text(payload.get("observacoes")):
        observacoes_parts.append(normalize_text(payload.get("observacoes")))

    return {
        "tipo_cadastro_api": tipo,
        "cadastro_tipo": "empresa",
        "empresa": razao,
        "cnpj": _format_cnpj_display(cnpj),
        "telefone_b2b": _format_phone_display(whatsapp),
        "telefone_fixo": _format_phone_display(telefone) if telefone != whatsapp else "",
        "socio_1": responsavel,
        "email_socio_1": email_login,
        "telefone_socio_1": _format_phone_display(whatsapp),
        "email_empresa": email_cobranca,
        "vendedor": normalize_text(payload.get("vendedor")) or "API",
        "status": "Fechado",
        "data_chamado": date.today().strftime("%d/%m/%Y"),
        "servico": SERVICE_NAME,
        "observacoes": " ".join(observacoes_parts),
        "is_filial": is_filial,
        "empresa_matriz_sheet_row": matriz_row,
        "colaboradores": normalize_text(payload.get("colaboradores")),
        "_access": {
            "email_login_gestor": email_login,
            "email_confirmacao_admin": email_verificacao or email_login,
            "email_cobranca": email_cobranca,
            "senha_acesso": normalize_text(payload.get("senha") or payload.get("password")),
        },
        "_gestor_login": normalize_text(payload.get("gestor_login") or responsavel),
        "_cargo_gestor": cargo,
    }


def build_financeiro_from_api(payload: dict[str, Any]) -> tuple[list[dict], list[dict], str]:
    modalidade = normalize_pagamento_modalidade(
        payload.get("pagamento_modalidade") or payload.get("forma_pagamento") or "manual"
    )
    valor = format_money(payload.get("plano_valor") or payload.get("valor"))
    if not valor:
        raise ProvisioningError("plano_valor é obrigatório.")
    vencimento = parse_plano_vencimento(payload.get("plano_vencimento") or "")
    if not vencimento:
        raise ProvisioningError("plano_vencimento é obrigatório.")

    forma = FORMA_PAGAMENTO_CRM[modalidade]
    closed = [
        {
            "servico": SERVICE_NAME,
            "valor": valor,
            "forma_pagamento": forma,
            "vencimento": vencimento,
        }
    ]
    payments = [
        {
            "data": vencimento,
            "descricao": f"Plano Oppi Ponto ({modalidade})",
            "valor": valor,
            "status": "Pendente",
            "forma_pagamento": forma,
        }
    ]
    return closed, payments, modalidade


def provision_cadastro(
    payload: dict[str, Any],
    *,
    sincronizar_ponto: bool | None = None,
) -> dict[str, Any]:
    """
    Fluxo completo:
    1) Cria empresa/filial no Comercial
    2) Grava acesso + financeiro
    3) Onboard / vínculo no Oppi Ponto (opcional)
    """
    form = build_registration_form_from_api(payload)
    access = form.pop("_access")
    gestor_login = form.pop("_gestor_login", "")
    cargo = form.pop("_cargo_gestor", "")
    tipo_api = form.pop("tipo_cadastro_api")
    closed, payments, modalidade = build_financeiro_from_api(payload)

    form["valor_proposta"] = closed[0]["valor"]

    try:
        sheet_row = save_new_company(form)
    except DuplicateRegistrationError as exc:
        raise ProvisioningError(str(exc)) from exc
    except ValueError as exc:
        raise ProvisioningError(str(exc)) from exc

    save_cadastro_tipo(DEFAULT_TENANT_ID, sheet_row, "empresa")
    save_access_fields(DEFAULT_TENANT_ID, sheet_row, access)
    save_closed_services(DEFAULT_TENANT_ID, sheet_row, closed, sync_sheet=True)
    save_payment_history(DEFAULT_TENANT_ID, sheet_row, payments)
    save_lead_action(
        DEFAULT_TENANT_ID,
        sheet_row,
        {
            "pagamento_modalidade": modalidade,
            "gestor_login": gestor_login,
            "cargo_gestor": cargo,
            "provisioned_via_api": True,
            "provisioned_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    sync = sincronizar_ponto if sincronizar_ponto is not None else bool(
        payload.get("sincronizar_ponto", True)
    )
    ponto_result: dict[str, Any] | None = None
    if sync:
        if not oppi_ponto_configured():
            ponto_result = {
                "ok": False,
                "action": "skipped",
                "message": "Oppi Ponto não configurado (OPPI_PONTO_API_URL / OPPI_PONTO_CRM_API_KEY).",
            }
        else:
            try:
                ponto_result = sync_or_onboard_company(
                    sheet_row,
                    values=form,
                    access=access,
                    closed_services=closed,
                    pagamento_modalidade=modalidade,
                    vincular_gestor_existente=(tipo_api == TIPO_NOVA_FILIAL),
                    admin_nome_override=gestor_login or None,
                )
            except OppiPontoError as exc:
                log.warning("Onboard Ponto falhou (sheet_row=%s): %s", sheet_row, exc)
                ponto_result = {
                    "ok": False,
                    "action": "onboard_failed",
                    "message": str(exc),
                    "status_code": exc.status_code,
                }

    return {
        "ok": True,
        "sheet_row": sheet_row,
        "tipo_cadastro": tipo_api,
        "cadastro_tipo": "empresa",
        "empresa": form.get("empresa"),
        "cnpj": normalize_cnpj_for_duplicate(form.get("cnpj")),
        "is_filial": bool(form.get("is_filial")),
        "empresa_matriz_sheet_row": form.get("empresa_matriz_sheet_row"),
        "financeiro": {
            "plano_valor": closed[0]["valor"],
            "plano_vencimento": closed[0]["vencimento"],
            "pagamento_modalidade": modalidade,
            "forma_pagamento": closed[0]["forma_pagamento"],
        },
        "acesso": {
            "email_login": access.get("email_login_gestor"),
            "email_cobranca": access.get("email_cobranca"),
            "email_verificacao": access.get("email_confirmacao_admin"),
        },
        "ponto": ponto_result,
    }


def get_cadastro_by_cnpj(cnpj: str) -> dict[str, Any] | None:
    digits = normalize_cnpj_for_duplicate(cnpj)
    if len(digits) != 14:
        raise ProvisioningError("CNPJ inválido.")

    sheet_row = None
    empresa = ""
    try:
        from app.services.crm_registrations_storage import (
            find_registration_by_cnpj,
            is_crm_postgres_ready,
            registration_to_payload,
        )

        if is_crm_postgres_ready():
            hit = find_registration_by_cnpj(digits)
            if hit:
                payload = registration_to_payload(hit)
                sheet_row = int(getattr(hit, "sheet_row", 0) or 0)
                empresa = normalize_text(payload.get("empresa"))
    except Exception:
        log.exception("Falha ao buscar cadastro por CNPJ")

    if not sheet_row:
        try:
            from app.services.ponto_migration import load_migration_index

            entry = (load_migration_index().get("by_cnpj") or {}).get(digits) or {}
            sheet_row = int(entry.get("sheet_row") or 0) or None
            empresa = normalize_text(entry.get("empresa"))
        except Exception:
            pass

    if not sheet_row:
        return None

    stored = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    return {
        "sheet_row": sheet_row,
        "empresa": empresa,
        "cnpj": digits,
        "cadastro_tipo": normalize_text(stored.get("cadastro_tipo")) or "empresa",
        "oppi_ponto_company_id": stored.get("oppi_ponto_company_id"),
        "pagamento_modalidade": stored.get("pagamento_modalidade"),
        "provisioned_via_api": bool(stored.get("provisioned_via_api")),
    }
