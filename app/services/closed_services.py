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
