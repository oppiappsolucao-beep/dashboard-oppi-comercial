"""Painel Financeiro — Asaas + vínculo com cadastros do CRM."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.services.asaas_client import (
    AsaasError,
    fetch_account_balance,
    fetch_dashboard_payload,
    fetch_payments_due,
    fetch_payments_received,
    fetch_statement,
    is_configured,
)
from app.services.company_payables import list_payables, list_suppliers, payable_calendar
from app.services.internal_finance import build_internal_forecast, resolve_period
from app.services.legacy_core import (
    normalize_cnpj_for_duplicate,
    normalize_phone_for_duplicate,
    normalize_search_text,
    normalize_text,
    parse_money,
    phones_match_for_duplicate,
)

logger = logging.getLogger(__name__)
_TZ = ZoneInfo("America/Sao_Paulo")

PAID_STATUSES = {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}
PENDING_STATUSES = {"PENDING", "AWAITING_RISK_ANALYSIS"}
OVERDUE_STATUSES = {"OVERDUE"}
CANCELLED_STATUSES = {"REFUNDED", "REFUND_REQUESTED", "DELETED"}
# Cobranças que entram no saldo do mês: boleto, PIX e cartão (e PIX/Boleto avulso).
ENTRY_BILLING_TYPES = {"BOLETO", "PIX", "CREDIT_CARD", "DEBIT_CARD", "UNDEFINED"}

TAB_VISAO = "visao"
TAB_FATURAS = "faturas"
TAB_RECORRENCIAS = "recorrencias"
TAB_ATRASO = "atraso"
TAB_ENTRADAS = "entradas"
TAB_PAGAR = "pagar"

_MESES = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)

ENTRADA_TIPOS = {
    "PAYMENT_RECEIVED": "Cobrança recebida",
    "PIX_TRANSACTION_CREDIT": "Pix recebido",
    "TRANSFER": "Transferência recebida",
    "TRANSFER_REVERSAL": "Estorno de transferência",
    "PAYMENT_REFUND_CANCELLED": "Cancelamento de estorno",
    "RECEIVABLE_ANTICIPATION_GROSS_CREDIT": "Antecipação de recebíveis",
    "RECEIVABLE_ANTICIPATION_CREDIT": "Antecipação",
    "PAYMENT_CUSTODY_BLOCK_REVERSAL": "Liberação de saldo",
    "INTERNAL_TRANSFER_CREDIT": "Transferência interna",
    "PAYMENT_FEE_REVERSAL": "Estorno de tarifa",
    "CHARGEBACK_REVERSAL": "Estorno de chargeback",
    "PROMOTIONAL_CODE_CREDIT": "Crédito promocional",
}


def _today() -> date:
    return datetime.now(_TZ).date()


def _parse_date(value: str | None) -> date | None:
    raw = normalize_text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def format_brl(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def format_date_br(value: date | None) -> str:
    if not value:
        return "—"
    return value.strftime("%d/%m/%Y")


def billing_label(billing_type: str, *, has_subscription: bool = False) -> str:
    kind = normalize_text(billing_type).upper()
    if kind == "BOLETO":
        return "Boleto"
    if kind == "PIX":
        return "PIX"
    if kind in {"CREDIT_CARD", "DEBIT_CARD"}:
        return "Cartão recorrente" if has_subscription else "Cartão"
    if kind == "TRANSFER":
        return "Transferência"
    if kind == "UNDEFINED":
        return "PIX/Boleto"
    return kind or "—"


def cycle_label(cycle: str) -> str:
    mapping = {
        "WEEKLY": "Semanal",
        "BIWEEKLY": "Quinzenal",
        "MONTHLY": "Mensal",
        "QUARTERLY": "Trimestral",
        "SEMIANNUALLY": "Semestral",
        "YEARLY": "Anual",
    }
    return mapping.get(normalize_text(cycle).upper(), normalize_text(cycle) or "—")


def classify_payment(payment: dict, *, today: date | None = None) -> dict[str, str]:
    """Status operacional + cor do badge."""
    today = today or _today()
    status = normalize_text(payment.get("status")).upper()
    due = _parse_date(payment.get("dueDate"))
    has_sub = bool(payment.get("subscription"))
    if status in PAID_STATUSES:
        return {"key": "pago", "label": "Pago", "tone": "green"}
    if status in CANCELLED_STATUSES:
        return {"key": "cancelado", "label": "Cancelado", "tone": "muted"}
    if status in OVERDUE_STATUSES or (status in PENDING_STATUSES and due and due < today):
        return {"key": "atrasado", "label": "Atrasado", "tone": "red"}
    if status in PENDING_STATUSES and due == today:
        return {"key": "vence_hoje", "label": "Vence hoje", "tone": "yellow"}
    if has_sub and status in PENDING_STATUSES:
        return {"key": "recorrente", "label": "Recorrente", "tone": "purple"}
    if status in PENDING_STATUSES:
        return {"key": "receber", "label": "A vencer", "tone": "blue"}
    return {"key": "outro", "label": status or "—", "tone": "muted"}


def _service_name(item: dict, cliente: str = "") -> str:
    description = normalize_text(item.get("description") or item.get("externalReference"))
    if not description:
        return "Cobrança Asaas"
    line = description.split("\n", 1)[0].strip()
    client = normalize_text(cliente)
    if client:
        lower_line = line.lower()
        lower_client = client.lower()
        if lower_line.endswith(lower_client):
            line = line[: len(line) - len(client)].rstrip(" —–-")
        elif f"— {lower_client}" in lower_line:
            line = line[: lower_line.find(f"— {lower_client}")].rstrip(" —–-")
    return (line or "Oppi RH")[:60]


def _load_crm_rows() -> list[dict[str, Any]]:
    try:
        from database.connection import SessionLocal
        from database.models import CrmRegistration
        from app.services.crm_registrations_storage import DEFAULT_TENANT_ID

        db = SessionLocal()
        try:
            rows = (
                db.query(CrmRegistration)
                .filter(CrmRegistration.tenant_id == DEFAULT_TENANT_ID)
                .all()
            )
            out = []
            for row in rows:
                out.append({
                    "sheet_row": int(row.sheet_row) if row.sheet_row else None,
                    "empresa": normalize_text(row.empresa),
                    "cnpj": normalize_cnpj_for_duplicate(row.cnpj),
                    "telefone": normalize_text(row.telefone_b2b or row.telefone_socio_1 or ""),
                    "nome_contato": normalize_text(getattr(row, "nome_contato", "") or ""),
                })
            return out
        finally:
            db.close()
    except Exception:
        logger.exception("Falha ao indexar CRM para o Financeiro")
        return []


def _match_crm(customer: dict | None, crm_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not customer:
        return None
    cnpj = normalize_cnpj_for_duplicate(customer.get("cpfCnpj") or "")
    if cnpj:
        for row in crm_rows:
            if row.get("cnpj") and row["cnpj"] == cnpj:
                return row
    phones = [
        customer.get("mobilePhone"),
        customer.get("phone"),
    ]
    for phone in phones:
        target = normalize_phone_for_duplicate(phone or "")
        if not target:
            continue
        for row in crm_rows:
            if phones_match_for_duplicate(row.get("telefone"), target):
                return row
    name = normalize_text(customer.get("name")).lower()
    if name:
        for row in crm_rows:
            empresa = normalize_text(row.get("empresa")).lower()
            if empresa and (empresa == name or name in empresa or empresa in name):
                return row
    return None


def month_label(start: date, end: date) -> str:
    if start.year == end.year and start.month == end.month and start.day == 1:
        last = (end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if end == last:
            return f"{_MESES[start.month]} {start.year}"
    return f"{format_date_br(start)} a {format_date_br(end)}"


def entrada_type_label(kind: str) -> str:
    key = normalize_text(kind).upper()
    if key in ENTRADA_TIPOS:
        return ENTRADA_TIPOS[key]
    if not key:
        return "Entrada"
    return key.replace("_", " ").capitalize()


def map_entradas(
    transactions: list[dict] | None,
    payment_index: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """Créditos do extrato Asaas — o que entrou na conta e aparece no banco."""
    payment_index = payment_index or {}
    rows = []
    for item in transactions or []:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        when = _parse_date(item.get("date"))
        payment_id = normalize_text(item.get("paymentId"))
        linked = payment_index.get(payment_id) if payment_id else None
        tipo = entrada_type_label(item.get("type") or "")
        description = normalize_text(item.get("description"))
        if linked:
            cliente = normalize_text(linked.get("cliente"))
            servico = normalize_text(linked.get("servico"))
            description = " — ".join(part for part in (cliente, servico) if part) or description
        rows.append(
            {
                "id": normalize_text(item.get("id")),
                "date": when,
                "date_label": format_date_br(when),
                "description": description or tipo,
                "tipo": tipo,
                "valor": value,
                "valor_label": format_brl(value),
                "payment_id": payment_id,
            }
        )
    rows.sort(key=lambda row: row.get("date") or date.min, reverse=True)
    return rows


def _is_boleto_payment(payment: dict) -> bool:
    """Boleto puro, boleto/PIX (UNDEFINED) ou cobrança que já tem link de boleto."""
    kind = normalize_text(payment.get("billingType")).upper()
    if kind in {"BOLETO", "UNDEFINED"}:
        return True
    return bool(normalize_text(payment.get("bankSlipUrl")))


def _boleto_row(payment: dict, today: date) -> dict[str, Any] | None:
    if not _is_boleto_payment(payment):
        return None
    classified = classify_payment(payment, today=today)
    if classified["key"] == "cancelado":
        return None
    try:
        value = float(payment.get("value") or 0)
    except (TypeError, ValueError):
        value = 0.0
    due = _parse_date(payment.get("dueDate"))
    return {
        "cliente": _service_name(payment),
        "valor": value,
        "valor_label": format_brl(value),
        "vencimento": due,
        "vencimento_label": format_date_br(due),
        "status_key": classified["key"],
        "status_label": classified["label"],
        "status_tone": classified["tone"],
        "invoice_url": payment.get("invoiceUrl") or payment.get("bankSlipUrl") or "",
    }


def summarize_boletos(hoje_raw: list[dict] | None, mes_raw: list[dict] | None, today: date) -> dict[str, Any]:
    """Conta boletos do Asaas que vencem hoje e os que vencem no mês."""
    hoje = [row for row in (_boleto_row(item, today) for item in (hoje_raw or []) if isinstance(item, dict)) if row]
    mes = [row for row in (_boleto_row(item, today) for item in (mes_raw or []) if isinstance(item, dict)) if row]
    hoje.sort(key=lambda row: row["valor"], reverse=True)
    mes.sort(key=lambda row: (row.get("vencimento") or date.min, row["cliente"]))
    pagos = [row for row in mes if row["status_key"] == "pago"]
    abertos = [row for row in mes if row["status_key"] != "pago"]

    def _sum(rows: list[dict]) -> float:
        return sum(float(row["valor"]) for row in rows)

    hoje_total = _sum(hoje)
    mes_total = _sum(mes)
    pagos_total = _sum(pagos)
    abertos_total = _sum(abertos)
    return {
        "error": "",
        "month_label": f"{_MESES[today.month]} {today.year}",
        "hoje_n": len(hoje),
        "hoje_valor_label": format_brl(hoje_total),
        "hoje": hoje,
        "mes_n": len(mes),
        "mes_valor_label": format_brl(mes_total),
        "mes_pagos_n": len(pagos),
        "mes_pagos_label": format_brl(pagos_total),
        "mes_abertos_n": len(abertos),
        "mes_abertos_label": format_brl(abertos_total),
        "mes": mes,
        "entraram": [],
        "entraram_n": 0,
        "entraram_valor_label": format_brl(0),
        "entradas_n": 0,
        "entradas_valor_label": format_brl(0),
        "entradas_outras_n": 0,
        "entradas_outras_label": format_brl(0),
        "entradas_outras": [],
        "entradas_conta": [],
        "entradas_conta_n": 0,
        "entradas_conta_label": format_brl(0),
        "saldo_conta_label": "",
        "entraram_aviso": "",
    }


def _payments_due_on(payments: list[dict] | None, start: date, end: date) -> list[dict]:
    """O Asaas às vezes devolve vazio quando o vencimento inicial e o final são o mesmo dia."""
    rows = []
    for payment in payments or []:
        if not isinstance(payment, dict):
            continue
        due = _parse_date(payment.get("dueDate"))
        if due and start <= due <= end:
            rows.append(payment)
    return rows


def received_entries(payments: list[dict] | None, start: date, end: date, today: date) -> list[dict[str, Any]]:
    """Tudo que foi pago no período, pela data em que entrou."""
    rows = []
    for payment in payments or []:
        if not isinstance(payment, dict):
            continue
        classified = classify_payment(payment, today=today)
        if classified["key"] != "pago":
            continue
        paid_on = _parse_date(
            payment.get("paymentDate") or payment.get("clientPaymentDate") or payment.get("confirmedDate")
        )
        if not paid_on or paid_on < start or paid_on > end:
            continue
        try:
            value = float(payment.get("value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        billing = normalize_text(payment.get("billingType")).upper()
        due = _parse_date(payment.get("dueDate"))
        rows.append(
            {
                "cliente": _service_name(payment),
                "valor": value,
                "valor_label": format_brl(value),
                "pago_em": paid_on,
                "pago_label": format_date_br(paid_on),
                "vencimento_label": format_date_br(due),
                "billing_type": billing,
                "forma": billing_label(billing),
                "status_label": classified["label"],
                "status_tone": classified["tone"],
                "invoice_url": payment.get("invoiceUrl") or payment.get("bankSlipUrl") or "",
            }
        )
    rows.sort(key=lambda row: (row.get("pago_em") or date.min, row["cliente"]), reverse=True)
    return rows


def load_boleto_entrada(today: date, start: date, end: date, *, force: bool = False) -> dict[str, Any]:
    blank = summarize_boletos([], [], today)
    blank["month_label"] = month_label(start, end)
    if not is_configured():
        blank["error"] = "Configure ASAAS_API_KEY para ler os boletos."
        return blank
    try:
        hoje_raw = _payments_due_on(
            fetch_payments_due(today, today + timedelta(days=1), force=force),
            today,
            today,
        )
        mes_end = end if end > start else end + timedelta(days=1)
        mes_raw = fetch_payments_due(start, mes_end, force=force)
        if start == end:
            mes_raw = _payments_due_on(mes_raw, start, end)
    except AsaasError as exc:
        blank["error"] = str(exc)
        return blank
    except Exception:
        logger.exception("Falha ao ler boletos do Asaas")
        blank["error"] = "Não foi possível ler os boletos do Asaas agora."
        return blank
    result = summarize_boletos(hoje_raw, mes_raw, today)
    result["month_label"] = month_label(start, end)
    return _attach_received(result, start, end, today, force=force)


def _attach_received(result: dict[str, Any], start: date, end: date, today: date, *, force: bool) -> dict[str, Any]:
    try:
        raw, truncated = fetch_payments_received(start, end, force=force)
    except AsaasError as exc:
        result["entraram_aviso"] = str(exc)
        return result
    except Exception:
        logger.exception("Falha ao validar entradas do Asaas")
        result["entraram_aviso"] = "Não foi possível validar os boletos que entraram."
        return result
    rows = received_entries(raw, start, end, today)
    boletos = [row for row in rows if row.get("billing_type") == "BOLETO"]
    outras = [row for row in rows if row.get("billing_type") != "BOLETO"]

    def _sum(items: list[dict]) -> float:
        return sum(float(item.get("valor") or 0) for item in items)

    result["entraram"] = boletos
    result["entraram_n"] = len(boletos)
    result["entraram_valor_label"] = format_brl(_sum(boletos))
    result["entradas_n"] = len(rows)
    result["entradas_valor_label"] = format_brl(_sum(rows))
    result["entradas_outras_n"] = len(outras)
    result["entradas_outras_label"] = format_brl(_sum(outras))
    result["entradas_outras"] = outras
    result["entradas_conta"] = rows
    result["entradas_conta_n"] = len(rows)
    result["entradas_conta_label"] = format_brl(_sum(rows))
    result["saldo_conta_label"] = _account_balance_label(force=force)
    if truncated:
        result["entraram_aviso"] = "A consulta passou do limite. O total pode estar incompleto."
    return result


def _account_balance_label(*, force: bool) -> str:
    """Saldo atual da conta Asaas. Vazio se a consulta não responder."""
    try:
        balance = fetch_account_balance(force=force)
    except AsaasError as exc:
        logger.warning("Saldo Asaas indisponível: %s", exc)
        return ""
    except Exception:
        logger.exception("Falha ao ler saldo Asaas")
        return ""
    if balance is None:
        return ""
    return format_brl(balance)


def apply_boleto_status(boletos: dict[str, Any], status: str) -> dict[str, Any]:
    """Filtra os boletos do período pelo status escolhido."""
    key = normalize_text(status).lower()
    aliases = {
        "a_vencer": "receber",
        "avencer": "receber",
        "vencehoje": "vence_hoje",
    }
    key = aliases.get(key, key)
    out = dict(boletos)
    out["status"] = key
    if not key:
        return out
    rows = [row for row in (boletos.get("mes") or []) if row.get("status_key") == key]
    pagos = [row for row in rows if row.get("status_key") == "pago"]
    abertos = [row for row in rows if row.get("status_key") != "pago"]

    def _sum(items: list[dict]) -> float:
        return sum(float(item.get("valor") or 0) for item in items)

    out["mes"] = rows
    out["mes_n"] = len(rows)
    out["mes_valor_label"] = format_brl(_sum(rows))
    out["mes_pagos_n"] = len(pagos)
    out["mes_pagos_label"] = format_brl(_sum(pagos))
    out["mes_abertos_n"] = len(abertos)
    out["mes_abertos_label"] = format_brl(_sum(abertos))
    return out


def _payables_view(start: date, end: date, search: str, status: str = "", today: date | None = None) -> dict[str, Any]:
    today = today or _today()
    rows = list_payables(start, end, today)
    atrasados = [row for row in rows if row.get("status") == "atrasado"]
    atraso_total = sum(float(row["amount"]) for row in atrasados)
    saida = sum(float(row["amount"]) for row in rows)
    periodo_a_pagar = sum(float(row["amount"]) for row in rows if not row.get("paid"))
    periodo_pago = sum(float(row["amount"]) for row in rows if row.get("paid"))
    needle = normalize_text(search).lower()
    if needle:
        rows = [
            row for row in rows
            if needle in f"{row['description']} {row.get('supplier') or ''} {row.get('category_label') or ''}".lower()
        ]
    status_key = normalize_text(status).lower()
    if status_key in {"pago", "atrasado", "a_pagar"}:
        rows = [row for row in rows if row.get("status") == status_key]
    calendar = payable_calendar(rows, start, end)
    listed = []
    for row in rows:
        listed.append(
            {
                **row,
                "valor_label": format_brl(row["amount"]),
                "due_label": format_date_br(row["due"]),
                "paid_label": format_date_br(row["paid_on"]) if row.get("paid_on") else "",
                "status_label": {"pago": "Pago", "atrasado": "Atrasado", "a_pagar": "A pagar"}.get(row.get("status"), "A pagar"),
                "status_tone": {"pago": "green", "atrasado": "red", "a_pagar": "blue"}.get(row.get("status"), "blue"),
            }
        )
    dividas = round(saida, 2)
    return {
        "rows": listed,
        "days": [
            {
                **day,
                "a_pagar_label": format_brl(day["a_pagar"]) if day["a_pagar"] else "",
                "pago_label": format_brl(day["pago"]) if day["pago"] else "",
                "items": [
                    {
                        **item,
                        "valor_label": format_brl(item["amount"]),
                        "status_label": {"pago": "Pago", "atrasado": "Atrasado", "a_pagar": "A pagar"}.get(item.get("status"), "A pagar"),
                        "status_tone": {"pago": "green", "atrasado": "red", "a_pagar": "blue"}.get(item.get("status"), "blue"),
                    }
                    for item in day["items"]
                ],
            }
            for day in calendar["days"]
        ],
        "show_grid": calendar["show_grid"],
        "a_pagar": periodo_a_pagar,
        "pago": periodo_pago,
        "aberto": periodo_a_pagar,
        "a_pagar_label": format_brl(periodo_a_pagar),
        "pago_label": format_brl(periodo_pago),
        "aberto_label": format_brl(periodo_a_pagar),
        "lista_a_pagar_label": format_brl(sum(float(row["amount"]) for row in rows if not row.get("paid"))),
        "lista_pago_label": format_brl(sum(float(row["amount"]) for row in rows if row.get("paid"))),
        "atrasado_n": len(atrasados),
        "atrasado_label": format_brl(atraso_total),
        "saida": saida,
        "saida_label": format_brl(saida),
        "entrada": 0.0,
        "entrada_label": format_brl(0),
        "a_receber": 0.0,
        "a_receber_label": format_brl(0),
        "dividas": dividas,
        "dividas_label": format_brl(dividas),
        "saldo": 0.0,
        "saldo_label": format_brl(0),
        "compare_note": "",
        "suppliers": list_suppliers(),
        "month_label": month_label(start, end),
    }


def attach_cash_compare(payables: dict[str, Any], start: date, end: date, today: date, *, force: bool = False) -> dict[str, Any]:
    """Compara o que entrou no Asaas com a saída das contas do período."""
    saida = float(payables.get("saida") or 0)
    entrada = 0.0
    a_receber = 0.0
    note = ""
    if not is_configured():
        note = "Asaas não configurado para ler a entrada."
    else:
        try:
            raw, _truncated = fetch_payments_received(start, end, force=force)
            entrada = sum(float(row["valor"]) for row in received_entries(raw, start, end, today))
        except AsaasError as exc:
            note = str(exc)
        except Exception:
            logger.exception("Falha ao comparar entrada e saída")
            note = "Não foi possível ler as entradas do Asaas."
        try:
            due_raw = fetch_payments_due(start, end, billing_type="BOLETO", force=force)
            abertos = [
                row for row in (summarize_boletos([], due_raw, today).get("mes") or [])
                if row.get("status_key") != "pago"
            ]
            a_receber = sum(float(row["valor"]) for row in abertos)
        except AsaasError as exc:
            note = note or str(exc)
        except Exception:
            logger.exception("Falha ao ler o valor a receber")
            note = note or "Não foi possível ler o valor a receber."
    saldo = entrada - saida
    payables["entrada"] = entrada
    payables["entrada_label"] = format_brl(entrada)
    payables["a_receber"] = a_receber
    payables["a_receber_label"] = format_brl(a_receber)
    payables["saldo"] = saldo
    payables["saldo_label"] = format_brl(saldo)
    payables["compare_note"] = note
    return payables


def _wa_link(phone: str, text: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits and not digits.startswith("55") and len(digits) >= 10:
        digits = "55" + digits
    if not digits:
        return ""
    return f"https://wa.me/{digits}?text={quote(text)}"


def _map_invoice(payment: dict, customers: dict[str, dict], crm_rows: list[dict], today: date) -> dict[str, Any]:
    customer = customers.get(normalize_text(payment.get("customer"))) or {}
    crm = _match_crm(customer, crm_rows)
    classified = classify_payment(payment, today=today)
    due = _parse_date(payment.get("dueDate"))
    entered = _parse_date(payment.get("dateCreated"))
    value = float(payment.get("value") or 0)
    cliente = (
        (crm or {}).get("empresa")
        or normalize_text(customer.get("name"))
        or "Cliente Asaas"
    )
    phone = (crm or {}).get("telefone") or customer.get("mobilePhone") or customer.get("phone") or ""
    service = _service_name(payment, cliente)
    due_label = format_date_br(due)
    charge_text = (
        f"Olá! Aqui é da Oppi. Identificamos a fatura de {service} "
        f"({cliente}) no valor de {format_brl(value)}, vencimento {due_label}. "
        f"Pode nos ajudar a regularizar?"
    )
    sheet_row = (crm or {}).get("sheet_row")
    return {
        "id": payment.get("id") or "",
        "cliente": cliente,
        "servico": service,
        "valor": value,
        "valor_label": format_brl(value),
        "vencimento": due,
        "vencimento_label": due_label,
        "entrada": entered,
        "entrada_label": format_date_br(entered),
        "forma": billing_label(payment.get("billingType") or "", has_subscription=bool(payment.get("subscription"))),
        "billing_type": normalize_text(payment.get("billingType")).upper(),
        "status_key": classified["key"],
        "status_label": classified["label"],
        "status_tone": classified["tone"],
        "invoice_url": payment.get("invoiceUrl") or payment.get("bankSlipUrl") or "",
        "sheet_row": sheet_row,
        "cliente_href": f"/cadastro/todos/{sheet_row}/editar" if sheet_row else "",
        "whatsapp_url": _wa_link(str(phone), charge_text),
        "has_subscription": bool(payment.get("subscription")),
        "payment_date": _parse_date(payment.get("paymentDate") or payment.get("confirmedDate")),
    }


def _map_subscription(item: dict, customers: dict[str, dict], crm_rows: list[dict]) -> dict[str, Any]:
    customer = customers.get(normalize_text(item.get("customer"))) or {}
    crm = _match_crm(customer, crm_rows)
    status = normalize_text(item.get("status")).upper()
    active = status == "ACTIVE"
    next_due = _parse_date(item.get("nextDueDate"))
    sheet_row = (crm or {}).get("sheet_row")
    return {
        "id": item.get("id") or "",
        "cliente": (crm or {}).get("empresa") or normalize_text(customer.get("name")) or "Cliente Asaas",
        "servico": _service_name(item, (crm or {}).get("empresa") or normalize_text(customer.get("name"))),
        "valor": float(item.get("value") or 0),
        "valor_label": format_brl(item.get("value")),
        "ciclo": cycle_label(item.get("cycle") or ""),
        "proximo_vencimento": next_due,
        "proximo_label": format_date_br(next_due),
        "forma": billing_label(item.get("billingType") or "", has_subscription=True),
        "status_label": "Ativa" if active else (status.title() or "Inativa"),
        "status_tone": "green" if active else "muted",
        "ativa": active,
        "sheet_row": sheet_row,
        "cliente_href": f"/cadastro/todos/{sheet_row}/editar" if sheet_row else "",
    }


def charges_entered_in_period(rows: list[dict], start: date, end: date) -> list[dict]:
    """Boleto, PIX e cartão gerados no período. Cancelados ficam de fora do saldo."""
    out = []
    for row in rows:
        if row.get("billing_type") not in ENTRY_BILLING_TYPES:
            continue
        if row.get("status_key") == "cancelado":
            continue
        entered = row.get("entrada")
        if not entered or entered < start or entered > end:
            continue
        out.append(row)
    out.sort(key=lambda row: row.get("entrada") or date.min, reverse=True)
    return out


def _filter_invoices(rows: list[dict], params: dict) -> list[dict]:
    status = normalize_text(params.get("status")).lower()
    forma = normalize_text(params.get("forma")).lower()
    search = normalize_text(params.get("search")).lower()
    start = _parse_date(params.get("period_start"))
    end = _parse_date(params.get("period_end"))
    out = []
    for row in rows:
        key = row["status_key"]
        billing = row["billing_type"]
        if status in {"receber", "a receber"} and key not in {"receber", "vence_hoje", "recorrente"}:
            continue
        if status in {"atrasados", "atrasado"} and key != "atrasado":
            continue
        if status in {"pagos", "pago"} and key != "pago":
            continue
        if status in {"cancelados", "cancelado"} and key != "cancelado":
            continue
        if status in {"cartao_recorrente", "cartão recorrente"} and not (
            row["has_subscription"] and billing in {"CREDIT_CARD", "DEBIT_CARD"}
        ):
            continue
        if status == "pix" and billing != "PIX":
            continue
        if status == "boleto" and billing != "BOLETO":
            continue
        if forma == "pix" and billing != "PIX":
            continue
        if forma == "boleto" and billing != "BOLETO":
            continue
        if forma in {"cartao", "cartão", "cartao_recorrente"} and billing not in {"CREDIT_CARD", "DEBIT_CARD"}:
            continue
        due = row.get("vencimento")
        if start or end:
            if not due:
                continue
            if start and due < start:
                continue
            if end and due > end:
                continue
        if search:
            blob = f"{row['cliente']} {row['servico']} {row['id']}".lower()
            if search not in blob:
                continue
        out.append(row)
    return out


_DATE_FULL = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")
_DATE_DM = re.compile(r"^(\d{1,2})[/-](\d{1,2})$")
_DAY_ONLY = re.compile(r"^(?:dia\s*)?(\d{1,2})$")


def parse_sheet_due(value: str, *, today: date) -> tuple[date | None, int | None]:
    """Data cheia da planilha, ou só o dia quando a célula não tem mês e ano."""
    raw = normalize_text(value)
    if not raw:
        return None, None
    head = raw.split()[0]
    parsed = _parse_date(head)
    if parsed:
        return parsed, None
    full = _DATE_FULL.match(head)
    if full:
        day, month, year = (int(part) for part in full.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day), None
        except ValueError:
            return None, None
    short = _DATE_DM.match(head)
    if short:
        day, month = (int(part) for part in short.groups())
        try:
            return date(today.year, month, day), None
        except ValueError:
            return None, None
    day_only = _DAY_ONLY.match(normalize_search_text(raw))
    if day_only:
        day = int(day_only.group(1))
        if 1 <= day <= 31:
            return None, day
    return None, None


def classify_raissa_entry(status: str, due: date | None, today: date) -> dict[str, str]:
    """Pago, vencido ou a entrar a partir do texto e da data da base Raissa."""
    text = normalize_search_text(status)
    if any(token in text for token in ("cancel", "inativ", "distrat", "encerr", "churn")):
        return {"key": "fora", "label": "Fora", "tone": "muted"}
    if "nao pago" in text or "em aberto" in text:
        paid = False
    else:
        paid = any(token in text for token in ("pago", "recebid", "quitad", "confirmad", "liquidad"))
    if paid:
        return {"key": "pago", "label": "Pago", "tone": "green"}
    if any(token in text for token in ("vencid", "atras", "inadimpl")):
        return {"key": "vencido", "label": "Vencido", "tone": "red"}
    if due and due < today:
        return {"key": "vencido", "label": "Vencido", "tone": "red"}
    if due == today:
        return {"key": "a_entrar", "label": "Vence hoje", "tone": "yellow"}
    return {"key": "a_entrar", "label": normalize_text(status) or "A entrar", "tone": "blue"}


def _month_last(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _first_day_in_period(day: int, start: date, end: date) -> date | None:
    cursor = date(start.year, start.month, 1)
    limit = date(end.year, end.month, 1)
    while cursor <= limit:
        last = _month_last(cursor.year, cursor.month).day
        if day <= last:
            candidate = date(cursor.year, cursor.month, day)
            if start <= candidate <= end:
                return candidate
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return None


def _sheet_cell(item: dict, headers: list[str], index: int | None) -> str:
    if index is None or index >= len(headers):
        return ""
    return normalize_text(item.get(headers[index]))


def _payment_status_index(headers: list[str], mapping: dict[str, int]) -> int | None:
    for index, header in enumerate(headers):
        plain = normalize_search_text(header)
        has_status = "status" in plain or "situacao" in plain
        has_bill = any(word in plain for word in ("pagamento", "financeiro", "boleto", "cobranca"))
        if has_status and has_bill:
            return index
    return mapping.get("status_cadastro")


def raissa_rows_from_sheet(sheet: dict, *, today: date) -> list[dict]:
    """Uma linha financeira por empresa/plano da aba Raissa."""
    from app.services.raissa_company_sync import _header_map, company_hidden_on_raissa_list

    headers = list(sheet.get("colunas") or [])
    mapping = _header_map(headers)
    status_index = _payment_status_index(headers, mapping)
    ordered: list[dict] = []
    index_by_key: dict[tuple, int] = {}
    for item in sheet.get("clientes") or []:
        if not isinstance(item, dict):
            continue
        empresa = _sheet_cell(item, headers, mapping.get("empresa"))
        if not empresa:
            continue
        cnpj = _sheet_cell(item, headers, mapping.get("cnpj"))
        if company_hidden_on_raissa_list(empresa, cnpj):
            continue
        plano = _sheet_cell(item, headers, mapping.get("plano"))
        status = _sheet_cell(item, headers, status_index)
        forma = _sheet_cell(item, headers, mapping.get("forma"))
        valor = parse_money(_sheet_cell(item, headers, mapping.get("valor")))
        due, day = parse_sheet_due(_sheet_cell(item, headers, mapping.get("vencimento")), today=today)
        if valor <= 0 and not plano and not status and due is None and day is None:
            continue
        row = {
            "empresa": empresa,
            "cnpj": cnpj,
            "plano": plano or "Sem plano",
            "valor": round(max(valor, 0.0), 2),
            "vencimento": due,
            "dia": day,
            "status": status,
            "forma": forma,
        }
        key = (
            normalize_search_text(empresa),
            normalize_search_text(row["plano"]),
            row["valor"],
            due.isoformat() if due else "",
            day or 0,
        )
        if key in index_by_key:
            ordered[index_by_key[key]] = row
        else:
            index_by_key[key] = len(ordered)
            ordered.append(row)
    return ordered


def _empty_bucket() -> dict[str, float]:
    return {"a_entrar": 0.0, "pago": 0.0, "vencido": 0.0, "n_entrar": 0, "n_pago": 0, "n_vencido": 0}


def _add_bucket(bucket: dict[str, float], key: str, valor: float) -> None:
    if key not in {"a_entrar", "pago", "vencido"}:
        return
    bucket[key] += valor
    count_key = {"a_entrar": "n_entrar", "pago": "n_pago", "vencido": "n_vencido"}[key]
    bucket[count_key] += 1


def _bucket_view(label: str, bucket: dict[str, float], *, extra: dict | None = None) -> dict:
    total = bucket["a_entrar"] + bucket["pago"] + bucket["vencido"]
    view = {
        "label": label,
        "a_entrar_label": format_brl(bucket["a_entrar"]),
        "pago_label": format_brl(bucket["pago"]),
        "vencido_label": format_brl(bucket["vencido"]),
        "total_label": format_brl(total),
        "total": total,
        "n": int(bucket["n_entrar"] + bucket["n_pago"] + bucket["n_vencido"]),
    }
    if extra:
        view.update(extra)
    return view


def _period_due(row: dict, start: date, end: date) -> date | None:
    due = row.get("vencimento")
    if isinstance(due, date):
        return due if start <= due <= end else None
    day = row.get("dia")
    if isinstance(day, int):
        return _first_day_in_period(day, start, end)
    return None


def summarize_raissa_entrada(
    rows: list[dict],
    *,
    start: date,
    end: date,
    today: date,
    status: str = "",
    search: str = "",
) -> dict[str, Any]:
    """Resumo do período, a grade por mês da base inteira e a divisão por plano."""
    wanted = normalize_search_text(status)
    aliases = {"atrasado": "vencido", "vencidos": "vencido", "receber": "a_entrar", "a entrar": "a_entrar"}
    wanted = aliases.get(wanted, wanted)
    needle = normalize_search_text(search)
    period = _empty_bucket()
    listed: list[dict] = []
    months: dict[tuple[int, int], dict[str, float]] = {}
    recorrente = _empty_bucket()
    sem_data = _empty_bucket()
    plans: dict[str, dict[str, float]] = {}
    fora_n = 0

    for row in rows:
        due = row.get("vencimento") if isinstance(row.get("vencimento"), date) else None
        day = row.get("dia") if isinstance(row.get("dia"), int) else None
        classify_due = due
        if classify_due is None and day:
            classify_due = _first_day_in_period(day, date(today.year, today.month, 1), _month_last(today.year, today.month))
        classified = classify_raissa_entry(row.get("status") or "", classify_due, today)
        if classified["key"] == "fora":
            fora_n += 1
            continue
        valor = float(row.get("valor") or 0)
        if due:
            bucket = months.setdefault((due.year, due.month), _empty_bucket())
            _add_bucket(bucket, classified["key"], valor)
        elif day:
            _add_bucket(recorrente, classified["key"], valor)
        else:
            _add_bucket(sem_data, classified["key"], valor)

        shown_due = _period_due(row, start, end)
        if shown_due is None:
            continue
        period_status = classify_raissa_entry(row.get("status") or "", shown_due, today)
        if period_status["key"] == "fora":
            continue
        if wanted and period_status["key"] != wanted:
            continue
        blob = normalize_search_text(f"{row.get('empresa') or ''} {row.get('plano') or ''} {row.get('forma') or ''}")
        if needle and needle not in blob:
            continue
        _add_bucket(period, period_status["key"], valor)
        plan_name = normalize_text(row.get("plano")) or "Sem plano"
        _add_bucket(plans.setdefault(plan_name, _empty_bucket()), period_status["key"], valor)
        listed.append(
            {
                "cliente": row.get("empresa") or "—",
                "plano": plan_name,
                "forma": normalize_text(row.get("forma")) or "—",
                "valor": valor,
                "valor_label": format_brl(valor),
                "vencimento": shown_due,
                "vencimento_label": format_date_br(shown_due),
                "status_key": period_status["key"],
                "status_label": period_status["label"],
                "status_tone": period_status["tone"],
            }
        )

    listed.sort(key=lambda item: ({"vencido": 0, "a_entrar": 1, "pago": 2}.get(item["status_key"], 3), item["vencimento"], item["cliente"]))
    month_rows = []
    for (year, month), bucket in sorted(months.items()):
        month_start = date(year, month, 1)
        month_end = _month_last(year, month)
        month_rows.append(
            _bucket_view(
                f"{_MESES[month]} {year}",
                bucket,
                extra={
                    "period_start": month_start.isoformat(),
                    "period_end": month_end.isoformat(),
                    "selected": not (month_end < start or month_start > end),
                },
            )
        )
    if recorrente["n_entrar"] or recorrente["n_pago"] or recorrente["n_vencido"]:
        month_rows.append(_bucket_view("Todo mês (dia fixo)", recorrente))
    if sem_data["n_entrar"] or sem_data["n_pago"] or sem_data["n_vencido"]:
        month_rows.append(_bucket_view("Sem vencimento", sem_data))
    plan_rows = [
        _bucket_view(name, bucket)
        for name, bucket in sorted(plans.items(), key=lambda item: item[1]["a_entrar"] + item[1]["pago"] + item[1]["vencido"], reverse=True)
    ]
    total = period["a_entrar"] + period["pago"] + period["vencido"]
    return {
        "a_entrar": period["a_entrar"],
        "a_entrar_n": int(period["n_entrar"]),
        "a_entrar_label": format_brl(period["a_entrar"]),
        "pago": period["pago"],
        "pago_n": int(period["n_pago"]),
        "pago_label": format_brl(period["pago"]),
        "vencido": period["vencido"],
        "vencido_n": int(period["n_vencido"]),
        "vencido_label": format_brl(period["vencido"]),
        "total": total,
        "total_n": int(period["n_entrar"] + period["n_pago"] + period["n_vencido"]),
        "total_label": format_brl(total),
        "meses": month_rows,
        "planos": plan_rows,
        "linhas": listed,
        "fora_n": fora_n,
        "error": "",
        "aviso": "",
        "aba": "Raissa",
        "month_label": month_label(start, end),
    }


def _raissa_kpi_cards(controle: dict) -> list[dict]:
    def empresas(count: int) -> str:
        return f"{count} empresa" if count == 1 else f"{count} empresas"

    return [
        {
            "label": "A entrar",
            "value": controle.get("a_entrar_label") or format_brl(0),
            "note": f"{empresas(int(controle.get('a_entrar_n') or 0))} no período",
            "tone": "blue",
            "icon": "📅",
        },
        {
            "label": "Pago",
            "value": controle.get("pago_label") or format_brl(0),
            "note": f"{empresas(int(controle.get('pago_n') or 0))} no período",
            "tone": "green",
            "icon": "✓",
        },
        {
            "label": "Vencidos",
            "value": controle.get("vencido_label") or format_brl(0),
            "note": f"{empresas(int(controle.get('vencido_n') or 0))} no período",
            "tone": "orange",
            "icon": "!",
        },
        {
            "label": "Total do período",
            "value": controle.get("total_label") or format_brl(0),
            "note": controle.get("month_label") or "Período",
            "tone": "purple",
            "icon": "↓",
        },
    ]


def build_raissa_entrada_context(
    params: dict,
    start: date,
    end: date,
    today: date,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Entrada lê só a aba Raissa. Não consulta o Asaas."""
    from app.services.campaign_leads import read_raissa_sheet

    sheet = read_raissa_sheet(force=force)
    rows = raissa_rows_from_sheet(sheet, today=today)
    controle = summarize_raissa_entrada(
        rows,
        start=start,
        end=end,
        today=today,
        status=normalize_text(params.get("status")),
        search=normalize_text(params.get("search")),
    )
    controle["aba"] = sheet.get("aba") or "Raissa"
    controle["aviso"] = normalize_text(sheet.get("aviso"))
    controle["month_label"] = month_label(start, end)
    if not rows and controle["aviso"]:
        controle["error"] = controle["aviso"]
    elif not rows:
        controle["aviso"] = "A aba Raissa não trouxe plano, valor ou vencimento."
    return {
        "kpi_cards": _raissa_kpi_cards(controle),
        "controle": controle,
        "filters": params,
        "tab": TAB_ENTRADAS,
        "boletos": {"error": controle.get("error") or ""},
    }


