"""Plano contratado e geração de fatura Asaas no cadastro individual."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.services.asaas_client import (
    AsaasError,
    create_customer,
    create_payment,
    create_subscription,
    fetch_dashboard_payload,
    find_customers,
    is_configured,
    list_payments_for_customer,
)
from app.services.financeiro import classify_payment, format_brl, format_date_br
from app.services.lead_actions_storage import get_lead_action, save_lead_action
from app.services.legacy_core import (
    normalize_cnpj_for_duplicate,
    normalize_digits,
    normalize_text,
    parse_money,
    phones_match_for_duplicate,
)
from app.services.payment_history import load_payment_history, save_payment_history

PLAN_CYCLE_OPTIONS = [
    ("mensal", "Mensal"),
    ("anual", "Anual"),
    ("avulso", "Avulso"),
]
BILLING_FORM_OPTIONS = [
    ("pix", "PIX"),
    ("cartao_recorrente", "Cartão recorrente"),
    ("pix_boleto", "PIX / Boleto"),
    ("boleto_recorrente", "Boleto recorrente"),
]
_CYCLE_KEYS = {key for key, _ in PLAN_CYCLE_OPTIONS}
_FORM_KEYS = {key for key, _ in BILLING_FORM_OPTIONS}


def cycle_label(key: str) -> str:
    mapping = dict(PLAN_CYCLE_OPTIONS)
    return mapping.get(normalize_text(key).lower(), "—")


def forma_label(key: str) -> str:
    mapping = dict(BILLING_FORM_OPTIONS)
    return mapping.get(normalize_text(key).lower(), "—")


def _empty_plan() -> dict[str, Any]:
    return {
        "ciclo": "mensal",
        "forma": "pix",
        "servico": "",
        "valor": "",
        "vencimento": "",
        "asaas_customer_id": "",
        "asaas_subscription_id": "",
    }


def _normalize_plan(raw: dict | None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    ciclo = normalize_text(data.get("ciclo")).lower() or "mensal"
    if ciclo not in _CYCLE_KEYS:
        ciclo = "mensal"
    forma = normalize_text(data.get("forma")).lower() or "pix"
    if forma not in _FORM_KEYS:
        forma = "pix"
    return {
        "ciclo": ciclo,
        "forma": forma,
        "servico": normalize_text(data.get("servico")),
        "valor": normalize_text(data.get("valor")),
        "vencimento": normalize_text(data.get("vencimento"))[:10],
        "asaas_customer_id": normalize_text(data.get("asaas_customer_id")),
        "asaas_subscription_id": normalize_text(data.get("asaas_subscription_id")),
        "ciclo_label": cycle_label(ciclo),
        "forma_label": forma_label(forma),
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
            forma = "cartao_recorrente"
        elif "boleto" in forma_closed and "pix" in forma_closed:
            forma = "pix_boleto"
        elif "boleto" in forma_closed:
            forma = "boleto_recorrente"
        elif "pix" in forma_closed:
            forma = "pix"
        return _normalize_plan({
            "ciclo": ciclo,
            "forma": forma,
            "servico": first.get("servico"),
            "valor": first.get("valor"),
            "vencimento": first.get("vencimento"),
            "asaas_customer_id": (stored or {}).get("asaas_customer_id") if isinstance(stored, dict) else "",
            "asaas_subscription_id": (stored or {}).get("asaas_subscription_id") if isinstance(stored, dict) else "",
        })
    return _normalize_plan(_empty_plan())


def parse_billing_plan_from_form(form: Any, *, previous: dict | None = None) -> dict[str, Any]:
    prev = previous or {}
    return _normalize_plan({
        "ciclo": form.get("billing_ciclo"),
        "forma": form.get("billing_forma"),
        "servico": form.get("billing_servico"),
        "valor": form.get("billing_valor"),
        "vencimento": form.get("billing_vencimento"),
        "asaas_customer_id": prev.get("asaas_customer_id"),
        "asaas_subscription_id": prev.get("asaas_subscription_id"),
    })


def save_billing_plan(tenant_id: str | None, sheet_row: int, plan: dict) -> dict[str, Any]:
    normalized = _normalize_plan(plan)
    payload = {
        "ciclo": normalized["ciclo"],
        "forma": normalized["forma"],
        "servico": normalized["servico"],
        "valor": normalized["valor"],
        "vencimento": normalized["vencimento"],
        "asaas_customer_id": normalized["asaas_customer_id"],
        "asaas_subscription_id": normalized["asaas_subscription_id"],
    }
    save_lead_action(tenant_id, sheet_row, {"billing_plan": payload})
    return load_billing_plan(tenant_id, sheet_row)


def asaas_billing_type(forma: str) -> str:
    key = normalize_text(forma).lower()
    if key == "pix":
        return "PIX"
    if key == "cartao_recorrente":
        return "CREDIT_CARD"
    if key == "boleto_recorrente":
        return "BOLETO"
    return "UNDEFINED"


def asaas_cycle(ciclo: str) -> str | None:
    key = normalize_text(ciclo).lower()
    if key == "mensal":
        return "MONTHLY"
    if key == "anual":
        return "YEARLY"
    return None


def _crm_row_from_values(values: dict) -> dict[str, Any]:
    return {
        "empresa": normalize_text(values.get("empresa")),
        "cnpj": normalize_cnpj_for_duplicate(values.get("cnpj") or ""),
        "telefone": normalize_text(values.get("telefone_b2b") or values.get("telefone_socio_1") or ""),
        "email": normalize_text(values.get("email_empresa") or values.get("email") or ""),
        "nome_contato": normalize_text(values.get("nome_contato") or ""),
    }


def _customer_matches_crm(customer: dict, crm: dict) -> bool:
    cnpj = normalize_cnpj_for_duplicate(customer.get("cpfCnpj") or "")
    if cnpj and crm.get("cnpj") and cnpj == crm["cnpj"]:
        return True
    for phone in (customer.get("mobilePhone"), customer.get("phone")):
        if phones_match_for_duplicate(crm.get("telefone"), phone or ""):
            return True
    name = normalize_text(customer.get("name")).lower()
    empresa = normalize_text(crm.get("empresa")).lower()
    if name and empresa and (name == empresa or name in empresa or empresa in name):
        return True
    return False


def find_asaas_customer_for_cadastro(
    values: dict,
    *,
    plan: dict | None = None,
    allow_lookup: bool = True,
) -> dict | None:
    stored_id = normalize_text((plan or {}).get("asaas_customer_id"))
    payload = fetch_dashboard_payload()
    customers = payload.get("customers") or []
    if stored_id:
        for customer in customers:
            if normalize_text(customer.get("id")) == stored_id:
                return customer
    crm = _crm_row_from_values(values)
    if allow_lookup and crm.get("cnpj"):
        try:
            hits = find_customers(cpfCnpj=crm["cnpj"])
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
    existing = find_asaas_customer_for_cadastro(values, plan=plan)
    if existing:
        return existing
    crm = _crm_row_from_values(values)
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
    cycle = asaas_cycle(normalized["ciclo"])
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
    save_billing_plan(tenant_id, sheet_row, normalized)

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
        })
        save_payment_history(tenant_id, sheet_row, history)

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
