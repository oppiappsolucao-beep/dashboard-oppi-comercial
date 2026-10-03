"""Resumo do Kanban no período: conversas, leads Raissa, andamento e concluídos."""
from __future__ import annotations

from datetime import date

from app.services.legacy_core import normalize_text
from app.services.service_orders import DONE_QUEUE_ID, list_orders_by_sector


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


def build_kanban_summary(sector_name: str, inicio: str, fim: str) -> dict:
    start, end = period_bounds(inicio, fim)
    conversations, conversations_note = _conversations(start, end)
    try:
        from app.services.campaign_leads import count_raissa_leads

        leads, leads_note = count_raissa_leads(start, end)
    except Exception:
        leads, leads_note = 0, "Não consegui ler a aba Leads Raissa."
    andamento, concluidos = _orders(sector_name, start, end)
    return {
        "inicio": start,
        "fim": end,
        "conversations": conversations,
        "conversations_note": conversations_note,
        "leads": leads,
        "leads_note": leads_note,
        "andamento": andamento,
        "andamento_note": "Em aberto neste período. Na Campanha, conta a data do lead.",
        "concluidos": concluidos,
        "concluidos_note": "Ordens deste setor concluídas no período.",
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


def _orders(sector_name: str, start: str, end: str) -> tuple[int, int]:
    if not normalize_text(sector_name):
        return 0, 0
    andamento = 0
    concluidos = 0
    for card in list_orders_by_sector(sector_name):
        campaign = card.get("queue_id") == "campanha" or normalize_text(card.get("created_by")) == "Leads Raissa"
        lead_day = _stamp_day(card.get("scheduled_date") or "")
        created = _stamp_day(card.get("created_at") or "") or lead_day
        updated = _stamp_day(card.get("updated_at") or "") or created
        if campaign:
            in_period = (not lead_day) or (start <= lead_day <= end)
        else:
            in_period = start <= created <= end
        if card.get("queue_id") == DONE_QUEUE_ID or card.get("status") == "concluida":
            if campaign:
                if in_period:
                    concluidos += 1
            elif start <= updated <= end:
                concluidos += 1
            continue
        if in_period:
            andamento += 1
    return andamento, concluidos
