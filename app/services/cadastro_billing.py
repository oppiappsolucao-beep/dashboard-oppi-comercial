"""Plano contratado e geração de fatura Asaas no cadastro individual."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.config import settings
from app.services.asaas_client import (
    AsaasError,
    create_customer,
    create_payment,
    create_subscription,
    fetch_dashboard_payload,
    find_customers,
    is_configured,
    list_payments_for_customer,
    update_customer,
)
from app.services.financeiro import classify_payment, format_brl, format_date_br
from app.services.lead_actions_storage import get_lead_action, save_lead_action
from app.services.legacy_core import (
    normalize_cnpj_for_duplicate,
    normalize_digits,
    normalize_text,
    parse_money,
)
from app.services.payment_history import load_payment_history, save_payment_history

PLAN_CYCLE_OPTIONS = [
    ("mensal", "Mensal"),
    ("anual", "Anual"),
    ("avulso", "Avulso"),
]
BILLING_BANK_OPTIONS = [
    ("asaas", "Asaas"),
    ("mercado_pago", "Mercado Pago"),
]
BILLING_FORM_OPTIONS = [
    ("cartao_recorrente", "Cartão recorrente"),
    ("boleto_recorrente", "Boleto recorrente"),
    ("pix_recorrente", "Pix recorrente"),
    ("cartao_avulso", "Cartão de crédito (Avulso)"),
    ("pix_boleto", "Pix/Boleto (Avulso)"),
]
MERCADO_PAGO_FORM_OPTIONS = BILLING_FORM_OPTIONS
_CYCLE_KEYS = {key for key, _ in PLAN_CYCLE_OPTIONS}
_ASAAS_FORM_KEYS = {key for key, _ in BILLING_FORM_OPTIONS} | {"pix"}
_MP_FORM_KEYS = {key for key, _ in MERCADO_PAGO_FORM_OPTIONS}
_FORM_KEYS = _ASAAS_FORM_KEYS | _MP_FORM_KEYS


def cycle_label(key: str) -> str:
    mapping = dict(PLAN_CYCLE_OPTIONS)
    return mapping.get(normalize_text(key).lower(), "—")


def forma_label(key: str) -> str:
    mapping = dict(BILLING_FORM_OPTIONS)
    mapping.update(dict(MERCADO_PAGO_FORM_OPTIONS))
    mapping["pix"] = "PIX Avulso"
    return mapping.get(normalize_text(key).lower(), "—")


def bank_label(key: str) -> str:
    return dict(BILLING_BANK_OPTIONS).get(normalize_text(key).lower(), "Asaas")


def _empty_plan() -> dict[str, Any]:
    return {
        "banco": "asaas",
        "ciclo": "mensal",
        "forma": "cartao_recorrente",
        "servico": "",
        "valor": "",
        "vencimento": "",
        "asaas_customer_id": "",
        "asaas_subscription_id": "",
        "mercadopago_id": "",
    }


def _normalize_plan(raw: dict | None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    ciclo = normalize_text(data.get("ciclo")).lower() or "mensal"
    if ciclo not in _CYCLE_KEYS:
        ciclo = "mensal"
    forma = {
        "pix_automatico": "pix_recorrente",
        "cartao_anual": "cartao_avulso",
        "boleto": "boleto_recorrente",
    }.get(normalize_text(data.get("forma")).lower(), normalize_text(data.get("forma")).lower())
    banco = normalize_text(data.get("banco")).lower()
    if banco not in {"asaas", "mercado_pago"}:
        banco = "asaas"
    if forma not in _FORM_KEYS:
        forma = "cartao_recorrente"
    return {
        "banco": banco,
        "ciclo": ciclo,
        "forma": forma,
        "servico": normalize_text(data.get("servico")),
        "valor": normalize_text(data.get("valor")),
        "vencimento": normalize_text(data.get("vencimento"))[:10],
        "asaas_customer_id": normalize_text(data.get("asaas_customer_id")),
        "asaas_subscription_id": normalize_text(data.get("asaas_subscription_id")),
        "mercadopago_id": normalize_text(data.get("mercadopago_id")),
        "ciclo_label": cycle_label(ciclo),
        "forma_label": forma_label(forma),
        "banco_label": bank_label(banco),
    }


def load_billing_plan(tenant_id: str | None, sheet_row: int) -> dict[str, Any]:
    lead = get_lead_action(tenant_id, sheet_row) or {}
    stored = lead.get("billing_plan")
    if isinstance(stored, dict) and any(normalize_text(stored.get(key)) for key in ("ciclo", "forma", "servico", "valor")):
        return _normalize_plan(stored)
    closed = lead.get("closed_services")
    if isinstance(closed, list) and closed:
        first = closed[0] if isinstance(closed[0], dict) else {}
        forma_closed = normalize_text(first.get("forma_pagamento")).lower()
        ciclo = "mensal"
        forma = "pix"
        if "anual" in forma_closed:
            ciclo = "anual"
        elif "vista" in forma_closed or "avulso" in forma_closed:
            ciclo = "avulso"
        if "cartão" in forma_closed or "cartao" in forma_closed:
            forma = "cartao_avulso" if ciclo == "avulso" else "cartao_recorrente"
        elif "boleto" in forma_closed and "pix" in forma_closed:
            forma = "pix_boleto"
        elif "boleto" in forma_closed:
            forma = "boleto_recorrente"
        elif "pix" in forma_closed and "recorrente" in forma_closed:
            forma = "pix_recorrente"
        elif "pix" in forma_closed:
            forma = "pix"
        return _normalize_plan({
            "banco": (stored or {}).get("banco") if isinstance(stored, dict) else "",
            "ciclo": ciclo,
            "forma": forma,
            "servico": first.get("servico"),
            "valor": first.get("valor"),
            "vencimento": first.get("vencimento"),
            "asaas_customer_id": (stored or {}).get("asaas_customer_id") if isinstance(stored, dict) else "",
            "asaas_subscription_id": (stored or {}).get("asaas_subscription_id") if isinstance(stored, dict) else "",
            "mercadopago_id": (stored or {}).get("mercadopago_id") if isinstance(stored, dict) else "",
        })
    return _normalize_plan(_empty_plan())


def parse_billing_plan_from_form(form: Any, *, previous: dict | None = None) -> dict[str, Any]:
    prev = previous or {}
    return _normalize_plan({
        "banco": form.get("billing_banco"),
        "ciclo": form.get("billing_ciclo"),
        "forma": form.get("billing_forma"),
        "servico": form.get("billing_servico"),
        "valor": form.get("billing_valor"),
        "vencimento": form.get("billing_vencimento"),
        "asaas_customer_id": prev.get("asaas_customer_id"),
        "asaas_subscription_id": prev.get("asaas_subscription_id"),
        "mercadopago_id": prev.get("mercadopago_id"),
    })


def save_billing_plan(
    tenant_id: str | None,
    sheet_row: int,
    plan: dict,
    *,
    mirror_sheet: bool = True,
) -> dict[str, Any]:
    normalized = _normalize_plan(plan)
    payload = {
        "banco": normalized["banco"],
        "ciclo": normalized["ciclo"],
        "forma": normalized["forma"],
        "servico": normalized["servico"],
        "valor": normalized["valor"],
        "vencimento": normalized["vencimento"],
        "asaas_customer_id": normalized["asaas_customer_id"],
        "asaas_subscription_id": normalized["asaas_subscription_id"],
        "mercadopago_id": normalized["mercadopago_id"],
    }
    save_lead_action(tenant_id, sheet_row, {"billing_plan": payload}, mirror_sheet=mirror_sheet)
    return load_billing_plan(tenant_id, sheet_row)


def is_recurring_forma(forma: str) -> bool:
    return normalize_text(forma).lower() in {"cartao_recorrente", "boleto_recorrente", "pix_recorrente"}


def asaas_billing_type(forma: str) -> str:
    key = normalize_text(forma).lower()
    if key in {"pix", "pix_recorrente"}:
        return "PIX"
    if key in {"cartao_recorrente", "cartao_avulso"}:
        return "CREDIT_CARD"
    if key == "boleto_recorrente":
        return "BOLETO"
    return "UNDEFINED"


def asaas_subscription_fields(forma: str) -> dict[str, str]:
    """A assinatura Pix, boleto e cartão segue sem campo extra.

    pixAutomaticAuthorizationMode não entra: esta conta do Asaas recusa o valor
    e a fatura inteira deixa de ser criada.
    """
    normalize_text(forma)
    return {}


def asaas_cycle(ciclo: str) -> str | None:
    key = normalize_text(ciclo).lower()
    if key == "mensal":
        return "MONTHLY"
    if key == "anual":
        return "YEARLY"
    return None


def _same_email(left: str, right: str) -> bool:
    return bool(left and right and left.lower() == right.lower() and "@" in left)


def billing_email_from_values(values: dict) -> str:
    """E-mail de cobrança e login do responsável. O e-mail da Receita não entra."""
    company = normalize_text(values.get("email_empresa") or values.get("email") or "")
    for key in ("email_cobranca", "email_login_gestor", "email_socio_1"):
        candidate = normalize_text(values.get(key))
        if candidate and "@" in candidate and not _same_email(candidate, company):
            return candidate
    return ""


def _crm_row_from_values(values: dict) -> dict[str, Any]:
    return {
        "empresa": normalize_text(values.get("empresa")),
        "cnpj": normalize_cnpj_for_duplicate(values.get("cnpj") or ""),
        "telefone": normalize_text(values.get("telefone_b2b") or values.get("telefone_socio_1") or ""),
        "email": billing_email_from_values(values),
        "nome_contato": normalize_text(values.get("nome_contato") or values.get("responsavel_legal") or ""),
    }


def _customer_matches_crm(customer: dict, crm: dict) -> bool:
    """Só o CNPJ deste cadastro. Sem CNPJ, só o nome inteiro igual — nunca um trecho de outro cliente."""
    cnpj = normalize_cnpj_for_duplicate(customer.get("cpfCnpj") or "")
    crm_cnpj = normalize_cnpj_for_duplicate(crm.get("cnpj") or "")
    if crm_cnpj:
        return bool(cnpj) and cnpj == crm_cnpj
    name = normalize_text(customer.get("name")).lower()
    empresa = normalize_text(crm.get("empresa")).lower()
    return bool(name and empresa and name == empresa)


def find_asaas_customer_for_cadastro(
    values: dict,
    *,
    plan: dict | None = None,
    allow_lookup: bool = True,
) -> dict | None:
    stored_id = normalize_text((plan or {}).get("asaas_customer_id"))
    payload = fetch_dashboard_payload()
    customers = payload.get("customers") or []
    crm = _crm_row_from_values(values)
    if stored_id:
        for customer in customers:
            if normalize_text(customer.get("id")) == stored_id and _customer_matches_crm(customer, crm):
                return customer
    if allow_lookup and crm.get("cnpj"):
        try:
            hits = [
                item for item in (find_customers(cpfCnpj=crm["cnpj"]) or [])
                if _customer_matches_crm(item, crm)
            ]
            if hits:
                return hits[0]
        except AsaasError:
            pass
    for customer in customers:
        if _customer_matches_crm(customer, crm):
            return customer
    return None


def _map_asaas_payment(payment: dict) -> dict[str, Any]:
    classified = classify_payment(payment)
    due_raw = normalize_text(payment.get("dueDate"))[:10]
    due_label = "—"
    if due_raw:
        try:
            due_label = format_date_br(date.fromisoformat(due_raw))
        except ValueError:
            due_label = due_raw
    return {
        "fonte": "asaas",
        "asaas_payment_id": normalize_text(payment.get("id")),
        "data": due_raw,
        "data_display": due_label,
        "descricao": normalize_text(payment.get("description") or payment.get("invoiceNumber") or "Fatura Asaas"),
        "valor": format_brl(payment.get("value")),
        "status": classified["label"],
        "status_tone": classified["tone"],
        "forma_pagamento": {
            "PIX": "PIX",
            "BOLETO": "Boleto",
            "CREDIT_CARD": "Cartão",
            "UNDEFINED": "PIX / Boleto",
        }.get(normalize_text(payment.get("billingType")).upper(), normalize_text(payment.get("billingType")) or "—"),
        "invoice_url": payment.get("invoiceUrl") or payment.get("bankSlipUrl") or payment.get("transactionReceiptUrl") or "",
    }


def asaas_history_for_cadastro(values: dict, *, plan: dict | None = None) -> list[dict[str, Any]]:
    if not is_configured():
        return []
    try:
        customer = find_asaas_customer_for_cadastro(values, plan=plan, allow_lookup=False)
        if not customer:
            return []
        customer_id = normalize_text(customer.get("id"))
        payload = fetch_dashboard_payload()
        payments = [
            item for item in (payload.get("payments") or [])
            if normalize_text(item.get("customer")) == customer_id
        ]
        rows = [_map_asaas_payment(item) for item in payments]
        rows.sort(key=lambda item: item.get("data") or "", reverse=True)
        return rows
    except AsaasError:
        return []


def _digits_phone(value: str) -> str:
    digits = normalize_digits(value)
    if digits.startswith("55") and len(digits) in {12, 13}:
        digits = digits[2:]
    return digits


def ensure_asaas_customer(values: dict, *, plan: dict | None = None) -> dict:
    crm = _crm_row_from_values(values)
    existing = find_asaas_customer_for_cadastro(values, plan=plan)
    if existing:
        desired = normalize_text(crm.get("email"))
        current = normalize_text(existing.get("email"))
        customer_id = normalize_text(existing.get("id"))
        if desired and customer_id and desired.lower() != current.lower():
            updated = update_customer(customer_id, {"email": desired})
            if isinstance(updated, dict) and updated.get("id"):
                return updated
            return {**existing, "email": desired}
        return existing
    name = crm.get("empresa") or crm.get("nome_contato")
    if not name:
        raise AsaasError("Informe o nome da empresa no cadastro para criar o cliente no Asaas.")
    payload: dict[str, Any] = {"name": name}
    if crm.get("cnpj"):
        payload["cpfCnpj"] = crm["cnpj"]
    if crm.get("email"):
        payload["email"] = crm["email"]
    phone = _digits_phone(crm.get("telefone") or "")
    if phone:
        payload["mobilePhone"] = phone
    payload["externalReference"] = f"crm-{normalize_text(values.get('sheet_row') or '')}"
    return create_customer(payload)


def generate_asaas_invoice(
    tenant_id: str | None,
    sheet_row: int,
    values: dict,
    plan: dict,
) -> dict[str, Any]:
    if not is_configured():
        raise AsaasError("Asaas não está configurado. Defina ASAAS_API_KEY no Easypanel.")
    normalized = _normalize_plan(plan)
    amount = parse_money(normalized.get("valor"))
    if amount <= 0:
        raise AsaasError("Informe o valor do plano para gerar a fatura.")
    due = normalize_text(normalized.get("vencimento"))[:10]
    if len(due) != 10:
        due = (date.today() + timedelta(days=5)).isoformat()
    service = normalized.get("servico") or "Plano Oppi"
    description = f"{service} · {normalized['ciclo_label']} · {normalized['forma_label']}"
    customer = ensure_asaas_customer({**values, "sheet_row": sheet_row}, plan=normalized)
    customer_id = normalize_text(customer.get("id"))
    billing_type = asaas_billing_type(normalized["forma"])
    cycle = asaas_cycle(normalized["ciclo"]) if is_recurring_forma(normalized["forma"]) else None
    if normalized["forma"] == "pix_recorrente" and not cycle:
        raise AsaasError("Pix recorrente precisa da periodicidade Mensal ou Anual.")
    created: dict[str, Any]
    if cycle:
        created = create_subscription({
            "customer": customer_id,
            "billingType": billing_type,
            "cycle": cycle,
            "value": amount,
            "nextDueDate": due,
            "description": description,
            "externalReference": f"crm-{sheet_row}",
            **asaas_subscription_fields(normalized["forma"]),
        })
        normalized["asaas_subscription_id"] = normalize_text(created.get("id"))
    else:
        created = create_payment({
            "customer": customer_id,
            "billingType": billing_type,
            "value": amount,
            "dueDate": due,
            "description": description,
            "externalReference": f"crm-{sheet_row}",
        })
    normalized["asaas_customer_id"] = customer_id
    save_billing_plan(tenant_id, sheet_row, normalized, mirror_sheet=False)

    history = load_payment_history(tenant_id, sheet_row)
    payment_id = normalize_text(created.get("id"))
    already = {normalize_text(item.get("asaas_payment_id")) for item in history}
    if payment_id and payment_id not in already:
        history.append({
            "data": due,
            "descricao": description,
            "valor": format_brl(amount),
            "status": "Pendente",
            "forma_pagamento": normalized["forma_label"],
            "asaas_payment_id": payment_id,
            "invoice_url": created.get("invoiceUrl") or created.get("bankSlipUrl") or created.get("paymentLink") or "",
        })
        save_payment_history(tenant_id, sheet_row, history, mirror_sheet=False)

    invoice_url = created.get("invoiceUrl") or created.get("bankSlipUrl") or created.get("paymentLink") or ""
    return {
        "ok": True,
        "customer_id": customer_id,
        "created_id": payment_id,
        "invoice_url": invoice_url,
        "kind": "assinatura" if cycle else "fatura",
        "message": (
            f"{'Assinatura' if cycle else 'Fatura'} gerada no Asaas ({normalized['ciclo_label']} · {normalized['forma_label']})."
        ),
    }


def _mp_notification_url() -> str:
    return f"{settings.public_app_url}/webhooks/mercadopago"


def generate_mercadopago_invoice(
    tenant_id: str | None,
    sheet_row: int,
    values: dict,
    plan: dict,
) -> dict[str, Any]:
    from app.services.mercadopago_client import (
        MercadoPagoError,
        build_boleto_body,
        build_card_preference_body,
        build_pix_body,
        build_pix_boleto_preference_body,
        build_pix_subscription_body,
        charge_link,
        create_boleto,
        create_card_link,
        create_payment,
        create_pix_subscription,
        is_configured as mp_configured,
    )

    if not mp_configured():
        raise MercadoPagoError("Mercado Pago não está configurado. Defina MERCADO_PAGO_ACCESS_TOKEN no Easypanel.")
    normalized = _normalize_plan({**plan, "banco": "mercado_pago"})
    amount = parse_money(normalized.get("valor"))
    if amount <= 0:
        raise MercadoPagoError("Informe o valor do plano para gerar a cobrança.")
    due = normalize_text(normalized.get("vencimento"))[:10]
    if len(due) != 10:
        due = (date.today() + timedelta(days=5)).isoformat()
    email = billing_email_from_values(values)
    if not email:
        raise MercadoPagoError("Informe o e-mail de cobrança do responsável antes de gerar no Mercado Pago.")
    cnpj = normalize_cnpj_for_duplicate(values.get("cnpj") or "")
    empresa = normalize_text(values.get("empresa")) or "Cliente Oppi"
    service = normalized.get("servico") or "Plano Oppi"
    description = f"{service} · {normalized['ciclo_label']} · {normalized['forma_label']}"
    notice = _mp_notification_url()
    back = settings.public_app_url
    forma = normalized["forma"]
    ciclo = normalized["ciclo"]
    recurring = forma in {"cartao_recorrente", "pix_recorrente"} and ciclo in {"mensal", "anual"}
    if recurring:
        created = create_pix_subscription(build_pix_subscription_body(
            amount=amount,
            description=description,
            email=email,
            due=due,
            ciclo=ciclo,
            sheet_row=sheet_row,
            notification_url=notice,
            back_url=back,
        ))
        if forma == "pix_recorrente":
            kind = "Pix recorrente"
            message = (
                "Link do Pix recorrente gerado no Mercado Pago. "
                "O cliente autoriza o débito uma vez. Copie o link no histórico e envie no WhatsApp."
            )
        else:
            kind = "cartão recorrente"
            message = (
                "Link do cartão recorrente gerado no Mercado Pago. "
                "O cliente autoriza no link e as próximas cobranças saem sozinhas."
            )
    elif forma == "boleto_recorrente":
        if len(cnpj) != 14:
            raise MercadoPagoError("Informe o CNPJ da empresa para gerar o boleto.")
        created = create_boleto(build_boleto_body(
            amount=amount,
            description=description,
            due=due,
            email=email,
            empresa=empresa,
            cnpj=cnpj,
            sheet_row=sheet_row,
            notification_url=notice,
        ))
        kind = "boleto"
        message = "Boleto gerado no Mercado Pago. Copie o link no histórico e envie no WhatsApp."
    elif forma == "pix_recorrente":
        created = create_payment(build_pix_body(
            amount=amount,
            description=description,
            due=due,
            email=email,
            empresa=empresa,
            cnpj=cnpj,
            sheet_row=sheet_row,
            notification_url=notice,
        ))
        kind = "Pix"
        message = "Pix gerado no Mercado Pago. Copie o link no histórico e envie no WhatsApp."
    elif forma == "pix_boleto":
        created = create_card_link(build_pix_boleto_preference_body(
            amount=amount,
            description=description,
            email=email,
            sheet_row=sheet_row,
            notification_url=notice,
            back_url=back,
        ))
        kind = "Pix/Boleto"
        message = "Link de Pix ou boleto gerado no Mercado Pago. O cliente escolhe na hora. Copie e envie no WhatsApp."
    else:
        created = create_card_link(build_card_preference_body(
            amount=amount,
            description=description,
            email=email,
            sheet_row=sheet_row,
            notification_url=notice,
            back_url=back,
        ))
        kind = "cartão"
        message = "Link do cartão gerado no Mercado Pago. Copie no histórico e envie no WhatsApp."
    link = charge_link(created)
    resource_id = normalize_text(created.get("id"))
    normalized["mercadopago_id"] = resource_id
    save_billing_plan(tenant_id, sheet_row, normalized, mirror_sheet=False)
    history = load_payment_history(tenant_id, sheet_row)
    already = {normalize_text(item.get("mercadopago_id")) for item in history}
    if resource_id and resource_id not in already:
        history.append({
            "data": due,
            "descricao": description,
            "valor": format_brl(amount),
            "status": "Pendente",
            "forma_pagamento": normalized["forma_label"],
            "invoice_url": link,
            "fonte": "mercadopago",
            "mercadopago_id": resource_id,
        })
        save_payment_history(tenant_id, sheet_row, history, mirror_sheet=False)
    return {
        "ok": True,
        "created_id": resource_id,
        "invoice_url": link,
        "kind": kind,
        "message": message,
    }


def generate_customer_invoice(
    tenant_id: str | None,
    sheet_row: int,
    values: dict,
    plan: dict,
) -> dict[str, Any]:
    normalized = _normalize_plan(plan)
    if normalized["banco"] == "mercado_pago":
        return generate_mercadopago_invoice(tenant_id, sheet_row, values, normalized)
    return generate_asaas_invoice(tenant_id, sheet_row, values, normalized)


_MP_STATUS = {
    "approved": "Pago",
    "authorized": "Pendente",
    "pending": "Pendente",
    "in_process": "Pendente",
    "rejected": "Cancelado",
    "cancelled": "Cancelado",
    "canceled": "Cancelado",
    "refunded": "Cancelado",
    "charged_back": "Cancelado",
}


def apply_mercadopago_status(sheet_row: int, resource_id: str, status: str, invoice_url: str = "") -> None:
    """Atualiza o histórico local quando o Mercado Pago avisa uma cobrança. Não mexe no Oppi Ponto."""
    from app.services.lead_actions_storage import DEFAULT_TENANT_ID

    mapped = _MP_STATUS.get(normalize_text(status).lower())
    if not mapped or int(sheet_row) <= 0:
        return
    resource = normalize_text(resource_id)
    history = load_payment_history(DEFAULT_TENANT_ID, int(sheet_row))
    target = None
    for item in history:
        if resource and normalize_text(item.get("mercadopago_id")) == resource:
            target = item
            break
    if target is None:
        for item in reversed(history):
            if item.get("fonte") == "mercadopago" and item.get("status") == "Pendente":
                target = item
                break
    if target is None:
        return
    target["status"] = mapped
    if resource:
        target["mercadopago_id"] = resource
    if invoice_url:
        target["invoice_url"] = invoice_url
    target["fonte"] = "mercadopago"
    save_payment_history(DEFAULT_TENANT_ID, int(sheet_row), history, mirror_sheet=False)
