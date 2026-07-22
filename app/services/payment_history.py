"""Histórico de pagamentos do cliente — persistido em lead_actions."""
from __future__ import annotations

from starlette.datastructures import FormData

from app.services.lead_actions_storage import get_lead_action, save_lead_action
from app.services.legacy_core import as_python_date, normalize_text, parse_date

PAYMENT_STATUS_OPTIONS = ["Pago", "Pendente", "Atrasado", "Cancelado"]

_EMPTY_PAYMENT = {
    "data": "",
    "descricao": "",
    "valor": "",
    "status": "Pendente",
    "forma_pagamento": "PIX",
}


def _format_date_input(value: str) -> str:
    parsed = as_python_date(parse_date(value)) or as_python_date(value)
    if parsed:
        return parsed.isoformat()
    raw = normalize_text(value)
    if len(raw) == 10 and raw[4] == "-":
        return raw
    return ""


def _format_date_display(value: str) -> str:
    raw = normalize_text(value)
    if not raw:
        return "—"
    parsed = as_python_date(parse_date(raw)) or as_python_date(raw)
    if parsed:
        return parsed.strftime("%d/%m/%Y")
    return raw


def _normalize_payment(raw: dict | None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    status = normalize_text(data.get("status")) or "Pendente"
    if status not in PAYMENT_STATUS_OPTIONS:
        status = "Pendente"
    return {
        "data": _format_date_input(normalize_text(data.get("data"))),
        "descricao": normalize_text(data.get("descricao")),
        "valor": normalize_text(data.get("valor")),
        "status": status,
        "forma_pagamento": normalize_text(data.get("forma_pagamento")) or "PIX",
        "data_display": _format_date_display(normalize_text(data.get("data"))),
    }


def _has_payment_data(item: dict) -> bool:
    return any(normalize_text(item.get(key)) for key in ("data", "descricao", "valor"))


def load_payment_history(tenant_id: str | None, sheet_row: int) -> list[dict]:
    lead_action = get_lead_action(tenant_id, sheet_row) or {}
    stored = lead_action.get("payment_history")
    if not isinstance(stored, list) or not stored:
        return []
    items = [_normalize_payment(item) for item in stored if isinstance(item, dict)]
    return [item for item in items if _has_payment_data(item)]


def parse_payment_history_from_form(form: FormData) -> list[dict]:
    datas = form.getlist("pay_data")
    descricoes = form.getlist("pay_descricao")
    valores = form.getlist("pay_valor")
    statuses = form.getlist("pay_status")
    formas = form.getlist("pay_forma_pagamento")
    total = max(len(datas), len(descricoes), len(valores), len(statuses), len(formas), 0)
    if total == 0:
        return []

    items: list[dict] = []
    for index in range(total):
        items.append(
            _normalize_payment(
                {
                    "data": datas[index] if index < len(datas) else "",
                    "descricao": descricoes[index] if index < len(descricoes) else "",
                    "valor": valores[index] if index < len(valores) else "",
                    "status": statuses[index] if index < len(statuses) else "Pendente",
                    "forma_pagamento": formas[index] if index < len(formas) else "PIX",
                }
            )
        )
    return [item for item in items if _has_payment_data(item)]


def save_payment_history(tenant_id: str | None, sheet_row: int, items: list[dict]) -> list[dict]:
    if not sheet_row:
        return []
    normalized = [_normalize_payment(item) for item in items if isinstance(item, dict)]
    normalized = [item for item in normalized if _has_payment_data(item)]
    # Remove campo só de exibição antes de persistir.
    payload_items = [
        {
            "data": item.get("data", ""),
            "descricao": item.get("descricao", ""),
            "valor": item.get("valor", ""),
            "status": item.get("status", "Pendente"),
            "forma_pagamento": item.get("forma_pagamento", "PIX"),
        }
        for item in normalized
    ]
    save_lead_action(tenant_id, sheet_row, {"payment_history": payload_items})
    return load_payment_history(tenant_id, sheet_row)


def financial_summary(closed_services: list[dict], payments: list[dict]) -> dict:
    active_services = [
        item for item in closed_services
        if any(normalize_text(item.get(key)) for key in ("servico", "valor", "vencimento"))
    ]
    paid = [item for item in payments if item.get("status") == "Pago"]
    pending = [item for item in payments if item.get("status") in {"Pendente", "Atrasado"}]
    next_due = ""
    for item in active_services:
        if normalize_text(item.get("vencimento")):
            next_due = _format_date_display(item.get("vencimento", ""))
            break
    return {
        "services_count": len(active_services),
        "payments_count": len(payments),
        "paid_count": len(paid),
        "pending_count": len(pending),
        "next_due": next_due or "—",
        "status_label": "Em dia" if not pending else ("Atenção" if any(p.get("status") == "Atrasado" for p in pending) else "Pendente"),
    }
