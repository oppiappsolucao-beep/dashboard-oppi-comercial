from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy.orm import Query, Session

from database.models import (
    Activity,
    Company,
    FinancialEntry,
    Goal,
    Integration,
    InteractionHistory,
    Lead,
    LostReason,
    Notification,
    Permission,
    PipelineStage,
    Proposal,
    ProposalItem,
    Service,
    SubscriptionPlan,
    Tenant,
    User,
)

ModelT = TypeVar("ModelT")


def tenant_query(db: Session, model: type[ModelT], tenant_id: int) -> Query:
    if not hasattr(model, "tenant_id"):
        raise ValueError(f"Model {model.__name__} não possui tenant_id")
    return db.query(model).filter(model.tenant_id == tenant_id)


def get_tenant(db: Session, tenant_id: int) -> Tenant | None:
    return db.query(Tenant).filter(Tenant.id == tenant_id, Tenant.active.is_(True)).first()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower().strip(), User.active.is_(True)).first()


def get_user_by_login(db: Session, login: str) -> User | None:
    value = login.strip().lower()
    if not value:
        return None

    user = db.query(User).filter(User.email == value, User.active.is_(True)).first()
    if user:
        return user

    user = db.query(User).filter(User.name.ilike(value), User.active.is_(True)).first()
    if user:
        return user

    if "@" not in value:
        user = db.query(User).filter(User.email.ilike(f"{value}@%"), User.active.is_(True)).first()
    return user


def get_users(db: Session, tenant_id: int, active_only: bool = True) -> list[User]:
    q = tenant_query(db, User, tenant_id)
    if active_only:
        q = q.filter(User.active.is_(True))
    return q.order_by(User.name).all()


def get_pipeline_stages(db: Session, tenant_id: int) -> list[PipelineStage]:
    return (
        tenant_query(db, PipelineStage, tenant_id)
        .filter(PipelineStage.active.is_(True))
        .order_by(PipelineStage.stage_order)
        .all()
    )


def get_stage_by_name(db: Session, tenant_id: int, name: str) -> PipelineStage | None:
    return (
        tenant_query(db, PipelineStage, tenant_id)
        .filter(PipelineStage.name == name, PipelineStage.active.is_(True))
        .first()
    )


def get_leads(
    db: Session,
    tenant_id: int,
    assigned_user_id: int | None = None,
    stage_id: int | None = None,
    search: str = "",
    status: str | None = None,
) -> list[Lead]:
    q = tenant_query(db, Lead, tenant_id)
    if assigned_user_id:
        q = q.filter(Lead.assigned_user_id == assigned_user_id)
    if stage_id:
        q = q.filter(Lead.pipeline_stage_id == stage_id)
    if status:
        q = q.filter(Lead.status == status)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            (Lead.name.ilike(term))
            | (Lead.email.ilike(term))
            | (Lead.phone.ilike(term))
        )
    return q.order_by(Lead.updated_at.desc()).all()


def get_lead(db: Session, tenant_id: int, lead_id: int) -> Lead | None:
    return tenant_query(db, Lead, tenant_id).filter(Lead.id == lead_id).first()


def get_companies(db: Session, tenant_id: int, search: str = "") -> list[Company]:
    q = tenant_query(db, Company, tenant_id)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter((Company.company_name.ilike(term)) | (Company.trade_name.ilike(term)))
    return q.order_by(Company.company_name).all()


def get_company(db: Session, tenant_id: int, company_id: int) -> Company | None:
    return tenant_query(db, Company, tenant_id).filter(Company.id == company_id).first()


def get_activities(
    db: Session,
    tenant_id: int,
    assigned_user_id: int | None = None,
    status: str | None = None,
) -> list[Activity]:
    q = tenant_query(db, Activity, tenant_id)
    if assigned_user_id:
        q = q.filter(Activity.assigned_user_id == assigned_user_id)
    if status:
        q = q.filter(Activity.status == status)
    return q.order_by(Activity.scheduled_date.asc().nullslast()).all()


def get_proposals(db: Session, tenant_id: int, assigned_user_id: int | None = None) -> list[Proposal]:
    q = tenant_query(db, Proposal, tenant_id)
    if assigned_user_id:
        q = q.filter(Proposal.assigned_user_id == assigned_user_id)
    return q.order_by(Proposal.created_at.desc()).all()


def get_services(db: Session, tenant_id: int) -> list[Service]:
    return tenant_query(db, Service, tenant_id).filter(Service.active.is_(True)).order_by(Service.name).all()


def get_goals(db: Session, tenant_id: int, month: int, year: int) -> list[Goal]:
    return (
        tenant_query(db, Goal, tenant_id)
        .filter(Goal.reference_month == month, Goal.reference_year == year)
        .all()
    )


def get_lost_reasons(db: Session, tenant_id: int) -> list[LostReason]:
    return tenant_query(db, LostReason, tenant_id).filter(LostReason.active.is_(True)).all()


def get_integrations(db: Session, tenant_id: int) -> list[Integration]:
    return tenant_query(db, Integration, tenant_id).order_by(Integration.name).all()


def get_notifications(db: Session, tenant_id: int, user_id: int, unread_only: bool = False) -> list[Notification]:
    q = tenant_query(db, Notification, tenant_id).filter(Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.read.is_(False))
    return q.order_by(Notification.created_at.desc()).all()


def get_history(db: Session, tenant_id: int, lead_id: int) -> list[InteractionHistory]:
    return (
        tenant_query(db, InteractionHistory, tenant_id)
        .filter(InteractionHistory.lead_id == lead_id)
        .order_by(InteractionHistory.created_at.desc())
        .all()
    )


def add_history(
    db: Session,
    tenant_id: int,
    lead_id: int,
    user_id: int,
    event_type: str,
    title: str,
    description: str = "",
    previous_value: str = "",
    new_value: str = "",
) -> InteractionHistory:
    item = InteractionHistory(
        tenant_id=tenant_id,
        lead_id=lead_id,
        user_id=user_id,
        event_type=event_type,
        title=title,
        description=description,
        previous_value=previous_value,
        new_value=new_value,
    )
    db.add(item)
    return item


def add_notification(
    db: Session,
    tenant_id: int,
    user_id: int,
    title: str,
    description: str = "",
    link: str = "",
) -> Notification:
    item = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
        description=description,
        link=link,
    )
    db.add(item)
    return item


def create_financial_entry(
    db: Session,
    tenant_id: int,
    proposal_id: int | None,
    entry_type: str,
    category: str,
    description: str,
    value: float,
    due_date=None,
    status: str = "Pendente",
) -> FinancialEntry:
    entry = FinancialEntry(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        type=entry_type,
        category=category,
        description=description,
        value=value,
        due_date=due_date,
        status=status,
    )
    db.add(entry)
    return entry


def count_leads_by_stage(db: Session, tenant_id: int, stage_id: int) -> int:
    return tenant_query(db, Lead, tenant_id).filter(Lead.pipeline_stage_id == stage_id).count()


def get_permissions(db: Session, tenant_id: int, user_id: int) -> dict[str, bool]:
    rows = tenant_query(db, Permission, tenant_id).filter(Permission.user_id == user_id).all()
    return {row.permission_name: row.permission_value for row in rows}


def save_model(db: Session, obj: Any) -> Any:
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_model(db: Session, obj: Any) -> None:
    db.delete(obj)
    db.commit()


def touch_user_access(db: Session, user: User) -> None:
    user.last_access = datetime.utcnow()
    db.commit()
