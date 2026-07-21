"""Serviços fechados por cadastro — persistidos em lead_actions e espelhados na planilha."""
from __future__ import annotations

from starlette.datastructures import FormData

from app.services.lead_actions_storage import get_lead_action, save_lead_action
from app.services.legacy_core import as_python_date, normalize_text, parse_date

PAYMENT_METHOD_OPTIONS = [
    "Mensal",
    "Anual",
    "À vista",
    "Parcelado",
    "Boleto",
    "PIX",
    "Cartão",
]

_EMPTY_ITEM = {
    "servico": "",
    "valor": "",
    "forma_pagamento": "Mensal",
    "vencimento": "",
}


def _format_date_input(value: str) -> str:
    parsed = as_python_date(parse_date(value)) or as_python_date(value)
    if parsed:
        return parsed.isoformat()
    if len(normalize_text(value)) == 10 and normalize_text(value)[4] == "-":
        return normalize_text(value)
    return ""


def _normalize_item(raw: dict | None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    forma = normalize_text(data.get("forma_pagamento")) or "Mensal"
    if forma not in PAYMENT_METHOD_OPTIONS:
        forma = "Mensal"
    return {
        "servico": normalize_text(data.get("servico")),
        "valor": normalize_text(data.get("valor")),
        "forma_pagamento": forma,
        "vencimento": _format_date_input(normalize_text(data.get("vencimento"))),
    }


def _has_data(item: dict) -> bool:
    return any(normalize_text(item.get(key)) for key in ("servico", "valor", "vencimento"))


def closed_services_has_data(items: list[dict]) -> bool:
    return any(_has_data(item) for item in items if isinstance(item, dict))


def load_closed_services(
    tenant_id: str | None,
    sheet_row: int,
    *,
    servico: str = "",
    valor_proposta: str = "",
) -> list[dict]:
    lead_action = get_lead_action(tenant_id, sheet_row) or {}
    stored = lead_action.get("closed_services")
    if isinstance(stored, list) and stored:
        items = [_normalize_item(item) for item in stored if isinstance(item, dict)]
        return items or [_EMPTY_ITEM.copy()]

    return [
        _normalize_item(
            {
                "servico": servico,
                "valor": valor_proposta,
                "forma_pagamento": lead_action.get("forma_pagamento", "Mensal"),
                "vencimento": lead_action.get("vencimento", ""),
            }
        )
    ]


def parse_closed_services_from_form(form: FormData) -> list[dict]:
    servicos = form.getlist("closed_servico")
    valores = form.getlist("closed_valor")
    pagamentos = form.getlist("closed_forma_pagamento")
    vencimentos = form.getlist("closed_vencimento")
    total = max(len(servicos), len(valores), len(pagamentos), len(vencimentos), 1)

    items: list[dict] = []
    for index in range(total):
        items.append(
            _normalize_item(
                {
                    "servico": servicos[index] if index < len(servicos) else "",
                    "valor": valores[index] if index < len(valores) else "",
                    "forma_pagamento": pagamentos[index] if index < len(pagamentos) else "Mensal",
                    "vencimento": vencimentos[index] if index < len(vencimentos) else "",
                }
            )
        )

    filtered = [item for item in items if _has_data(item)]
    return filtered or [_EMPTY_ITEM.copy()]


def save_closed_services(
    tenant_id: str | None,
    sheet_row: int,
    items: list[dict],
) -> dict:
    normalized = [_normalize_item(item) for item in items if isinstance(item, dict)]
    if not normalized:
        normalized = [_EMPTY_ITEM.copy()]

    primary = normalized[0]
    save_lead_action(
        tenant_id,
        sheet_row,
        {
            "closed_services": normalized,
            "forma_pagamento": primary.get("forma_pagamento", ""),
            "vencimento": primary.get("vencimento", ""),
        },
    )
    return primary


def _format_vencimento_display(value: str) -> str:
    raw = normalize_text(value)
    if not raw:
        return ""
    parsed = as_python_date(parse_date(raw)) or as_python_date(raw)
    if parsed:
        return parsed.strftime("%d/%m/%Y")
    return raw


def summarize_closed_services_for_display(
    tenant_id: str | None,
    sheet_row: int,
    *,
    servico: str = "",
    valor_proposta: str = "",
) -> dict:
    items = load_closed_services(
        tenant_id,
        sheet_row,
        servico=servico,
        valor_proposta=valor_proposta,
    )
    active_items = [item for item in items if _has_data(item)]
    if not active_items:
        return {
            "closed_services_title": "—",
            "closed_services_meta": "",
        }

    first = active_items[0]
    title = normalize_text(first.get("servico")) or "Proposta comercial"
    meta_parts: list[str] = []
    if normalize_text(first.get("valor")):
        meta_parts.append(normalize_text(first["valor"]))
    if normalize_text(first.get("forma_pagamento")):
        meta_parts.append(normalize_text(first["forma_pagamento"]))
    vencimento = _format_vencimento_display(first.get("vencimento", ""))
    if vencimento:
        meta_parts.append(vencimento)

    meta = " · ".join(meta_parts)
    extra_count = len(active_items) - 1
    if extra_count > 0:
        suffix = f"+{extra_count} serviço(s)"
        meta = f"{meta} · {suffix}" if meta else suffix

    return {
        "closed_services_title": title,
        "closed_services_meta": meta,
    }
