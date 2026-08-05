from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
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


class AttendanceConversation(Base):
    """Inbox WhatsApp — persistido em DATABASE_URL (Postgres em produção)."""

    __tablename__ = "attendance_conversations"

    id = Column(String(64), primary_key=True)
    phone_e164 = Column(String(32), nullable=False, index=True)
    contact_name = Column(String(255), nullable=False, default="")
    profile_pic_url = Column(String(1000), nullable=False, default="")
    sheet_row = Column(Integer, nullable=True)
    registration_id = Column(Integer, nullable=True, index=True)
    status = Column(String(40), nullable=False, default="novo_lead")
    assignee = Column(String(120), nullable=False, default="")
    ai_mode = Column(String(20), nullable=False, default="on")
    tags_json = Column(Text, nullable=False, default="[]")
    notes = Column(Text, nullable=False, default="")
    last_message_at = Column(String(40), nullable=False, default="", index=True)
    last_message_preview = Column(String(255), nullable=False, default="")
    unread_count = Column(Integer, nullable=False, default=0)
    typing = Column(Boolean, nullable=False, default=False)
    remote_jid = Column(String(255), nullable=False, default="")
    evolution_instance = Column(String(120), nullable=False, default="", index=True)
    sector_id = Column(Integer, nullable=True, index=True)
    sector_name = Column(String(150), nullable=False, default="")
    created_at = Column(String(40), nullable=False, default="")
    updated_at = Column(String(40), nullable=False, default="")

    messages = relationship(
        "AttendanceMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class AttendanceMessage(Base):
    __tablename__ = "attendance_messages"
    __table_args__ = (
        Index(
            "uq_attendance_messages_evolution_id",
            "evolution_id",
            unique=True,
            sqlite_where=text("evolution_id != ''"),
            postgresql_where=text("evolution_id != ''"),
        ),
    )

    id = Column(String(64), primary_key=True)
    conversation_id = Column(
        String(64),
        ForeignKey("attendance_conversations.id"),
        nullable=False,
        index=True,
    )
    direction = Column(String(10), nullable=False)
    msg_type = Column(String(40), nullable=False, default="text")
    body = Column(Text, nullable=False, default="")
    media_url = Column(String(1000), nullable=False, default="")
    media_mime = Column(String(120), nullable=False, default="")
    media_filename = Column(String(255), nullable=False, default="")
    evolution_id = Column(String(255), nullable=False, default="")
    sender = Column(String(40), nullable=False, default="contact")
    created_at = Column(String(40), nullable=False, default="")

    conversation = relationship("AttendanceConversation", back_populates="messages")


class AttendanceSuppressedChat(Base):
    """Contatos removidos da inbox — sync/Evolution não recria até nova msg do lead."""

    __tablename__ = "attendance_suppressed_chats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_e164 = Column(String(32), nullable=False, default="", index=True)
    remote_jid = Column(String(255), nullable=False, default="", index=True)
    evolution_instance = Column(String(120), nullable=False, default="", index=True)
    suppressed_at = Column(String(40), nullable=False, default="")


class AppMeta(Base):
    """Flags leves de migração/config persistidas no mesmo DATABASE_URL."""

    __tablename__ = "app_meta"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False, default="")


class CrmNiche(Base):
    """Nichos comerciais (Configurações + cadastro)."""

    __tablename__ = "crm_niches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False)
    is_system = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String(40), nullable=False, default="")


class CrmSector(Base):
    """Setores com usuários responsáveis vinculados (account_users.id)."""

    __tablename__ = "crm_sectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    user_ids_json = Column(Text, nullable=False, default="[]")
    created_at = Column(String(40), nullable=False, default="")
    updated_at = Column(String(40), nullable=False, default="")


class CrmAttendanceTag(Base):
    """Tags de atendimento (Configurações → Atendimentos)."""

    __tablename__ = "crm_attendance_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False)
    is_system = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    color = Column(String(40), nullable=False, default="verde")
    created_at = Column(String(40), nullable=False, default="")


class CrmQuickReply(Base):
    """Mensagens rápidas / atalhos (ex.: /posvenda) em Atendimentos."""

    __tablename__ = "crm_quick_replies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shortcut = Column(String(80), unique=True, nullable=False, index=True)
    title = Column(String(150), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    media_type = Column(String(20), nullable=False, default="text")  # text|image|audio|video
    media_filename = Column(String(255), nullable=False, default="")
    media_mime = Column(String(120), nullable=False, default="")
    media_stored_name = Column(String(255), nullable=False, default="")
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String(40), nullable=False, default="")


