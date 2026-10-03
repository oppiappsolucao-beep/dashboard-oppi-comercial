"""Previsão financeira interna a partir dos serviços contratados (independente do Asaas)."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from typing import Any

from app.services.lead_actions_storage import DEFAULT_TENANT_ID, get_all_lead_actions
from app.services.legacy_core import as_python_date, normalize_text, parse_date, parse_money

_RECURRING_MONTHLY = {"mensal", "cartão", "cartao", "boleto", "pix"}
_RECURRING_YEARLY = {"anual"}


def format_brl(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def parse_iso_date(value: str | None) -> date | None:
    raw = normalize_text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return as_python_date(parse_date(raw)) or as_python_date(raw)


def default_month_period(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today.replace(day=1)
    last = calendar.monthrange(today.year, today.month)[1]
    return start, date(today.year, today.month, last)


def resolve_period(start_raw: str | None, end_raw: str | None, *, today: date | None = None) -> tuple[date, date]:
    start = parse_iso_date(start_raw)
    end = parse_iso_date(end_raw)
    if start and end and end < start:
        start, end = end, start
    if start and end:
        return start, end
    month_start, month_end = default_month_period(today)
    return start or month_start, end or month_end


def previous_calendar_month(start: date, end: date) -> tuple[date, date]:
    """Mesmo recorte deslocado um mês para trás."""
    def shift(d: date) -> date:
        month = d.month - 1
        year = d.year
        if month < 1:
            month = 12
            year -= 1
        last = calendar.monthrange(year, month)[1]
        return date(year, month, min(d.day, last))

    return shift(start), shift(end)


def _quantidade(item: dict) -> int:
    raw = normalize_text(item.get("quantidade")) or "1"
    try:
        qty = int(float(raw.replace(",", ".")))
    except (TypeError, ValueError):
        qty = 1
    return max(qty, 1)


def _service_amount(item: dict) -> float:
    return parse_money(item.get("valor")) * _quantidade(item)


def _is_monthly(forma: str) -> bool:
    key = normalize_text(forma).lower()
    if any(token in key for token in _RECURRING_YEARLY):
        return False
    if "avista" in key.replace(" ", "") or "à vista" in key or "vista" in key:
        return False
    if "parcelado" in key or "avulso" in key:
        return False
    if "mensal" in key:
        return True
    if "recorrente" in key:
        return True
    return key in _RECURRING_MONTHLY or key in {"cartão recorrente", "boleto recorrente"}


def _is_yearly(forma: str) -> bool:
    return "anual" in normalize_text(forma).lower()


def _add_months(anchor: date, months: int) -> date:
    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor.day, last))


def occurrences_in_period(item: dict, start: date, end: date) -> list[date]:
    venc = parse_iso_date(item.get("vencimento"))
    forma = normalize_text(item.get("forma_pagamento"))
    dates: list[date] = []
    if not venc:
        return dates
    if _is_yearly(forma):
        year = start.year
        while year <= end.year:
            try:
                occ = date(year, venc.month, min(venc.day, calendar.monthrange(year, venc.month)[1]))
            except ValueError:
                year += 1
                continue
            if start <= occ <= end:
                dates.append(occ)
            year += 1
        return dates
    if _is_monthly(forma):
        cursor = venc
        if cursor < start:
            months = (start.year - cursor.year) * 12 + (start.month - cursor.month)
            cursor = _add_months(venc, max(months, 0))
            if cursor < start:
                cursor = _add_months(cursor, 1)
        while cursor <= end:
            if cursor >= start:
                dates.append(cursor)
            cursor = _add_months(cursor, 1)
        return dates
    if start <= venc <= end:
        dates.append(venc)
    return dates


def _payment_amount(item: dict) -> float:
    return parse_money(item.get("valor"))


def collect_company_services(tenant_id: str | None = DEFAULT_TENANT_ID) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    actions = get_all_lead_actions(tenant_id) or {}
    for sheet_key, record in actions.items():
        if not isinstance(record, dict):
            continue
        try:
            sheet_row = int(sheet_key)
        except (TypeError, ValueError):
            continue
        stored = record.get("closed_services")
        if not isinstance(stored, list):
            stored = []
        payments = record.get("payment_history") if isinstance(record.get("payment_history"), list) else []
        billing = record.get("billing_plan") if isinstance(record.get("billing_plan"), dict) else {}
        for item in stored:
            if not isinstance(item, dict):
                continue
            if not normalize_text(item.get("servico")) and not normalize_text(item.get("valor")):
                continue
            rows.append(
                {
                    "sheet_row": sheet_row,
                    "servico": normalize_text(item.get("servico")) or "Serviço",
                    "valor": normalize_text(item.get("valor")),
                    "quantidade": _quantidade(item),
                    "forma_pagamento": normalize_text(item.get("forma_pagamento")) or "Mensal",
                    "vencimento": normalize_text(item.get("vencimento")),
                    "amount": _service_amount(item),
                    "unit_amount": parse_money(item.get("valor")),
                    "payments": payments,
                    "billing_forma": normalize_text(billing.get("forma")),
                }
            )
    return rows


def build_internal_forecast(
    start: date,
    end: date,
    *,
    tenant_id: str | None = DEFAULT_TENANT_ID,
    company_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    names = company_names or {}
    lines: list[dict[str, Any]] = []
    by_forma: dict[str, float] = defaultdict(float)
    faturamento = 0.0

    for row in collect_company_services(tenant_id):
        dates = occurrences_in_period(row, start, end)
        if not dates:
            continue
        total = row["amount"] * len(dates)
        faturamento += total
        forma = row["forma_pagamento"] or "—"
        by_forma[forma] += total
        empresa = names.get(row["sheet_row"]) or f"Cadastro {row['sheet_row']}"
        for occ in dates:
            lines.append(
                {
                    "sheet_row": row["sheet_row"],
                    "empresa": empresa,
                    "cliente_href": f"/cadastro/todos/{row['sheet_row']}/editar?tab=financeiro",
                    "servico": row["servico"],
                    "quantidade": row["quantidade"],
                    "unit_amount": row["unit_amount"],
                    "valor_unit": format_brl(row["unit_amount"]),
                    "valor": row["amount"],
                    "valor_label": format_brl(row["amount"]),
                    "forma": forma,
                    "vencimento": occ,
                    "vencimento_label": occ.strftime("%d/%m/%Y"),
                }
            )

    recebido = 0.0
    a_receber = 0.0
    seen_payments: set[tuple[int, str, str, str]] = set()
    for row in collect_company_services(tenant_id):
        for pay in row.get("payments") or []:
            if not isinstance(pay, dict):
                continue
            pay_date = parse_iso_date(pay.get("data"))
            if not pay_date or pay_date < start or pay_date > end:
                continue
            key = (row["sheet_row"], normalize_text(pay.get("data")), normalize_text(pay.get("descricao")), normalize_text(pay.get("valor")))
            if key in seen_payments:
                continue
            seen_payments.add(key)
            amount = _payment_amount(pay)
            status = normalize_text(pay.get("status")).lower()
            if status == "pago":
                recebido += amount
            elif status in {"pendente", "atrasado"}:
                a_receber += amount

    if a_receber == 0 and recebido == 0:
        a_receber = faturamento

    forma_cards = [
        {
            "label": f"Forma: {name}",
            "value": format_brl(total),
            "note": "Serviços internos no período",
            "tone": "blue",
            "icon": "💳",
        }
        for name, total in sorted(by_forma.items(), key=lambda item: item[0].lower())
    ]
    if not forma_cards:
        forma_cards = [
            {
                "label": "Valores por forma de pagamento",
                "value": format_brl(0),
                "note": "Nenhum serviço no período",
                "tone": "blue",
                "icon": "💳",
            }
        ]

    lines.sort(key=lambda item: (item["vencimento"], item["empresa"], item["servico"]))
    return {
        "period_start": start,
        "period_end": end,
        "faturamento": faturamento,
        "faturamento_label": format_brl(faturamento),
        "recebimentos": a_receber + recebido,
        "recebimentos_label": format_brl(a_receber + recebido),
        "recebido": recebido,
        "recebido_label": format_brl(recebido),
        "a_receber": a_receber,
        "a_receber_label": format_brl(a_receber),
        "forma_totals": [{"name": name, "total": total, "total_label": format_brl(total)} for name, total in sorted(by_forma.items())],
        "forma_cards": forma_cards,
        "lines": lines,
        "companies_count": len({line["sheet_row"] for line in lines}),
    }


def internal_kpi_cards(forecast: dict[str, Any]) -> list[dict]:
    cards = [
        {
            "label": "Previsão de Faturamento",
            "value": forecast["faturamento_label"],
            "note": f"{forecast['companies_count']} empresa(s) com serviços no período",
            "tone": "purple",
            "icon": "📈",
        },
        {
            "label": "Previsão de Recebimentos",
            "value": forecast["recebimentos_label"],
            "note": f"Recebido {forecast['recebido_label']} · a receber {forecast['a_receber_label']}",
            "tone": "green",
            "icon": "👛",
        },
    ]
    cards.extend(forecast["forma_cards"])
    return cards


def group_value_bands(lines: list[dict], payments_by_value: list[dict] | None = None) -> list[dict]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"recebido": 0.0, "a_receber": 0.0})
    for line in lines:
        key = f"{line.get('unit_amount', line.get('valor', 0)):.2f}"
        grouped[key]["a_receber"] += float(line.get("valor") or 0)
    for pay in payments_by_value or []:
        amount = float(pay.get("amount") or 0)
        key = f"{amount:.2f}"
        if pay.get("status") == "pago":
            grouped[key]["recebido"] += amount
        else:
            grouped[key]["a_receber"] += amount
    rows = []
    for key in sorted(grouped.keys(), key=lambda v: float(v)):
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


def plan_breakdown(lines: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for line in lines:
        valor_key = format_brl(line.get("valor") or 0)
        forma = line.get("forma") or "—"
        key = (valor_key, forma)
        bucket = grouped.setdefault(key, {"valor_label": valor_key, "forma": forma, "count": 0, "total": 0.0})
        bucket["count"] += 1
        bucket["total"] += float(line.get("valor") or 0)
    rows = []
    for item in sorted(grouped.values(), key=lambda row: (-row["count"], row["valor_label"])):
        rows.append(
            {
                **item,
                "total_label": format_brl(item["total"]),
                "summary": f"{item['count']} plano{'s' if item['count'] != 1 else ''} {item['valor_label'].replace('R$ ', '')} {item['forma']}",
            }
        )
    return rows
