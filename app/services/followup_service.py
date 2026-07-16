"""Painel operacional da Visão Geral — regras automáticas de follow-up comercial."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from app.services.filters import DashboardFilters, apply_dashboard_filters
from app.services.lead_actions_storage import DEFAULT_TENANT_ID, get_lead_action
from app.services.legacy_core import (
    as_python_date,
    as_python_datetime,
    normalize_digits,
    normalize_text,
    safe_series,
    status_group,
)

COMPLETED_STATUSES = {"Fechado", "Sem interesse"}
FIRST_CONTACT_STATUSES = {
    "Chamado Whats",
    "Ligação - Conversando Whats",
    "Ligação",
    "Conversando",
    "Reunião",
    "Proposta",
    "Retornar",
    "Ligação retornar",
    "Sem Resposta",
}

STAGE_MAP = {
    "Novo Lead": ["Novo Lead"],
    "Primeiro Contato": ["Chamado Whats", "Ligação - Conversando Whats", "Ligação"],
    "Qualificação": ["Conversando"],
    "Reunião": ["Reunião"],
    "Proposta": ["Proposta"],
    "Negociação": ["Retornar", "Ligação retornar"],
    "Fechado": ["Fechado", "Sem interesse"],
}

PROCESS_GUIDE = [
    ("Novo Lead", "Até 1 hora"),
    ("Contato", "No mesmo dia"),
    ("Qualificação", "1 a 3 dias"),
    ("Reunião", "Até 7 dias"),
    ("Proposta", "Até 24 horas"),
    ("Retorno", "2 dias"),
    ("Negociação", "3 a 7 dias"),
    ("Fechado", "Processo concluído"),
]

PRIORITY_SCORE = {
    "critico": 100,
    "atrasado": 80,
    "vence_hoje": 60,
    "alta": 50,
    "normal": 20,
    "futuro": 10,
}

PRIORITY_LABEL = {
    "critico": "Crítico",
    "atrasado": "Atrasado",
    "vence_hoje": "Vence hoje",
    "alta": "Alta",
    "normal": "Normal",
    "futuro": "Futuro",
}

STATUS_VISUAL = {
    "no_prazo": ("No prazo", "status-green"),
    "agendado": ("Agendado", "status-blue"),
    "vence_hoje": ("Vence hoje", "status-yellow"),
    "atencao": ("Atenção", "status-yellow"),
    "alta": ("Alta prioridade", "status-orange"),
    "atrasado": ("Atrasado", "status-red"),
    "critico": ("Crítico", "status-red"),
}


@dataclass
class OperationalFilters:
    priority: str = "Todas"
    stage: str = "Todas as etapas"
    action_type: str = "Todos os tipos"
    status: str = "Todos"
    channel: str = "Todos os canais"
    overdue_only: bool = False
    no_next_action_only: bool = False


def _now() -> datetime:
    return datetime.now()


def _as_date(value) -> date | None:
    return as_python_date(value)


def _as_datetime(value) -> datetime | None:
    return as_python_datetime(value)


def _minutes_since(value) -> int | None:
    dt = _as_datetime(value)
    if not dt:
        return None
    return max(0, int((_now() - dt).total_seconds() // 60))


def _days_since(value) -> int | None:
    d = _as_date(value)
    if not d:
        return None
    return max(0, (date.today() - d).days)


def _format_relative_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"Há {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"Há {hours} h"
    days = hours // 24
    return f"Há {days} dia{'s' if days != 1 else ''}"


def _format_last_interaction(value) -> str:
    dt = _as_datetime(value)
    if not dt:
        return "Nenhuma"
    today = date.today()
    d = dt.date()
    time_part = dt.strftime("%H:%M")
    if d == today:
        return f"Hoje, {time_part}"
    if d == today - timedelta(days=1):
        return f"Ontem, {time_part}"
    return dt.strftime("%d/%m/%Y, %H:%M")


def _format_deadline(value: date | None, label: str = "") -> str:
    if label:
        return label
    if not value:
        return "—"
    today = date.today()
    if value == today:
        return "Hoje"
    if value == today + timedelta(days=1):
        return "Amanhã"
    if value < today:
        days = (today - value).days
        return f"Atrasado ({days} dia{'s' if days != 1 else ''})"
    return value.strftime("%d/%m/%Y")


def _stage_for_status(grouped: str) -> str:
    for stage, statuses in STAGE_MAP.items():
        if grouped in statuses:
            return stage
    return "Qualificação"


def _phone_for_row(row, columns: dict) -> str:
    for key in ("telefone_b2b", "telefone_socio_1", "telefone_fixo", "telefone_alternativo"):
        column = columns.get(key)
        if column and column in row.index:
            value = normalize_text(row.get(column, ""))
            if value:
                return value
    return normalize_text(row.get("_telefone", ""))


def _email_for_row(row, columns: dict) -> str:
    for key in ("email", "email_socio_1"):
        column = columns.get(key)
        if column and column in row.index:
            value = normalize_text(row.get(column, ""))
            if value:
                return value
    return ""


def _whatsapp_href(phone: str) -> str:
    digits = normalize_digits(phone)
    if not digits:
        return ""
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = f"55{digits}"
    return f"https://wa.me/{digits}" if len(digits) >= 12 else ""


def _proposal_value(row, columns: dict) -> float:
    column = columns.get("valor_proposta")
    if column and column in row.index:
        raw = normalize_text(row.get(column, ""))
        if raw:
            from app.services.legacy_core import parse_money
            return float(parse_money(raw) or 0)
    return float(row.get("_capital_num") or 0)


def _lead_record(row, columns: dict, tenant_id: str | None = None) -> dict:
    grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
    sheet_row = int(row.get("_sheet_row", 0) or 0)
    created = row.get("_data_chamado")
    last_contact = row.get("_ultima_atualizacao") or row.get("_data_chamado")
    stored = get_lead_action(tenant_id, sheet_row) or {}

    next_action_date = None
    next_action_time = normalize_text(stored.get("next_action_time")) or "09:00"
    if stored.get("next_action_date"):
        try:
            next_action_date = date.fromisoformat(str(stored["next_action_date"])[:10])
        except ValueError:
            next_action_date = None

    next_action_completed = bool(stored.get("next_action_completed"))
    has_next_action = bool(next_action_date and not next_action_completed)

    return {
        "sheet_row": sheet_row,
        "empresa": normalize_text(row.get("_empresa", "")) or "—",
        "vendedor": normalize_text(row.get("_vendedor", "")) or "Sem vendedor",
        "grouped_status": grouped,
        "stage": _stage_for_status(grouped),
        "created_at": created,
        "last_contact_at": last_contact,
        "stage_entered_at": last_contact,
        "estimated_value": _proposal_value(row, columns),
        "phone": _phone_for_row(row, columns),
        "email": _email_for_row(row, columns),
        "next_action_date": next_action_date,
        "next_action_time": next_action_time,
        "next_action_type": normalize_text(stored.get("next_action_type")),
        "next_action_description": normalize_text(stored.get("next_action_description")),
        "has_next_action": has_next_action,
        "next_action_completed": next_action_completed,
        "stored": stored,
        "href": f"/cadastro/todos/{sheet_row}" if sheet_row else "/cadastro/todos",
    }


def calcular_prazo_etapa(stage: str) -> str:
    for name, sla in PROCESS_GUIDE:
        if name.lower().startswith(stage.lower()[:4]):
            return sla
    return "—"


def calcular_status_sla(rule_code: str, priority_key: str) -> tuple[str, str]:
    if priority_key in {"critico", "atrasado"}:
        return STATUS_VISUAL.get(priority_key, STATUS_VISUAL["atrasado"])
    if priority_key == "vence_hoje":
        return STATUS_VISUAL["vence_hoje"]
    if priority_key == "alta":
        return STATUS_VISUAL["alta"]
    if rule_code in {"retorno_hoje", "reuniao_proxima"}:
        return STATUS_VISUAL["agendado"]
    return STATUS_VISUAL["no_prazo"]


def calcular_prioridade_lead(priority_key: str) -> int:
    return PRIORITY_SCORE.get(priority_key, 20)


def _action_buttons(lead: dict, suggested: str, channel: str = "") -> list[dict]:
    buttons = []
    phone = lead.get("phone", "")
    email = lead.get("email", "")
    wa = _whatsapp_href(phone)

    if suggested in {"Fazer primeiro contato", "Retomar qualificação", "Realizar follow-up da proposta"} or channel == "whatsapp":
        if wa:
            buttons.append({"label": "Chamar", "href": wa, "tone": "green", "external": True})
    if suggested in {"Agendar retorno", "Agendar reunião", "Definir próximo passo", "Definir próxima ação"}:
        buttons.append({"label": "Agendar", "href": f"{lead['href']}/editar", "tone": "blue"})
    if suggested == "Criar proposta":
        buttons.append({"label": "Criar proposta", "href": "/propostas", "tone": "purple"})
    if suggested in {"Confirmar reunião", "Realizar follow-up da proposta", "Retomar qualificação"}:
        if wa:
            buttons.append({"label": "Retornar", "href": wa, "tone": "green", "external": True})
        elif phone:
            buttons.append({"label": "Ligar", "href": f"tel:{normalize_digits(phone)}", "tone": "green"})
    if channel == "email" and email:
        buttons.append({"label": "Enviar e-mail", "href": f"mailto:{email}", "tone": "blue", "external": True})
    if channel == "reuniao":
        buttons.append({"label": "Abrir reunião", "href": lead["href"], "tone": "blue"})

    if lead.get("has_next_action") and not lead.get("next_action_completed"):
        buttons.append({
            "label": "Concluir",
            "href": f"/visao-geral/acoes/{lead['sheet_row']}/concluir",
            "tone": "purple",
        })

    if not buttons:
        buttons.append({"label": "Abrir", "href": lead["href"], "tone": "blue"})
    return buttons[:3]


def _append_action(
    actions: list[dict],
    *,
    lead: dict,
    rule_code: str,
    priority_key: str,
    suggested_action: str,
    deadline_label: str = "",
    channel: str = "",
) -> None:
    status_label, status_class = calcular_status_sla(rule_code, priority_key)
    actions.append({
        "rule_code": rule_code,
        "priority_key": priority_key,
        "priority_label": PRIORITY_LABEL.get(priority_key, "Normal"),
        "priority_score": calcular_prioridade_lead(priority_key),
        "priority_class": priority_key,
        "empresa": lead["empresa"],
        "stage": lead["stage"],
        "last_interaction": _format_last_interaction(lead["last_contact_at"]),
        "deadline_label": _format_deadline(lead.get("next_action_date"), deadline_label),
        "next_action": suggested_action,
        "owner": lead["vendedor"],
        "status_label": status_label,
        "status_class": status_class,
        "buttons": _action_buttons(lead, suggested_action, channel),
        "sheet_row": lead["sheet_row"],
        "href": lead["href"],
    })


def _evaluate_lead_rules(lead: dict) -> list[dict]:
    if lead["grouped_status"] in COMPLETED_STATUSES:
        return []

    grouped = lead["grouped_status"]
    stage = lead["stage"]
    actions: list[dict] = []
    minutes = _minutes_since(lead["created_at"])
    days_idle = _days_since(lead["last_contact_at"]) or 0
    today = date.today()
    tomorrow = today + timedelta(days=1)

    # REGRA 4 — retorno atrasado (storage)
    if lead["has_next_action"] and lead["next_action_date"] and lead["next_action_date"] < today:
        _append_action(
            actions,
            lead=lead,
            rule_code="retorno_atrasado",
            priority_key="atrasado",
            suggested_action=lead["next_action_description"] or "Retornar contato",
            deadline_label="Atrasado",
            channel=lead["next_action_type"],
        )
        return actions

    # REGRA 3 — retorno hoje
    if lead["has_next_action"] and lead["next_action_date"] == today:
        _append_action(
            actions,
            lead=lead,
            rule_code="retorno_hoje",
            priority_key="vence_hoje",
            suggested_action=lead["next_action_description"] or "Retornar contato",
            deadline_label=f"Hoje, {lead['next_action_time']}",
            channel=lead["next_action_type"],
        )
        return actions

    # REGRA 1 — lead novo sem contato
    if grouped == "Novo Lead" and grouped not in FIRST_CONTACT_STATUSES:
        if minutes is not None and minutes >= 5:
            if minutes >= 60:
                priority = "atrasado"
            elif minutes >= 30:
                priority = "alta"
            else:
                priority = "normal"
            _append_action(
                actions,
                lead=lead,
                rule_code="lead_novo_sem_contato",
                priority_key=priority,
                suggested_action="Fazer primeiro contato",
                deadline_label=_format_relative_minutes(minutes),
                channel="whatsapp",
            )
            return actions

    # REGRA 7 — proposta não enviada após reunião
    if grouped == "Reunião" and days_idle >= 1:
        _append_action(
            actions,
            lead=lead,
            rule_code="proposta_nao_enviada",
            priority_key="alta" if days_idle >= 2 else "vence_hoje",
            suggested_action="Criar proposta",
            deadline_label="Atrasado" if days_idle >= 2 else "Até 24 horas",
        )
        return actions

    # REGRA 6 — reunião próxima
    if grouped == "Reunião" and days_idle <= 1:
        _append_action(
            actions,
            lead=lead,
            rule_code="reuniao_proxima",
            priority_key="vence_hoje",
            suggested_action="Confirmar reunião",
            deadline_label="Hoje ou amanhã",
            channel="reuniao",
        )
        return actions

    # REGRA 8 — proposta sem follow-up
    if grouped == "Proposta" and days_idle >= 2:
        priority = "critico" if days_idle >= 5 else "atrasado" if days_idle >= 3 else "alta"
        _append_action(
            actions,
            lead=lead,
            rule_code="proposta_sem_followup",
            priority_key=priority,
            suggested_action="Realizar follow-up da proposta",
            deadline_label=f"SLA 2 dias ({days_idle}d)",
            channel="whatsapp",
        )
        return actions

    # REGRA 9 — negociação parada
    if stage == "Negociação" and days_idle >= 3:
        if days_idle >= 7:
            priority = "critico"
        elif days_idle >= 6:
            priority = "alta"
        else:
            priority = "atencao" if days_idle <= 5 else "alta"
        priority_key = "critico" if days_idle >= 7 else "alta" if days_idle >= 6 else "alta"
        _append_action(
            actions,
            lead=lead,
            rule_code="negociacao_parada",
            priority_key=priority_key,
            suggested_action="Retomar negociação",
            deadline_label=f"{days_idle} dias parado",
            channel="whatsapp",
        )
        return actions

    # REGRA 5 — qualificação parada
    if stage == "Qualificação" and days_idle >= 3:
        priority_key = "critico" if days_idle >= 7 else "alta"
        _append_action(
            actions,
            lead=lead,
            rule_code="qualificacao_parada",
            priority_key=priority_key,
            suggested_action="Retomar qualificação",
            deadline_label=f"{days_idle} dias na etapa",
            channel="whatsapp",
        )
        return actions

    # REGRA 2 — primeiro contato sem próxima ação
    if grouped in FIRST_CONTACT_STATUSES and grouped != "Novo Lead" and not lead["has_next_action"]:
        _append_action(
            actions,
            lead=lead,
            rule_code="sem_proxima_acao_pos_contato",
            priority_key="alta",
            suggested_action="Definir próximo passo",
            deadline_label="No mesmo dia",
        )
        return actions

    # REGRA 10 — lead sem próxima ação
    if not lead["has_next_action"] and grouped not in COMPLETED_STATUSES:
        _append_action(
            actions,
            lead=lead,
            rule_code="lead_sem_proxima_acao",
            priority_key="normal",
            suggested_action="Definir próxima ação",
            deadline_label="Pendente",
        )
        return actions

    return actions


def buscar_leads_para_acao(
    filtered_df: pd.DataFrame,
    columns: dict,
    operational: OperationalFilters | None = None,
    tenant_id: str | None = None,
) -> list[dict]:
    operational = operational or OperationalFilters()
    actions: list[dict] = []

    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        actions.extend(_evaluate_lead_rules(lead))

    actions.sort(key=lambda item: (-item["priority_score"], item["empresa"]))

    if operational.priority != "Todas":
        actions = [a for a in actions if a["priority_label"] == operational.priority]
    if operational.stage != "Todas as etapas":
        actions = [a for a in actions if a["stage"] == operational.stage]
    if operational.overdue_only:
        actions = [a for a in actions if a["priority_key"] in {"atrasado", "critico"}]
    if operational.no_next_action_only:
        actions = [a for a in actions if a["rule_code"] in {"lead_sem_proxima_acao", "sem_proxima_acao_pos_contato"}]

    return actions


def buscar_retornos_de_hoje(
    filtered_df: pd.DataFrame,
    columns: dict,
    tenant_id: str | None = None,
) -> list[dict]:
    today = date.today()
    items = []

    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        if lead["grouped_status"] in COMPLETED_STATUSES:
            continue
        if not lead["has_next_action"] or lead["next_action_date"] != today:
            continue

        channel = lead["next_action_type"] or "whatsapp"
        channel_label = {
            "whatsapp": "WhatsApp",
            "ligacao": "Ligação",
            "email": "E-mail",
            "reuniao": "Reunião",
        }.get(channel, "WhatsApp")

        items.append({
            "empresa": lead["empresa"],
            "time": lead["next_action_time"],
            "channel": channel_label,
            "channel_key": channel,
            "description": lead["next_action_description"] or "Retorno agendado",
            "owner": lead["vendedor"],
            "sheet_row": lead["sheet_row"],
            "href": lead["href"],
            "buttons": _action_buttons(lead, lead["next_action_description"] or "Retornar", channel),
        })

    items.sort(key=lambda item: item["time"])
    return items


def buscar_reunioes_proximas(filtered_df: pd.DataFrame, columns: dict, tenant_id: str | None = None) -> int:
    count = 0
    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        if lead["grouped_status"] == "Reunião" and (_days_since(lead["last_contact_at"]) or 99) <= 1:
            count += 1
    return count


def buscar_propostas_sem_followup(filtered_df: pd.DataFrame, columns: dict, tenant_id: str | None = None) -> int:
    count = 0
    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        if lead["grouped_status"] == "Proposta" and (_days_since(lead["last_contact_at"]) or 0) >= 2:
            count += 1
    return count


def buscar_negociacoes_paradas(filtered_df: pd.DataFrame, columns: dict, tenant_id: str | None = None) -> int:
    count = 0
    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        if lead["stage"] == "Negociação" and (_days_since(lead["last_contact_at"]) or 0) >= 3:
            count += 1
    return count


def buscar_leads_sem_proxima_acao(filtered_df: pd.DataFrame, columns: dict, tenant_id: str | None = None) -> int:
    count = 0
    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        if lead["grouped_status"] in COMPLETED_STATUSES:
            continue
        if not lead["has_next_action"]:
            count += 1
    return count


def montar_alertas_processo(
    filtered_df: pd.DataFrame,
    columns: dict,
    tenant_id: str | None = None,
) -> list[dict]:
    today = date.today()
    alerts = []

    novos_sem_contato = 0
    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        minutes = _minutes_since(lead["created_at"])
        if lead["grouped_status"] == "Novo Lead" and minutes is not None and minutes >= 60:
            novos_sem_contato += 1
    if novos_sem_contato:
        alerts.append({
            "label": f"{novos_sem_contato} lead{'s' if novos_sem_contato != 1 else ''} novo{'s' if novos_sem_contato != 1 else ''} sem contato há mais de 1 hora",
            "filter": "overdue_only=1",
            "tone": "red",
        })

    propostas = buscar_propostas_sem_followup(filtered_df, columns, tenant_id)
    if propostas:
        alerts.append({
            "label": f"{propostas} proposta{'s' if propostas != 1 else ''} sem follow-up há mais de 2 dias",
            "filter": "stage=Proposta",
            "tone": "orange",
        })

    negociacoes = buscar_negociacoes_paradas(filtered_df, columns, tenant_id)
    if negociacoes:
        alerts.append({
            "label": f"{negociacoes} negociaç{'ões' if negociacoes != 1 else 'ão'} parada{'s' if negociacoes != 1 else ''} há mais de 3 dias",
            "filter": "stage=Negociação",
            "tone": "orange",
        })

    sem_acao = buscar_leads_sem_proxima_acao(filtered_df, columns, tenant_id)
    if sem_acao:
        alerts.append({
            "label": f"{sem_acao} lead{'s' if sem_acao != 1 else ''} sem próxima ação",
            "filter": "no_next_action_only=1",
            "tone": "yellow",
        })

    reunioes_sem_confirmacao = 0
    for _, row in filtered_df.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        if lead["grouped_status"] == "Reunião" and not lead["has_next_action"]:
            reunioes_sem_confirmacao += 1
    if reunioes_sem_confirmacao:
        alerts.append({
            "label": f"{reunioes_sem_confirmacao} reuni{'ões' if reunioes_sem_confirmacao != 1 else 'ão'} sem confirmação",
            "filter": "stage=Reunião",
            "tone": "blue",
        })

    retornos_hoje = len(buscar_retornos_de_hoje(filtered_df, columns, tenant_id))
    if retornos_hoje:
        alerts.append({
            "label": f"{retornos_hoje} retorno{'s' if retornos_hoje != 1 else ''} agendado{'s' if retornos_hoje != 1 else ''} para hoje ({today.strftime('%d/%m')})",
            "filter": "",
            "tone": "blue",
        })

    return alerts


def build_operational_kpi_cards(
    df: pd.DataFrame,
    columns: dict,
    filters: DashboardFilters,
    tenant_id: str | None = None,
) -> list[dict]:
    from app.services.legacy_core import apply_period_filter, count_dashboard_status

    scoped = apply_dashboard_filters(df, columns, filters)
    today = date.today()
    month_start = today.replace(day=1)

    novos_hoje = 0
    for _, row in scoped.iterrows():
        d = _as_date(row.get("_data_chamado"))
        if d == today and status_group(row.get("_status_grupo", "")) == "Novo Lead":
            novos_hoje += 1

    novos_sem_contato = 0
    for _, row in scoped.iterrows():
        lead = _lead_record(row, columns, tenant_id)
        minutes = _minutes_since(lead["created_at"])
        if lead["grouped_status"] == "Novo Lead" and minutes is not None and minutes >= 5:
            novos_sem_contato += 1

    retornos_hoje = len(buscar_retornos_de_hoje(scoped, columns, tenant_id))
    reunioes = buscar_reunioes_proximas(scoped, columns, tenant_id)
    propostas_followup = buscar_propostas_sem_followup(scoped, columns, tenant_id)

    atrasados = sum(
        1 for item in buscar_leads_para_acao(scoped, columns, tenant_id=tenant_id)
        if item["priority_key"] in {"atrasado", "critico"}
    )

    month_df = apply_period_filter(scoped.copy(), "_data_chamado", (month_start, today))
    fechados_mes = count_dashboard_status(month_df, "Fechado")
    valor_fechado = float(month_df[month_df["_status_grupo"].apply(lambda s: status_group(s) == "Fechado")]["_capital_num"].fillna(0).sum()) if not month_df.empty else 0.0

    def money(value: float) -> str:
        if value <= 0:
            return "R$ 0"
        return f"R$ {value:,.0f}".replace(",", ".")

    return [
        {
            "label": "Novos leads hoje",
            "value": novos_hoje,
            "note": f"{novos_sem_contato} sem contato" if novos_sem_contato else "Todos contatados",
            "note_class": "bad" if novos_sem_contato else "good",
            "icon": "👥",
            "tone": "purple",
        },
        {
            "label": "Retornos de hoje",
            "value": retornos_hoje,
            "note": "Agenda do dia",
            "note_class": "neutral",
            "icon": "↩",
            "tone": "pink",
        },
        {
            "label": "Reuniões próximas",
            "value": reunioes,
            "note": "Hoje e amanhã",
            "note_class": "neutral",
            "icon": "📅",
            "tone": "blue",
        },
        {
            "label": "Propostas sem follow-up",
            "value": propostas_followup,
            "note": "Mais de 2 dias" if propostas_followup else "Em dia",
            "note_class": "bad" if propostas_followup else "good",
            "icon": "📄",
            "tone": "orange",
        },
        {
            "label": "Leads atrasados",
            "value": atrasados,
            "note": "Precisam de ação",
            "note_class": "bad" if atrasados else "good",
            "icon": "⚠",
            "tone": "rose",
        },
        {
            "label": "Fechamentos do mês",
            "value": fechados_mes,
            "note": money(valor_fechado),
            "note_class": "good",
            "icon": "✓",
            "tone": "green",
        },
    ]


def build_funnel_summary(filtered_df: pd.DataFrame) -> list[dict]:
    from app.services.overview import OVERVIEW_FUNNEL_STAGES

    def count_statuses(names: list[str]) -> int:
        if filtered_df.empty:
            return 0
        total = 0
        for _, row in filtered_df.iterrows():
            grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
            if grouped in names:
                total += 1
        return total

    counts = [count_statuses(statuses) for _, statuses, _ in OVERVIEW_FUNNEL_STAGES]
    total = sum(counts) or 1
    return [
        {
            "name": name,
            "count": count,
            "percent": round((count / total) * 100),
            "color": color,
        }
        for (name, _, color), count in zip(OVERVIEW_FUNNEL_STAGES, counts)
    ]


def build_operational_overview_context(
    df: pd.DataFrame,
    columns: dict,
    filters: DashboardFilters,
    operational: OperationalFilters | None = None,
    tenant_id: str | None = None,
) -> dict:
    operational = operational or OperationalFilters()
    scoped = apply_dashboard_filters(df, columns, filters)

    action_queue = buscar_leads_para_acao(scoped, columns, operational, tenant_id)
    returns_today = buscar_retornos_de_hoje(scoped, columns, tenant_id)
    alerts = montar_alertas_processo(scoped, columns, tenant_id)
    funnel_summary = build_funnel_summary(scoped)

    return {
        "kpi_cards": build_operational_kpi_cards(df, columns, filters, tenant_id),
        "process_guide": PROCESS_GUIDE,
        "action_queue": action_queue,
        "returns_today": returns_today,
        "alerts": alerts,
        "funnel_summary": funnel_summary,
        "operational": operational,
        "stage_options": ["Todas as etapas", *STAGE_MAP.keys()],
        "priority_options": ["Todas", "Crítico", "Atrasado", "Vence hoje", "Alta", "Normal"],
    }


def apply_seller_scope(request, filters: DashboardFilters, seller_options: list[str], is_admin_user: bool) -> DashboardFilters:
    if is_admin_user:
        return filters
    username = normalize_text(request.session.get("username", ""))
    if not username:
        return filters
    for seller in seller_options:
        if normalize_text(seller).lower() == username.lower():
            return replace(filters, seller=seller)
    return filters


def parse_operational_filters(request, form: dict | None = None) -> OperationalFilters:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    def as_bool(value) -> bool:
        return str(value).lower() in {"1", "true", "on", "yes"}

    return OperationalFilters(
        priority=data.get("op_priority", "Todas"),
        stage=data.get("op_stage", "Todas as etapas"),
        action_type=data.get("op_action_type", "Todos os tipos"),
        status=data.get("op_status", "Todos"),
        channel=data.get("op_channel", "Todos os canais"),
        overdue_only=as_bool(data.get("overdue_only")),
        no_next_action_only=as_bool(data.get("no_next_action_only")),
    )
