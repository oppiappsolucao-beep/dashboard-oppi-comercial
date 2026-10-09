"""Resumo do Kanban no período: conversas, leads Raissa, andamento e concluídos.

Suporte nível 1 e 2 usam cinco cards próprios da fila de chamados.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from app.services.legacy_core import normalize_text
from app.services.service_orders import DONE_QUEUE_ID, ENTRY_QUEUE_ID, list_orders_by_sector


def period_bounds(inicio: str, fim: str) -> tuple[str, str]:
    today = date.today()
    start_default = today.replace(day=1).isoformat()
    end_default = today.isoformat()
    start = _day(inicio) or start_default
    end = _day(fim) or end_default
    if start > end:
        start, end = end, start
    return start, end


def _day(value: str) -> str:
    text = normalize_text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            date.fromisoformat(text[:10])
        except ValueError:
            return ""
        return text[:10]
    return ""


def _stamp_day(value: str) -> str:
    text = normalize_text(value)
    return text[:10] if len(text) >= 10 and text[4] == "-" else ""


def support_level(sector_name: str) -> int | None:
    """1 ou 2 quando o texto é Suporte nível 1 ou 2. Os demais ficam de fora."""
    text = _plain_key(sector_name)
    if "suporte" not in text:
        return None
    if re.search(r"nivel\s*1|\bn1\b|suporte\s*1\b", text):
        return 1
    if re.search(r"nivel\s*2|\bn2\b|suporte\s*2\b", text):
        return 2
    return None


def resolve_support_level(sector_name: str, viewer: str = "") -> int | None:
    """Nível do setor aberto, ou do login quando o quadro ainda é o Suporte genérico."""
    level = support_level(sector_name)
    if level:
        return level
    viewer_level = support_level(viewer)
    if not viewer_level:
        return None
    folded = _plain_key(sector_name)
    if "comercial" in folded and "suporte" not in folded:
        return None
    if not folded or "suporte" in folded or "nivel" in folded:
        return viewer_level
    return None


def pick_support_sector(sectors: list[dict], level: int) -> dict | None:
    """Setor do nível. Se só existir Suporte, esse quadro recebe o login do nível."""
    exact = [item for item in sectors if support_level(item.get("name") or "") == level]
    if exact:
        return exact[0]
    for item in sectors:
        if _plain_key(item.get("name") or "") == "suporte":
            return item
    return None


def _plain_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value).lower())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text).strip()


def _touches_period(card: dict, start: str, end: str) -> bool:
    for field in ("created_at", "updated_at", "scheduled_date"):
        day = _stamp_day(card.get(field) or "")
        if day and start <= day <= end:
            return True
    return False


def _top_group(orders: list[dict], field: str) -> tuple[str, int]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for order in orders:
        raw = normalize_text(order.get(field))
        key = _plain_key(raw)
        if not key or key in {"—", "-"}:
            continue
        counts[key] = counts.get(key, 0) + 1
        labels.setdefault(key, raw)
    if not counts:
        return "", 0
    key = sorted(counts, key=lambda item: (-counts[item], labels[item].lower()))[0]
    return labels[key], counts[key]


def support_summary_cards(
    orders: list[dict],
    start: str,
    end: str,
    *,
    level: int,
    andamento_ids: set[str] | None = None,
) -> list[dict]:
    """Cinco cards da fila de Suporte nível 1 ou 2."""
    active = [order for order in orders if normalize_text(order.get("status")) != "cancelada"]
    in_period = [order for order in active if _touches_period(order, start, end)]
    analise = [
        order
        for order in in_period
        if (normalize_text(order.get("queue_id")) or ENTRY_QUEUE_ID) == ENTRY_QUEUE_ID
    ]
    waiting = andamento_ids or set()
    andamento = 0
    concluidos = 0
    for order in active:
        queue_id = normalize_text(order.get("queue_id")) or ENTRY_QUEUE_ID
        if queue_id in waiting and _date_in_period(order, start, end):
            andamento += 1
        done = queue_id == DONE_QUEUE_ID or normalize_text(order.get("status")) == "concluida"
        if done:
            updated = _stamp_day(order.get("updated_at") or "") or _stamp_day(order.get("created_at") or "")
            if updated and start <= updated <= end:
                concluidos += 1
    subject, subject_total = _top_group(in_period, "subject")
    company, company_total = _top_group(in_period, "empresa")
    level_label = f"Suporte nível {level}"
    return [
        {
            "label": "Análise",
            "value": len(analise),
            "note": f"OS que outros setores enviaram para a fila de {level_label}.",
        },
        {
            "label": "Andamento",
            "value": andamento,
            "note": "Chamados em tratamento neste período.",
        },
        {
            "label": "Concluídos",
            "value": concluidos,
            "note": "Chamados concluídos neste período.",
        },
        {
            "label": subject or "Assunto",
            "value": subject_total,
            "note": "Assunto com mais chamados neste período." if subject else "Nenhum assunto neste período.",
        },
        {
            "label": company or "Empresa",
            "value": company_total,
            "note": "Empresa com mais chamados neste período." if company else "Nenhuma empresa neste período.",
        },
    ]


def build_kanban_summary(sector_name: str, inicio: str, fim: str, *, viewer: str = "") -> dict:
    start, end = period_bounds(inicio, fim)
    level = resolve_support_level(sector_name, viewer)
    if level:
        cards = support_summary_cards(
            list_orders_by_sector(sector_name),
            start,
            end,
            level=level,
            andamento_ids=_andamento_queue_ids(sector_name),
        )
        return {
            "inicio": start,
            "fim": end,
            "layout": "suporte",
            "cards": cards,
            "conversations": "—",
            "conversations_note": "",
            "leads": 0,
            "leads_note": "",
            "andamento": cards[1]["value"],
            "andamento_note": cards[1]["note"],
            "concluidos": cards[2]["value"],
            "concluidos_note": cards[2]["note"],
        }
    return {
        "inicio": start,
        "fim": end,
        "layout": "padrao",
        "cards": [],
        "conversations": "—",
        "conversations_note": "",
        "leads": 0,
        "leads_note": "",
        "andamento": 0,
        "andamento_note": "",
        "concluidos": 0,
        "concluidos_note": "",
    }


def _conversations(start: str, end: str) -> tuple[str, str]:
    try:
        from app.services.meta_ads import load_campaign_mirror

        mirror = load_campaign_mirror(start, end)
    except Exception:
        return "—", "Não consegui ler as conversas do Meta neste período."
    if not mirror.get("configured"):
        return "—", "Meta ainda não conectado. O número aparece quando a gestão de tráfego estiver ativa."
    if mirror.get("error"):
        return "—", "Não consegui ler as conversas do Meta neste período."
    label = (mirror.get("totals") or {}).get("conversations_label") or "0"
    return label, "Conversas iniciadas no Meta, no mesmo critério da gestão de tráfego."


def _andamento_queue_ids(sector_name: str) -> set[str]:
    from app.services.org_registry import list_sector_queues, list_sectors

    sector = next(
        (item for item in list_sectors() if normalize_text(item["name"]).lower() == normalize_text(sector_name).lower()),
        None,
    )
    if sector is None:
        return set()
    found = set()
    for queue in list_sector_queues(sector["id"]):
        name = normalize_text(queue["name"]).lower()
        if name in {"andamento", "em andamento"}:
            found.add(queue["id"])
    return found


def _date_in_period(card: dict, start: str, end: str) -> bool:
    day = _stamp_day(card.get("scheduled_date") or "")
    return bool(day) and start <= day <= end


def _orders(sector_name: str, start: str, end: str) -> tuple[int, int]:
    if not normalize_text(sector_name):
        return 0, 0
    andamento_ids = _andamento_queue_ids(sector_name)
    andamento = 0
    concluidos = 0
    for card in list_orders_by_sector(sector_name):
        campaign = card.get("queue_id") == "campanha" or normalize_text(card.get("created_by")) == "Leads Raissa"
        lead_day = _stamp_day(card.get("scheduled_date") or "")
        created = _stamp_day(card.get("created_at") or "") or lead_day
        updated = _stamp_day(card.get("updated_at") or "") or created
        if card.get("queue_id") in andamento_ids and _date_in_period(card, start, end):
            andamento += 1
        if card.get("queue_id") == DONE_QUEUE_ID or card.get("status") == "concluida":
            if campaign:
                if (not lead_day) or (start <= lead_day <= end):
                    concluidos += 1
            elif start <= updated <= end:
                concluidos += 1
    return andamento, concluidos
