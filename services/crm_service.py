from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from auth.permissions import lead_scope_user_id
from database.models import Activity, Lead, PipelineStage, Proposal
from database.repositories import (
    add_history,
    create_financial_entry,
    get_activities,
    get_leads,
    get_pipeline_stages,
    get_proposals,
    get_stage_by_name,
    save_model,
    tenant_query,
)


def _open_leads(db: Session, tenant_id: int, user: dict) -> list[Lead]:
    scope = lead_scope_user_id(user)
    leads = get_leads(db, tenant_id, assigned_user_id=scope, status="Aberto")
    return leads


def dashboard_kpis(db: Session, tenant_id: int, user: dict) -> list[dict]:
    scope = lead_scope_user_id(user)
    leads = get_leads(db, tenant_id, assigned_user_id=scope)
    open_leads = [l for l in leads if l.status == "Aberto"]
    closed = [l for l in leads if (l.stage and l.stage.name == "Fechado")]
    negotiation_value = sum(l.estimated_value or 0 for l in open_leads if l.stage and l.stage.name not in ("Fechado", "Perdido"))
    total_opportunities = max(len(open_leads), 1)
    conversion = round((len(closed) / total_opportunities) * 100) if open_leads else 0

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    closed_month = [l for l in closed if l.closed_at and l.closed_at >= month_start]

    return [
        {"label": "Novos Leads", "value": len([l for l in leads if l.stage and l.stage.name == "Novo Lead"]), "note": "no período", "icon": "✦", "tone": "pink"},
        {"label": "Oportunidades", "value": len(open_leads), "note": "em aberto", "icon": "◉", "tone": "purple"},
        {"label": "Valor em Negociação", "value": f"R$ {negotiation_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), "note": "pipeline aberto", "icon": "💰", "tone": "blue"},
        {"label": "Taxa de Conversão", "value": f"{conversion}%", "note": "fechados / oportunidades", "icon": "%", "tone": "green"},
        {"label": "Fechados no mês", "value": len(closed_month), "note": "mês atual", "icon": "✓", "tone": "violet"},
    ]


def funnel_steps(db: Session, tenant_id: int, user: dict, exclude_lost: bool = True) -> list[dict]:
    stages = get_pipeline_stages(db, tenant_id)
    if exclude_lost:
        stages = [s for s in stages if s.name != "Perdido"]
    scope = lead_scope_user_id(user)
    counts = []
    for stage in stages:
        q = tenant_query(db, Lead, tenant_id).filter(Lead.pipeline_stage_id == stage.id)
        if scope:
            q = q.filter(Lead.assigned_user_id == scope)
        counts.append(q.count())

    max_count = max(counts) if counts else 1
    steps = []
    for index, (stage, count) in enumerate(zip(stages, counts)):
        conversion = None
        if index < len(counts) - 1 and count > 0:
            conversion = round((counts[index + 1] / count) * 100)
        steps.append({
            "name": stage.name,
            "count": count,
            "width": max(28, round((count / max(max_count, 1)) * 100)),
            "conversion": conversion,
            "color": stage.color,
            "level": index,
        })
    return steps


def today_actions(db: Session, tenant_id: int, user: dict) -> list[Activity]:
    scope = lead_scope_user_id(user) or user["id"]
    activities = get_activities(db, tenant_id, assigned_user_id=scope, status="Pendente")
    today = datetime.utcnow().date()
    return [a for a in activities if a.scheduled_date and a.scheduled_date.date() == today]


def overdue_activities(db: Session, tenant_id: int, user: dict) -> list[Activity]:
    scope = lead_scope_user_id(user) or user["id"]
    now = datetime.utcnow()
    activities = get_activities(db, tenant_id, assigned_user_id=scope)
    overdue = []
    for activity in activities:
        if activity.status in ("Concluída", "Cancelada"):
            continue
        if activity.scheduled_date and activity.scheduled_date < now:
            activity.status = "Atrasada"
            overdue.append(activity)
    db.commit()
    return overdue


def hot_opportunities(db: Session, tenant_id: int, user: dict, limit: int = 10) -> list[dict]:
    scope = lead_scope_user_id(user)
    leads = get_leads(db, tenant_id, assigned_user_id=scope, status="Aberto")
    leads = sorted(leads, key=lambda l: (l.temperature != "Quente", -(l.estimated_value or 0)))[:limit]
    rows = []
    for lead in leads:
        rows.append({
            "Empresa": lead.company.company_name if lead.company else lead.name,
            "Etapa": lead.stage.name if lead.stage else "—",
            "Valor": lead.estimated_value or 0,
            "Temperatura": lead.temperature or "—",
            "Próxima ação": lead.next_action_description or "—",
            "lead_id": lead.id,
        })
    return rows


def move_lead_stage(
    db: Session,
    tenant_id: int,
    user: dict,
    lead_id: int,
    new_stage_id: int,
    closed_value: float | None = None,
    lost_reason_id: int | None = None,
    observation: str = "",
) -> tuple[bool, str]:
    lead = tenant_query(db, Lead, tenant_id).filter(Lead.id == lead_id).first()
    if not lead:
        return False, "Lead não encontrado."

    new_stage = tenant_query(db, PipelineStage, tenant_id).filter(PipelineStage.id == new_stage_id).first()
    if not new_stage:
        return False, "Etapa inválida."

    previous = lead.stage.name if lead.stage else ""
    lead.pipeline_stage_id = new_stage.id
    lead.updated_at = datetime.utcnow()

    if new_stage.name == "Fechado":
        if not closed_value:
            return False, "Informe o valor final do fechamento."
        lead.status = "Fechado"
        lead.closed_value = closed_value
        lead.closed_at = datetime.utcnow()
        create_financial_entry(
            db,
            tenant_id,
            None,
            "Receita",
            "Venda",
            f"Fechamento do lead {lead.name}",
            closed_value,
            status="Pendente",
        )
        proposal = (
            tenant_query(db, Proposal, tenant_id)
            .filter(Proposal.lead_id == lead.id, Proposal.status.in_(["Enviada", "Em negociação", "Aguardando resposta"]))
            .first()
        )
        if proposal:
            proposal.status = "Aprovada"
            proposal.approved_at = datetime.utcnow()

    elif new_stage.name == "Perdido":
        if not lost_reason_id:
            return False, "Informe o motivo da perda."
        lead.status = "Perdido"
        lead.lost_reason_id = lost_reason_id

    add_history(
        db,
        tenant_id,
        lead.id,
        user["id"],
        "Alteração de etapa",
        f"Lead movido para {new_stage.name}",
        observation,
        previous,
        new_stage.name,
    )
    db.commit()
    return True, f"Lead movido para {new_stage.name}."


def pipeline_kanban(db: Session, tenant_id: int, user: dict) -> dict[int, list[Lead]]:
    stages = [s for s in get_pipeline_stages(db, tenant_id) if s.name not in ("Fechado", "Perdido")]
    scope = lead_scope_user_id(user)
    board: dict[int, list[Lead]] = {}
    for stage in stages:
        q = tenant_query(db, Lead, tenant_id).filter(Lead.pipeline_stage_id == stage.id, Lead.status == "Aberto")
        if scope:
            q = q.filter(Lead.assigned_user_id == scope)
        board[stage.id] = q.order_by(Lead.updated_at.desc()).all()
    return board


def proposals_summary(db: Session, tenant_id: int, user: dict) -> list[dict]:
    scope = lead_scope_user_id(user)
    proposals = get_proposals(db, tenant_id, assigned_user_id=scope)
    month = datetime.utcnow().month
    approved_month = [p for p in proposals if p.status == "Aprovada" and p.approved_at and p.approved_at.month == month]
    total_value = sum(p.total_value or 0 for p in proposals)
    return [
        {"label": "Propostas criadas", "value": len(proposals), "note": "total", "icon": "📄", "tone": "purple"},
        {"label": "Em negociação", "value": len([p for p in proposals if p.status == "Em negociação"]), "note": "ativas", "icon": "↔", "tone": "blue"},
        {"label": "Aguardando resposta", "value": len([p for p in proposals if p.status == "Aguardando resposta"]), "note": "pendentes", "icon": "⏳", "tone": "orange"},
        {"label": "Aprovadas no mês", "value": len(approved_month), "note": "mês atual", "icon": "✓", "tone": "green"},
        {"label": "Valor total", "value": f"R$ {total_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), "note": "em propostas", "icon": "💰", "tone": "pink"},
    ]


def goals_report(db: Session, tenant_id: int, month: int, year: int) -> list[dict]:
    from database.repositories import get_goals, get_users

    goals = get_goals(db, tenant_id, month, year)
    users = {u.id: u for u in get_users(db, tenant_id)}
    rows = []
    for goal in goals:
        user = users.get(goal.user_id)
        closed = (
            tenant_query(db, Lead, tenant_id)
            .join(PipelineStage)
            .filter(
                Lead.assigned_user_id == goal.user_id,
                PipelineStage.name == "Fechado",
                Lead.closed_at.isnot(None),
            )
            .all()
        )
        realized = sum(l.closed_value or l.estimated_value or 0 for l in closed)
        pct = round((realized / goal.revenue_goal) * 100, 1) if goal.revenue_goal else 0
        status = "Acima da meta" if pct >= 100 else "Em linha" if pct >= 80 else "Atenção" if pct >= 60 else "Abaixo da meta"
        rows.append({
            "Vendedor": user.name if user else "—",
            "Meta": goal.revenue_goal,
            "Realizado": realized,
            "Atingimento": f"{pct}%",
            "Status": status,
        })
    return rows