def build_financeiro_context(
    params: dict | None = None,
    *,
    force_sync: bool = False,
    use_raissa: bool = False,
) -> dict[str, Any]:
    params = params or {}
    today = _today()
    period_start, period_end = resolve_period(params.get("period_start"), params.get("period_end"), today=today)
    params = {
        **params,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    tab = normalize_text(params.get("tab")).lower() or TAB_VISAO
    if tab not in {TAB_VISAO, TAB_FATURAS, TAB_RECORRENCIAS, TAB_ATRASO, TAB_ENTRADAS, TAB_PAGAR}:
        tab = TAB_VISAO
    if tab == TAB_ENTRADAS and use_raissa:
        return build_raissa_entrada_context(params, period_start, period_end, today, force=force_sync)

    forecast = build_internal_forecast(period_start, period_end)
    payable_status = normalize_text(params.get("status")) if tab == TAB_PAGAR else ""
    payables = _payables_view(
        period_start,
        period_end,
        normalize_text(params.get("search")),
        payable_status,
        today,
    )
    if tab == TAB_PAGAR:
        payables = attach_cash_compare(payables, period_start, period_end, today, force=force_sync)
    boletos = (
        apply_boleto_status(
            load_boleto_entrada(today, period_start, period_end, force=force_sync),
            normalize_text(params.get("status")),
        )
        if tab == TAB_ENTRADAS
        else summarize_boletos([], [], today)
    )
    empty = {
        "active_page": "financeiro",
        "asaas_configured": is_configured(),
        "asaas_error": "",
        "kpi_cards": _kpi_cards(0, 0, 0, 0, 0, 0, 0, 0),
        "forecast": forecast,
        "invoices": [],
        "saldo": 0.0,
        "saldo_label": format_brl(0),
        "saldo_count": 0,
        "subscriptions": [],
        "overdue_clients": [],
        "entradas": [],
        "entradas_total": 0.0,
        "entradas_total_label": format_brl(0),
        "entradas_error": "",
        "payables": payables,
        "boletos": boletos,
        "tab": tab,
        "filters": params,
        "status_options": [
            ("", "Todos os status"),
            ("receber", "A receber"),
            ("atrasados", "Atrasados"),
            ("pagos", "Pagos"),
            ("cancelados", "Cancelados"),
            ("cartao_recorrente", "Cartão recorrente"),
            ("pix", "PIX"),
            ("boleto", "Boleto"),
        ],
        "forma_options": [
            ("", "Todas as formas"),
            ("boleto", "Boleto"),
            ("pix", "PIX"),
            ("cartao", "Cartão"),
        ],
    }

    if not is_configured():
        empty["kpi_cards"] = _kpi_cards(0, 0, 0, 0, 0, 0, 0, 0)
        empty["asaas_error"] = "Configure ASAAS_API_KEY no Easypanel para puxar as cobranças."
        empty["entradas_error"] = empty["asaas_error"]
        return _with_tab_kpis(empty, tab)

    try:
        payload = fetch_dashboard_payload(force=force_sync)
    except AsaasError as exc:
        empty["kpi_cards"] = _kpi_cards(0, 0, 0, 0, 0, 0, 0, 0)
        empty["asaas_error"] = str(exc)
        empty["entradas_error"] = str(exc)
        return _with_tab_kpis(empty, tab)
    except Exception:
        logger.exception("Falha inesperada no Asaas")
        empty["kpi_cards"] = _kpi_cards(0, 0, 0, 0, 0, 0, 0, 0)
        empty["asaas_error"] = "Não foi possível sincronizar o Asaas agora."
        empty["entradas_error"] = empty["asaas_error"]
        return _with_tab_kpis(empty, tab)

    customers = {
        normalize_text(row.get("id")): row
        for row in (payload.get("customers") or [])
        if normalize_text(row.get("id"))
    }
    crm_rows = _load_crm_rows()
    company_names: dict[int, str] = {}
    for row in crm_rows:
        sheet_row = row.get("sheet_row")
        empresa = normalize_text(row.get("empresa"))
        if sheet_row and empresa:
            company_names[int(sheet_row)] = empresa
    forecast = build_internal_forecast(period_start, period_end, company_names=company_names)
    empty["forecast"] = forecast

    invoices = [
        _map_invoice(row, customers, crm_rows, today)
        for row in (payload.get("payments") or [])
    ]
    invoices.sort(key=lambda row: row.get("vencimento") or date.min, reverse=True)
    subscriptions = [
        _map_subscription(row, customers, crm_rows)
        for row in (payload.get("subscriptions") or [])
    ]
    subscriptions.sort(key=lambda row: row.get("proximo_vencimento") or date.max)

    receber_mes = [
        row for row in invoices
        if row["status_key"] in {"receber", "vence_hoje", "recorrente"}
        and row.get("vencimento")
        and period_start <= row["vencimento"] <= period_end
    ]
    recebido_mes = [
        row for row in invoices
        if row["status_key"] == "pago"
        and row.get("payment_date")
        and period_start <= row["payment_date"] <= period_end
    ]
    if not recebido_mes:
        recebido_mes = [
            row for row in invoices
            if row["status_key"] == "pago"
            and row.get("vencimento")
            and period_start <= row["vencimento"] <= period_end
        ]
    atrasados = [
        row for row in invoices
        if row["status_key"] == "atrasado"
        and row.get("vencimento")
        and period_start <= row["vencimento"] <= period_end
    ]
    proximos = [
        row for row in invoices
        if row["status_key"] in {"receber", "vence_hoje", "recorrente"}
        and row.get("vencimento")
        and today <= row["vencimento"] <= today + timedelta(days=7)
    ]
    ativas = [row for row in subscriptions if row.get("ativa")]
    recebido_valor = sum(row["valor"] for row in recebido_mes)
    atrasado_valor = sum(row["valor"] for row in atrasados)
    denom = recebido_valor + atrasado_valor
    inadimplencia = (atrasado_valor / denom * 100) if denom else 0.0

    overdue_groups: dict[str, dict[str, Any]] = {}
    for row in atrasados:
        key = row.get("cliente") or row.get("id")
        group = overdue_groups.setdefault(key, {
            "cliente": row["cliente"],
            "valor": 0.0,
            "count": 0,
            "oldest": row.get("vencimento"),
            "sheet_row": row.get("sheet_row"),
            "cliente_href": row.get("cliente_href") or "",
            "whatsapp_url": row.get("whatsapp_url") or "",
        })
        group["valor"] += row["valor"]
        group["count"] += 1
        if row.get("vencimento") and (not group["oldest"] or row["vencimento"] < group["oldest"]):
            group["oldest"] = row["vencimento"]
            if row.get("whatsapp_url"):
                group["whatsapp_url"] = row["whatsapp_url"]
        if not group["cliente_href"] and row.get("cliente_href"):
            group["cliente_href"] = row["cliente_href"]
            group["sheet_row"] = row.get("sheet_row")

    overdue_clients = []
    for group in overdue_groups.values():
        oldest = group.get("oldest")
        days = (today - oldest).days if oldest else 0
        overdue_clients.append({
            **group,
            "valor_label": format_brl(group["valor"]),
            "days": max(days, 0),
            "days_label": f"Vencido há {max(days, 0)} dia{'s' if max(days, 0) != 1 else ''}",
            "count_label": f"{group['count']} fatura{'s' if group['count'] != 1 else ''} em atraso",
        })
    overdue_clients.sort(key=lambda row: row.get("days") or 0, reverse=True)

    entered = charges_entered_in_period(invoices, period_start, period_end)
    listed = _filter_invoices(entered, {**params, "period_start": "", "period_end": ""})
    saldo = sum(row["valor"] for row in listed)
    asaas_cards = _kpi_cards(
            sum(row["valor"] for row in receber_mes),
            len(receber_mes),
            recebido_valor,
            len(recebido_mes),
            atrasado_valor,
            len({row["cliente"] for row in atrasados}),
            len(ativas),
            len(proximos),
            inadimplencia,
        )
    if tab == TAB_ENTRADAS:
        entradas, entradas_error, _ = _load_entradas(
            period_start,
            period_end,
            invoices=invoices,
            search=normalize_text(params.get("search")),
            force=force_sync,
        )
    else:
        entradas, entradas_error = [], ""
    empty.update({
        "kpi_cards": asaas_cards,
        "invoices": listed,
        "saldo": saldo,
        "saldo_label": format_brl(saldo),
        "saldo_count": len(listed),
        "subscriptions": subscriptions,
        "overdue_clients": overdue_clients,
        "inadimplencia": inadimplencia,
        "asaas_error": "",
        "entradas": entradas,
        "entradas_total": sum(row["valor"] for row in entradas),
        "entradas_total_label": format_brl(sum(row["valor"] for row in entradas)),
        "entradas_error": entradas_error,
    })
    return _with_tab_kpis(empty, tab)


def _load_entradas(
    start: date,
    end: date,
    *,
    invoices: list[dict],
    search: str,
    force: bool,
) -> tuple[list[dict], str, str]:
    saldo_label = ""
    try:
        balance = fetch_account_balance(force=force)
        if balance is not None:
            saldo_label = format_brl(balance)
    except AsaasError as exc:
        logger.warning("Saldo Asaas indisponível: %s", exc)
    except Exception:
        logger.exception("Falha ao ler saldo Asaas")
    try:
        statement = fetch_statement(start, end, force=force)
    except AsaasError as exc:
        return [], str(exc), saldo_label
    except Exception:
        logger.exception("Falha ao ler extrato Asaas")
        return [], "Não foi possível ler as entradas do Asaas agora.", saldo_label
    index = {normalize_text(row.get("id")): row for row in invoices if normalize_text(row.get("id"))}
    rows = [
        row for row in map_entradas(statement, index)
        if (not row.get("date")) or start <= row["date"] <= end
    ]
    needle = normalize_text(search).lower()
    if needle:
        rows = [
            row for row in rows
            if needle in f"{row['description']} {row['tipo']}".lower()
        ]
    return rows, "", saldo_label


def _with_tab_kpis(ctx: dict[str, Any], tab: str) -> dict[str, Any]:
    if tab == TAB_ENTRADAS:
        boletos = ctx.get("boletos") or {}
        ctx["kpi_cards"] = [
            {
                "label": "Boletos vencem hoje",
                "value": str(boletos.get("hoje_n") or 0),
                "note": boletos.get("hoje_valor_label") or format_brl(0),
                "tone": "orange",
                "icon": "!",
            },
            {
                "label": "Boletos que entraram",
                "value": str(boletos.get("entraram_n") or 0),
                "note": boletos.get("entraram_valor_label") or format_brl(0),
                "tone": "green",
                "icon": "✓",
            },
            {
                "label": "Todas as entradas",
                "value": str(boletos.get("entradas_n") or 0),
                "note": boletos.get("entradas_valor_label") or format_brl(0),
                "tone": "purple",
                "icon": "↓",
            },
            {
                "label": "Ainda entram",
                "value": str(boletos.get("mes_abertos_n") or 0),
                "note": boletos.get("mes_abertos_label") or format_brl(0),
                "tone": "blue",
                "icon": "📅",
            },
        ]
    elif tab == TAB_PAGAR:
        payables = ctx.get("payables") or {}
        ctx["kpi_cards"] = [
            {
                "label": "A pagar",
                "value": payables.get("a_pagar_label") or format_brl(0),
                "note": "Em aberto no período, incluindo as atrasadas",
                "tone": "orange",
                "icon": "!",
            },
            {
                "label": "Pago",
                "value": payables.get("pago_label") or format_brl(0),
                "note": "Contas quitadas no período",
                "tone": "green",
                "icon": "✓",
            },
            {
                "label": "Atrasado",
                "value": payables.get("atrasado_label") or format_brl(0),
                "note": f"{payables.get('atrasado_n') or 0} conta(s) vencida(s)",
                "tone": "orange",
                "icon": "!",
            },
            {
                "label": "Entrada − saída",
                "value": payables.get("saldo_label") or format_brl(0),
                "note": f"{payables.get('month_label') or 'Período'} · entrou {payables.get('entrada_label') or format_brl(0)} · saiu {payables.get('saida_label') or format_brl(0)}",
                "tone": "purple",
                "icon": "↔",
            },
        ]
    return ctx


def _kpi_cards(
    receber: float,
    receber_n: int,
    recebido: float,
    recebido_n: int,
    atraso: float,
    atraso_n: int,
    recorrencias: int,
    proximos: int,
    inadimplencia: float = 0.0,
) -> list[dict]:
    return [
        {
            "label": "A receber no mês",
            "value": format_brl(receber),
            "note": f"{receber_n} cobrança{'s' if receber_n != 1 else ''} em aberto",
            "tone": "purple",
            "icon": "👛",
        },
        {
            "label": "Recebido no mês",
            "value": format_brl(recebido),
            "note": f"{recebido_n} cobrança{'s' if recebido_n != 1 else ''} paga{'s' if recebido_n != 1 else ''}",
            "tone": "green",
            "icon": "✓",
        },
        {
            "label": "Em atraso",
            "value": format_brl(atraso),
            "note": f"{atraso_n} cliente{'s' if atraso_n != 1 else ''} inadimplente{'s' if atraso_n != 1 else ''}",
            "tone": "orange",
            "icon": "!",
        },
        {
            "label": "Recorrências ativas",
            "value": str(recorrencias),
            "note": "Cartão recorrente e mensalidades",
            "tone": "purple",
            "icon": "↻",
        },
        {
            "label": "Próximos vencimentos",
            "value": str(proximos),
            "note": "Vencem nos próximos 7 dias",
            "tone": "pink",
            "icon": "📅",
        },
        {
            "label": "Inadimplência",
            "value": f"{inadimplencia:.0f}%",
            "note": "Atraso sobre recebido + atrasado",
            "tone": "orange",
            "icon": "%",
        },
    ]


def group_asaas_payments_by_value(payments: list[dict] | None, start: date, end: date) -> list[dict]:
    """Agrupa cobranças Asaas já carregadas (sem nova chamada HTTP)."""
    from collections import defaultdict

    today = _today()
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"recebido": 0.0, "a_receber": 0.0})
    for payment in payments or []:
        if not isinstance(payment, dict):
            continue
        classified = classify_payment(payment, today=today)
        value = float(payment.get("value") or 0)
        key = f"{value:.2f}"
        due = _parse_date(payment.get("dueDate"))
        paid_on = _parse_date(payment.get("paymentDate") or payment.get("confirmedDate"))
        if classified["key"] == "pago":
            when = paid_on or due
            if when and start <= when <= end:
                grouped[key]["recebido"] += value
            continue
        if classified["key"] in {"receber", "vence_hoje", "recorrente", "atrasado"} and due and start <= due <= end:
            grouped[key]["a_receber"] += value

    rows = []
    for key in sorted(grouped.keys(), key=lambda item: float(item)):
        item = grouped[key]
        rows.append(
            {
                "label": format_brl(float(key)),
                "recebido": item["recebido"],
                "recebido_label": format_brl(item["recebido"]),
                "a_receber": item["a_receber"],
                "a_receber_label": format_brl(item["a_receber"]),
            }
        )
    return rows
