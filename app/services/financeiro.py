"""Painel Financeiro — Asaas + vínculo com cadastros do CRM."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.services.asaas_client import (
    AsaasError,
    fetch_account_balance,
    fetch_dashboard_payload,
    fetch_payments_due,
    fetch_statement,
    is_configured,
)
from app.services.company_payables import list_payables, payable_calendar
from app.services.internal_finance import build_internal_forecast, resolve_period
from app.services.legacy_core import (
    normalize_cnpj_for_duplicate,
    normalize_phone_for_duplicate,
    normalize_text,
    phones_match_for_duplicate,
)

logger = logging.getLogger(__name__)
_TZ = ZoneInfo("America/Sao_Paulo")

PAID_STATUSES = {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}
PENDING_STATUSES = {"PENDING", "AWAITING_RISK_ANALYSIS"}
OVERDUE_STATUSES = {"OVERDUE"}
CANCELLED_STATUSES = {"REFUNDED", "REFUND_REQUESTED", "DELETED"}

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


def _boleto_row(payment: dict, today: date) -> dict[str, Any] | None:
    if normalize_text(payment.get("billingType")).upper() != "BOLETO":
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
    }


def load_boleto_entrada(today: date, start: date, end: date, *, force: bool = False) -> dict[str, Any]:
    blank = summarize_boletos([], [], today)
    blank["month_label"] = month_label(start, end)
    if not is_configured():
        blank["error"] = "Configure ASAAS_API_KEY para ler os boletos."
        return blank
    try:
        hoje_raw = fetch_payments_due(today, today, billing_type="BOLETO", force=force)
        mes_raw = fetch_payments_due(start, end, billing_type="BOLETO", force=force)
    except AsaasError as exc:
        blank["error"] = str(exc)
        return blank
    except Exception:
        logger.exception("Falha ao ler boletos do Asaas")
        blank["error"] = "Não foi possível ler os boletos do Asaas agora."
        return blank
    result = summarize_boletos(hoje_raw, mes_raw, today)
    result["month_label"] = month_label(start, end)
    return result


def _payables_view(start: date, end: date, search: str) -> dict[str, Any]:
    rows = list_payables(start, end)
    needle = normalize_text(search).lower()
    if needle:
        rows = [row for row in rows if needle in row["description"].lower()]
    calendar = payable_calendar(rows, start, end)
    listed = []
    for row in rows:
        listed.append(
            {
                **row,
                "valor_label": format_brl(row["amount"]),
                "due_label": format_date_br(row["due"]),
                "paid_label": format_date_br(row["paid_on"]) if row.get("paid_on") else "",
            }
        )
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
                    }
                    for item in day["items"]
                ],
            }
            for day in calendar["days"]
        ],
        "show_grid": calendar["show_grid"],
        "a_pagar": calendar["a_pagar"],
        "pago": calendar["pago"],
        "aberto": calendar["aberto"],
        "a_pagar_label": format_brl(calendar["a_pagar"]),
        "pago_label": format_brl(calendar["pago"]),
        "aberto_label": format_brl(calendar["aberto"]),
        "month_label": month_label(start, end),
    }


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


def build_financeiro_context(params: dict | None = None, *, force_sync: bool = False) -> dict[str, Any]:
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

    forecast = build_internal_forecast(period_start, period_end)
    payables = _payables_view(period_start, period_end, normalize_text(params.get("search")))
    boletos = (
        load_boleto_entrada(today, period_start, period_end, force=force_sync)
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
        "subscriptions": [],
        "overdue_clients": [],
        "entradas": [],
        "entradas_total": 0.0,
        "entradas_total_label": format_brl(0),
        "entradas_error": "",
        "saldo_label": "",
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

    filtered = _filter_invoices(invoices, params)
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
        entradas, entradas_error, saldo_label = _load_entradas(
            period_start,
            period_end,
            invoices=invoices,
            search=normalize_text(params.get("search")),
            force=force_sync,
        )
    else:
        entradas, entradas_error, saldo_label = [], "", ""
    empty.update({
        "kpi_cards": asaas_cards,
        "invoices": filtered,
        "subscriptions": subscriptions,
        "overdue_clients": overdue_clients,
        "inadimplencia": inadimplencia,
        "asaas_error": "",
        "entradas": entradas,
        "entradas_total": sum(row["valor"] for row in entradas),
        "entradas_total_label": format_brl(sum(row["valor"] for row in entradas)),
        "entradas_error": entradas_error,
        "saldo_label": saldo_label,
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
                "label": "Entram no período",
                "value": str(boletos.get("mes_n") or 0),
                "note": f"{boletos.get('month_label') or 'Período'} · {boletos.get('mes_valor_label') or format_brl(0)}",
                "tone": "purple",
                "icon": "↓",
            },
            {
                "label": "Já recebidos",
                "value": str(boletos.get("mes_pagos_n") or 0),
                "note": boletos.get("mes_pagos_label") or format_brl(0),
                "tone": "green",
                "icon": "✓",
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
                "note": "Contas ainda em aberto no período",
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
                "label": "Em aberto",
                "value": payables.get("aberto_label") or format_brl(0),
                "note": payables.get("month_label") or "Período selecionado",
                "tone": "purple",
                "icon": "📅",
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
