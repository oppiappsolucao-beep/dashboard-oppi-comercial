from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from auth.password import hash_password
from config.theme import DEFAULT_PIPELINE_STAGES
from database.connection import SessionLocal, init_db
from database.models import (
    Activity,
    Company,
    Goal,
    Lead,
    LostReason,
    PipelineStage,
    Proposal,
    ProposalItem,
    Service,
    SubscriptionPlan,
    Tenant,
    User,
)


def run_seed(force: bool = False) -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(Tenant).first() and not force:
            print("Seed ignorado: já existem dados.")
            return

        plan = SubscriptionPlan(
            name="Profissional",
            monthly_price=297.0,
            user_limit=20,
            lead_limit=5000,
            proposal_limit=2000,
            features="CRM completo, propostas, relatórios, integrações",
            active=True,
        )
        db.add(plan)
        db.flush()

        tenant = Tenant(
            company_name="Oppi Comercial Demonstração",
            trade_name="Oppi Comercial",
            cnpj="00.000.000/0001-00",
            email="contato@oppicrm.com.br",
            phone="(11) 99999-0000",
            plan_id=plan.id,
            subscription_status="active",
            subscription_start=date.today(),
            subscription_end=date.today() + timedelta(days=365),
            user_limit=20,
            active=True,
        )
        db.add(tenant)
        db.flush()

        for name, order, color, prob in DEFAULT_PIPELINE_STAGES:
            db.add(
                PipelineStage(
                    tenant_id=tenant.id,
                    name=name,
                    stage_order=order,
                    color=color,
                    conversion_probability=prob,
                    active=True,
                )
            )

        for name in ["Preço alto", "Concorrente", "Sem budget", "Sem resposta", "Timing inadequado"]:
            db.add(LostReason(tenant_id=tenant.id, name=name, description="", active=True))

        users_data = [
            ("oppitech", "oppitech@oppitech.com.br", "Administrador", False),
            ("Gestor Demo", "gestor@oppicrm.com.br", "Gestor", False),
            ("Vendedor Demo", "vendedor@oppicrm.com.br", "Vendedor", False),
            ("Financeiro Demo", "financeiro@oppicrm.com.br", "Financeiro", False),
            ("Analista Demo", "analista@oppicrm.com.br", "Analista", False),
        ]
        password = hash_password("100316*")
        created_users: dict[str, User] = {}
        for name, email, role, must_change in users_data:
            user = User(
                tenant_id=tenant.id,
                name=name,
                email=email,
                password_hash=password,
                role=role,
                active=True,
                must_change_password=must_change,
            )
            db.add(user)
            created_users[role] = user
        db.flush()

        services_data = [
            ("Implantação do CRM", "Serviços", 8900.0, False),
            ("Automação de WhatsApp", "Automação", 4900.0, True),
            ("Agente de IA para Propostas", "IA", 3900.0, True),
            ("Dashboard Gerencial", "Analytics", 2900.0, True),
            ("Suporte Mensal", "Suporte", 990.0, True),
            ("Consultoria Comercial", "Consultoria", 3500.0, False),
            ("Integração n8n", "Integração", 2400.0, False),
            ("Relatórios Financeiros", "Financeiro", 1800.0, True),
        ]
        for name, category, value, recurring in services_data:
            db.add(
                Service(
                    tenant_id=tenant.id,
                    name=name,
                    description=f"Serviço {name}",
                    category=category,
                    unit_value=value,
                    recurring=recurring,
                    billing_cycle="Mensal" if recurring else "Único",
                    active=True,
                )
            )
        db.flush()

        stages = {
            s.name: s
            for s in db.query(PipelineStage).filter(PipelineStage.tenant_id == tenant.id).all()
        }
        seller = created_users["Vendedor"]

        companies = [
            Company(
                tenant_id=tenant.id,
                company_name="Clínica PetCare",
                trade_name="PetCare",
                niche="Saúde Animal",
                state="SP",
                city="São Paulo",
            ),
            Company(
                tenant_id=tenant.id,
                company_name="Alpha Tech",
                trade_name="Alpha",
                niche="Tecnologia",
                state="RJ",
                city="Rio de Janeiro",
            ),
        ]
        for company in companies:
            db.add(company)
        db.flush()

        now = datetime.utcnow()
        leads_data = [
            (companies[0], "Maria Souza", stages["Qualificação"].id, 24900.0, "Quente"),
            (companies[1], "João Lima", stages["Proposta Enviada"].id, 18900.0, "Morno"),
        ]
        for company, contact, stage_id, value, temp in leads_data:
            db.add(
                Lead(
                    tenant_id=tenant.id,
                    company_id=company.id,
                    assigned_user_id=seller.id,
                    name=contact,
                    phone="(11) 98888-7777",
                    whatsapp="(11) 98888-7777",
                    email=f"{contact.split()[0].lower()}@empresa.com",
                    lead_source="Indicação",
                    temperature=temp,
                    pipeline_stage_id=stage_id,
                    estimated_value=value,
                    probability=50,
                    last_contact=now - timedelta(days=2),
                    next_action_date=now + timedelta(days=1),
                    next_action_description="Retornar contato",
                    status="Aberto",
                )
            )
        db.flush()

        leads = db.query(Lead).filter(Lead.tenant_id == tenant.id).all()
        if leads:
            db.add(
                Activity(
                    tenant_id=tenant.id,
                    lead_id=leads[0].id,
                    company_id=leads[0].company_id,
                    assigned_user_id=seller.id,
                    created_by_user_id=seller.id,
                    activity_type="Ligação",
                    title="Retornar ligação",
                    description="Confirmar interesse na proposta",
                    scheduled_date=now + timedelta(hours=3),
                    status="Pendente",
                    priority="Alta",
                )
            )

        db.add(
            Goal(
                tenant_id=tenant.id,
                user_id=seller.id,
                reference_month=now.month,
                reference_year=now.year,
                revenue_goal=50000,
                sales_goal=10,
                meetings_goal=20,
                proposals_goal=15,
            )
        )

        db.commit()
        print("Seed concluído com sucesso.")
        print("Usuário: oppitech")
        print("Senha: 100316*")
    except Exception as exc:
        db.rollback()
        raise exc
    finally:
        db.close()


if __name__ == "__main__":
    run_seed(force=False)