class CrmRegistration(Base):
    """Cadastro Empresas/Leads — Postgres SoT; sheet_row espelha Folha1."""

    __tablename__ = "crm_registrations"
    __table_args__ = (
        Index("uq_crm_registrations_sheet_row", "sheet_row", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(80), nullable=False, default="default", index=True)
    sheet_row = Column(Integer, nullable=True, index=True)
    cadastro_tipo = Column(String(20), nullable=False, default="lead", index=True)
    cadastro_ativo = Column(Boolean, nullable=False, default=True)
    empresa = Column(String(255), nullable=False, default="")
    cnpj = Column(String(32), nullable=False, default="", index=True)
    data_abertura = Column(String(80), nullable=False, default="")
    capital = Column(String(120), nullable=False, default="")
    endereco = Column(String(255), nullable=False, default="")
    endereco_numero = Column(String(80), nullable=False, default="")
    endereco_complemento = Column(String(255), nullable=False, default="")
    cep = Column(String(120), nullable=False, default="")
    bairro = Column(String(255), nullable=False, default="")
    municipio = Column(String(255), nullable=False, default="")
    uf = Column(String(40), nullable=False, default="")
    email_empresa = Column(String(255), nullable=False, default="")
    site = Column(String(255), nullable=False, default="")
    telefone_b2b = Column(String(80), nullable=False, default="", index=True)
    telefone_fixo = Column(String(80), nullable=False, default="")
    telefone_alternativo = Column(String(80), nullable=False, default="")
    socio_1 = Column(String(255), nullable=False, default="")
    cpf_socio_1 = Column(String(40), nullable=False, default="")
    email_socio_1 = Column(String(255), nullable=False, default="")
    telefone_socio_1 = Column(String(80), nullable=False, default="")
    socio_2 = Column(String(255), nullable=False, default="")
    telefone_socio_2 = Column(String(80), nullable=False, default="")
    cpf_socio_2 = Column(String(40), nullable=False, default="")
    socio_3 = Column(String(255), nullable=False, default="")
    telefone_socio_3 = Column(String(80), nullable=False, default="")
    cpf_socio_3 = Column(String(40), nullable=False, default="")
    instagram = Column(String(255), nullable=False, default="")
    linkedin = Column(String(255), nullable=False, default="")
    vendedor = Column(String(120), nullable=False, default="")
    status = Column(String(120), nullable=False, default="Novo Lead", index=True)
    data_chamado = Column(String(80), nullable=False, default="")
    ultima_atualizacao = Column(String(80), nullable=False, default="")
    observacoes = Column(Text, nullable=False, default="")
    servico = Column(Text, nullable=False, default="")
    valor_proposta = Column(String(120), nullable=False, default="")
    colaboradores = Column(String(120), nullable=False, default="")
    nicho = Column(String(150), nullable=False, default="")
    is_filial = Column(Boolean, nullable=False, default=False)
    empresa_matriz_sheet_row = Column(Integer, nullable=True, index=True)
    extras_json = Column(Text, nullable=False, default="{}")
    actions_json = Column(Text, nullable=False, default="{}")
    payment_history_json = Column(Text, nullable=False, default="[]")
    closed_services_json = Column(Text, nullable=False, default="[]")
    created_at = Column(String(40), nullable=False, default="")
    updated_at = Column(String(40), nullable=False, default="")


class CrmActivity(Base):
    """Atividades do CRM — Postgres SoT."""

    __tablename__ = "crm_activities"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(80), nullable=False, default="default", index=True)
    registration_id = Column(Integer, nullable=True, index=True)
    sheet_row = Column(Integer, nullable=True, index=True)
    payload_json = Column(Text, nullable=False, default="{}")
    deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(String(40), nullable=False, default="")
    updated_at = Column(String(40), nullable=False, default="")


class CrmProposal(Base):
    """Histórico de propostas geradas — Postgres SoT."""

    __tablename__ = "crm_proposals"

    id = Column(String(64), primary_key=True)
    registration_id = Column(Integer, nullable=True, index=True)
    sheet_row = Column(Integer, nullable=True, index=True)
    cliente = Column(String(255), nullable=False, default="", index=True)
    cnpj_cpf = Column(String(40), nullable=False, default="")
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(String(40), nullable=False, default="", index=True)


class CrmAppSetting(Base):
    """Configurações da app (espelha aba Configuracoes)."""

    __tablename__ = "crm_app_settings"

    key = Column(String(120), primary_key=True)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(String(40), nullable=False, default="")


class CrmAccountUser(Base):
    """Usuários da conta FastAPI (espelha aba Usuarios)."""

    __tablename__ = "crm_account_users"

    id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False, default="")
    email = Column(String(255), nullable=False, default="", index=True)
    username = Column(String(80), nullable=False, default="", unique=True, index=True)
    password_hash = Column(String(255), nullable=False, default="")
    role = Column(String(50), nullable=False, default="Vendedor")
    active = Column(Boolean, nullable=False, default=True)
    department_id = Column(String(40), nullable=False, default="")
    department_name = Column(String(150), nullable=False, default="")
    last_access = Column(String(40), nullable=False, default="")
    created_at = Column(String(40), nullable=False, default="")
    updated_at = Column(String(40), nullable=False, default="")
    extras_json = Column(Text, nullable=False, default="{}")


class CrmMonthlyGoal(Base):
    """Metas mensais (espelha aba Metas)."""

    __tablename__ = "crm_monthly_goals"
    __table_args__ = (
        Index(
            "uq_crm_monthly_goals_period_seller",
            "reference_year",
            "reference_month",
            "seller",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    reference_year = Column(Integer, nullable=False)
    reference_month = Column(Integer, nullable=False)
    seller = Column(String(120), nullable=False, default="Todos os vendedores")
    amount = Column(Float, nullable=False, default=0)
    commission_rate = Column(Float, nullable=False, default=8.0)
    updated_at = Column(String(40), nullable=False, default="")
