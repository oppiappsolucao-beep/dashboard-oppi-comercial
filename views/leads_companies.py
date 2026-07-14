import streamlit as st

from auth.authentication import get_current_user
from auth.permissions import can
from components.cards import kpi_cards
from components.empty_states import empty_state
from components.tables import data_table
from database.connection import SessionLocal
from database.models import Company, Lead
from database.repositories import get_companies, get_leads, get_pipeline_stages, get_users, save_model
from utils.formatters import format_currency
from utils.dates import format_datetime


def render():
    user = get_current_user()
    db = SessionLocal()
    try:
        st.markdown("## Leads e Empresas")
        st.caption("Gerencie contatos, empresas e oportunidades.")

        search = st.text_input("Buscar", placeholder="Nome, empresa, e-mail ou telefone")
        tab1, tab2, tab3 = st.tabs(["Todos", "Leads", "Empresas"])

        leads = get_leads(db, user["tenant_id"], search=search)
        companies = get_companies(db, user["tenant_id"], search=search)
        open_leads = [l for l in leads if l.status == "Aberto"]
        negotiation = sum(l.estimated_value or 0 for l in open_leads)

        kpi_cards([
            {"label": "Total de Leads", "value": len(leads), "note": "cadastrados", "icon": "👥", "tone": "purple"},
            {"label": "Empresas", "value": len(companies), "note": "base", "icon": "🏢", "tone": "blue"},
            {"label": "Leads Ativos", "value": len(open_leads), "note": "em aberto", "icon": "✦", "tone": "pink"},
            {"label": "Oportunidades", "value": len(open_leads), "note": "pipeline", "icon": "◉", "tone": "green"},
            {"label": "Valor em Negociação", "value": format_currency(negotiation), "note": "estimado", "icon": "💰", "tone": "violet"},
        ])

        if st.session_state.pop("open_new_lead", False) or st.button("＋ Novo Lead", type="primary"):
            st.session_state.show_new_lead = True

        if st.session_state.get("show_new_lead") and can("create_lead"):
            with st.form("new_lead_form"):
                st.markdown("### Novo Lead")
                name = st.text_input("Nome do contato *")
                company_name = st.text_input("Empresa *")
                phone = st.text_input("Telefone")
                email = st.text_input("E-mail")
                users = get_users(db, user["tenant_id"])
                seller = st.selectbox("Responsável", users, format_func=lambda u: u.name)
                stages = get_pipeline_stages(db, user["tenant_id"])
                stage = st.selectbox("Etapa", stages, format_func=lambda s: s.name)
                value = st.number_input("Valor estimado", min_value=0.0, step=100.0)
                submitted = st.form_submit_button("Salvar lead")
                if submitted:
                    if not name or not company_name:
                        st.error("Nome e empresa são obrigatórios.")
                    else:
                        company = Company(
                            tenant_id=user["tenant_id"],
                            company_name=company_name,
                            trade_name=company_name,
                        )
                        db.add(company)
                        db.flush()
                        lead = Lead(
                            tenant_id=user["tenant_id"],
                            company_id=company.id,
                            assigned_user_id=seller.id,
                            name=name,
                            phone=phone,
                            email=email,
                            pipeline_stage_id=stage.id,
                            estimated_value=value,
                            status="Aberto",
                        )
                        save_model(db, lead)
                        st.success("Lead criado com sucesso.")
                        st.session_state.show_new_lead = False
                        st.rerun()

        def lead_rows(items):
            rows = []
            for lead in items:
                rows.append({
                    "Nome/Empresa": lead.company.company_name if lead.company else lead.name,
                    "Contato": lead.name,
                    "Etapa": lead.stage.name if lead.stage else "—",
                    "Responsável": lead.assigned_user.name if lead.assigned_user else "—",
                    "Último contato": format_datetime(lead.last_contact),
                    "Próxima ação": lead.next_action_description or "—",
                    "Valor": format_currency(lead.estimated_value),
                })
            return rows

        with tab1:
            if leads:
                data_table(lead_rows(leads))
            else:
                empty_state("Nenhum lead", "Cadastre seu primeiro lead para começar.")

        with tab2:
            if leads:
                data_table(lead_rows(leads))
            else:
                empty_state("Nenhum lead", "Cadastre leads para acompanhar oportunidades.")

        with tab3:
            if companies:
                data_table([
                    {
                        "Empresa": c.company_name,
                        "Nicho": c.niche or "—",
                        "Cidade": c.city or "—",
                        "Estado": c.state or "—",
                    }
                    for c in companies
                ])
            else:
                empty_state("Nenhuma empresa", "As empresas aparecerão conforme os leads forem cadastrados.")
    finally:
        db.close()
