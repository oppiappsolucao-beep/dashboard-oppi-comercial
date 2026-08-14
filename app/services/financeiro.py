"""Painel Financeiro — Asaas + vínculo com cadastros do CRM."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.services.asaas_client import AsaasError, fetch_dashboard_payload, is_configured
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


def _service_name(item: dict) -> str:
    description = normalize_text(item.get("description") or item.get("externalReference"))
    if description:
        return description.split("\n", 1)[0][:80]
    return "Cobrança Asaas"


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
    service = _service_name(payment)
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
        "servico": _service_name(item),
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
        if start and due and due < start:
            continue
        if end and due and due > end:
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
    month_start = today.replace(day=1)
    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)
    next_week = today + timedelta(days=7)
    tab = normalize_text(params.get("tab")).lower() or TAB_VISAO
    if tab not in {TAB_VISAO, TAB_FATURAS, TAB_RECORRENCIAS, TAB_ATRASO}:
        tab = TAB_VISAO

    empty = {
        "active_page": "financeiro",
        "asaas_configured": is_configured(),
        "asaas_error": "",
        "kpi_cards": [],
        "invoices": [],
        "subscriptions": [],
        "overdue_clients": [],
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
        return empty

    try:
        payload = fetch_dashboard_payload(force=force_sync)
    except AsaasError as exc:
        empty["kpi_cards"] = _kpi_cards(0, 0, 0, 0, 0, 0, 0, 0)
        empty["asaas_error"] = str(exc)
        return empty
    except Exception:
        logger.exception("Falha inesperada no Asaas")
        empty["kpi_cards"] = _kpi_cards(0, 0, 0, 0, 0, 0, 0, 0)
        empty["asaas_error"] = "Não foi possível sincronizar o Asaas agora."
        return empty

    customers = {
        normalize_text(row.get("id")): row
        for row in (payload.get("customers") or [])
        if normalize_text(row.get("id"))
    }
    crm_rows = _load_crm_rows()
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
        and month_start <= row["vencimento"] <= month_end
    ]
    recebido_mes = [
        row for row in invoices
        if row["status_key"] == "pago"
        and row.get("payment_date")
        and month_start <= row["payment_date"] <= month_end
    ]
    if not recebido_mes:
        recebido_mes = [
            row for row in invoices
            if row["status_key"] == "pago"
            and row.get("vencimento")
            and month_start <= row["vencimento"] <= month_end
        ]
    atrasados = [row for row in invoices if row["status_key"] == "atrasado"]
    proximos = [
        row for row in invoices
        if row["status_key"] in {"receber", "vence_hoje", "recorrente"}
        and row.get("vencimento")
        and today <= row["vencimento"] <= next_week
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
    empty.update({
        "kpi_cards": _kpi_cards(
            sum(row["valor"] for row in receber_mes),
            len(receber_mes),
            recebido_valor,
            len(recebido_mes),
            atrasado_valor,
            len({row["cliente"] for row in atrasados}),
            len(ativas),
            len(proximos),
            inadimplencia,
        ),
        "invoices": filtered,
        "subscriptions": subscriptions,
        "overdue_clients": overdue_clients,
        "inadimplencia": inadimplencia,
        "asaas_error": "",
    })
    return empty


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
