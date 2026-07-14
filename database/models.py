from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database.connection import Base


def utcnow():
    return datetime.utcnow()


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True)
    company_name = Column(String(255), nullable=False)
    trade_name = Column(String(255))
    cnpj = Column(String(20))
    email = Column(String(255))
    phone = Column(String(30))
    logo_url = Column(String(500))
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"))
    subscription_status = Column(String(50), default="active")
    subscription_start = Column(Date)
    subscription_end = Column(Date)
    user_limit = Column(Integer, default=10)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    active = Column(Boolean, default=True)

    users = relationship("User", back_populates="tenant")
    plan = relationship("SubscriptionPlan")


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    monthly_price = Column(Float, default=0)
    user_limit = Column(Integer, default=5)
    lead_limit = Column(Integer, default=1000)
    proposal_limit = Column(Integer, default=500)
    features = Column(Text)
    active = Column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="Vendedor")
    phone = Column(String(30))
    avatar_url = Column(String(500))
    active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)
    last_access = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    tenant = relationship("Tenant", back_populates="users")
    permissions = relationship("Permission", back_populates="user")


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    permission_name = Column(String(100), nullable=False)
    permission_value = Column(Boolean, default=True)

    user = relationship("User", back_populates="permissions")


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    company_name = Column(String(255), nullable=False)
    trade_name = Column(String(255))
    cnpj = Column(String(20))
    niche = Column(String(100))
    state = Column(String(2))
    city = Column(String(100))
    address = Column(String(255))
    website = Column(String(255))
    estimated_revenue = Column(Float)
    number_of_employees = Column(Integer)
    notes = Column(Text)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    stage_order = Column(Integer, nullable=False)
    color = Column(String(20), default="#6D28D9")
    conversion_probability = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class LostReason(Base):
    __tablename__ = "lost_reasons"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    description = Column(Text)
    active = Column(Boolean, default=True)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(30))
    whatsapp = Column(String(30))
    email = Column(String(255))
    position = Column(String(100))
    lead_source = Column(String(100))
    temperature = Column(String(20), default="Morno")
    pipeline_stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), index=True)
    estimated_value = Column(Float, default=0)
    probability = Column(Integer, default=0)
    last_contact = Column(DateTime)
    next_action_date = Column(DateTime)
    next_action_description = Column(String(255))
    status = Column(String(50), default="Aberto")
    lost_reason_id = Column(Integer, ForeignKey("lost_reasons.id"))
    closed_value = Column(Float)
    closed_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    company = relationship("Company")
    stage = relationship("PipelineStage")
    assigned_user = relationship("User")
    lost_reason = relationship("LostReason")


class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    activity_type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    scheduled_date = Column(DateTime)
    completed_date = Column(DateTime)
    status = Column(String(30), default="Pendente")
    priority = Column(String(20), default="Normal")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    lead = relationship("Lead", foreign_keys=[lead_id])
    company = relationship("Company")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    creator = relationship("User", foreign_keys=[created_by_user_id])


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    category = Column(String(100))
    unit_value = Column(Float, default=0)
    recurring = Column(Boolean, default=False)
    billing_cycle = Column(String(30))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Proposal(Base):
    __tablename__ = "proposals"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), index=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    proposal_code = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    subtotal = Column(Float, default=0)
    discount = Column(Float, default=0)
    total_value = Column(Float, default=0)
    payment_terms = Column(Text)
    implementation_deadline = Column(String(100))
    validity_date = Column(Date)
    status = Column(String(50), default="Rascunho")
    pdf_path = Column(String(500))
    sent_at = Column(DateTime)
    approved_at = Column(DateTime)
    rejected_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    lead = relationship("Lead")
    company = relationship("Company")
    items = relationship("ProposalItem", back_populates="proposal")


class ProposalItem(Base):
    __tablename__ = "proposal_items"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=False, index=True)
    service_id = Column(Integer, ForeignKey("services.id"))
    description = Column(Text, nullable=False)
    quantity = Column(Float, default=1)
    unit_value = Column(Float, default=0)
    total_value = Column(Float, default=0)

    proposal = relationship("Proposal", back_populates="items")
    service = relationship("Service")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reference_month = Column(Integer, nullable=False)
    reference_year = Column(Integer, nullable=False)
    revenue_goal = Column(Float, default=0)
    sales_goal = Column(Integer, default=0)
    meetings_goal = Column(Integer, default=0)
    proposals_goal = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class InteractionHistory(Base):
    __tablename__ = "interaction_history"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    previous_value = Column(String(255))
    new_value = Column(String(255))
    created_at = Column(DateTime, default=utcnow)


class Integration(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    integration_type = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    status = Column(String(30), default="desconectado")
    encrypted_credentials = Column(Text)
    webhook_url = Column(String(500))
    last_sync_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class FinancialEntry(Base):
    __tablename__ = "financial_entries"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"))
    type = Column(String(30), nullable=False)
    category = Column(String(100))
    description = Column(Text)
    value = Column(Float, default=0)
    due_date = Column(Date)
    payment_date = Column(Date)
    status = Column(String(30), default="Pendente")
    created_at = Column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    link = Column(String(500))
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
