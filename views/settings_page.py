import streamlit as st

from auth.authentication import get_current_user
from auth.permissions import can, require_permission
from components.empty_states import empty_state
from components.tables import data_table
from database.connection import SessionLocal
from database.models import SubscriptionPlan, Service
from database.repositories import get_integrations, get_pipeline_stages, get_services, get_tenant, get_users, save_model, tenant_query
from services.asaas_service import asaas_service
from services.zapsign_service import zapsign_service


def render():
    user = get_current_user()
    if not can("manage_users") and user.get("role") != "Administrador":
        st.warning("Acesso limitado. Algumas abas exigem perfil Administrador.")

    db = SessionLocal()
    try:
        st.markdown("## Configurações")
        st.caption("Gerencie empresa, usuários, serviços, pipeline e integrações.")

        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            "Geral", "Usuários e Permissões", "Serviços", "Pipeline", "Integrações", "Assinatura",
        ])

        tenant = get_tenant(db, user["tenant_id"])

        with tab1:
            st.markdown("### Dados da empresa")
            if tenant:
                with st.form("tenant_form"):
                    company_name = st.text_input("Nome da empresa", value=tenant.company_name)
                    trade_name = st.text_input("Nome fantasia", value=tenant.trade_name or "")
                    cnpj = st.text_input("CNPJ", value=tenant.cnpj or "")
                    email = st.text_input("E-mail", value=tenant.email or "")
                    phone = st.text_input("Telefone", value=tenant.phone or "")
                    if st.form_submit_button("Salvar"):
                        tenant.company_name = company_name
                        tenant.trade_name = trade_name
                        tenant.cnpj = cnpj
                        tenant.email = email
                        tenant.phone = phone
                        db.commit()
                        st.success("Dados atualizados.")
            else:
                empty_state("Empresa não encontrada", "Verifique a configuração do tenant.")

        with tab2:
            if require_permission("manage_users"):
                users = get_users(db, user["tenant_id"], active_only=False)
                st.metric("Usuários ativos", len([u for u in users if u.active]))
                data_table([
                    {
                        "Nome": u.name,
                        "E-mail": u.email,
                        "Perfil": u.role,
                        "Status": "Ativo" if u.active else "Inativo",
                        "Último acesso": u.last_access.strftime("%d/%m/%Y %H:%M") if u.last_access else "—",
                    }
                    for u in users
                ])
            else:
                st.info("Somente administradores e gestores podem gerenciar usuários.")

        with tab3:
            services = get_services(db, user["tenant_id"])
            if services:
                data_table([
                    {
                        "Nome": s.name,
                        "Categoria": s.category or "—",
                        "Valor": s.unit_value,
                        "Recorrente": "Sim" if s.recurring else "Não",
                        "Status": "Ativo" if s.active else "Inativo",
                    }
                    for s in services
                ])
            else:
                empty_state("Nenhum serviço", "Cadastre serviços para usar em propostas.")

            with st.form("new_service"):
                st.markdown("#### Novo serviço")
                name = st.text_input("Nome *")
                category = st.text_input("Categoria")
                unit_value = st.number_input("Valor", min_value=0.0, step=100.0)
                recurring = st.checkbox("Recorrente")
                if st.form_submit_button("Cadastrar serviço"):
                    if name:
                        save_model(db, Service(
                            tenant_id=user["tenant_id"],
                            name=name,
                            category=category,
                            unit_value=unit_value,
                            recurring=recurring,
                            billing_cycle="Mensal" if recurring else "Único",
                            active=True,
                        ))
                        st.success("Serviço cadastrado.")
                        st.rerun()

        with tab4:
            stages = get_pipeline_stages(db, user["tenant_id"])
            data_table([
                {
                    "Etapa": s.name,
                    "Ordem": s.stage_order,
                    "Cor": s.color,
                    "Probabilidade": f"{s.conversion_probability}%",
                    "Ativa": "Sim" if s.active else "Não",
                }
                for s in stages
            ])

        with tab5:
            integrations = get_integrations(db, user["tenant_id"])
            cards = [
                ("WhatsApp Business", "whatsapp"),
                ("n8n", "n8n"),
                ("Google Sheets", "google_sheets"),
                ("Asaas", "asaas"),
                ("ZapSign", "zapsign"),
                ("Agente de IA", "ai"),
            ]
            for name, key in cards:
                with st.expander(name):
                    existing = next((i for i in integrations if i.integration_type == key), None)
                    st.write(f"Status: {existing.status if existing else 'desconectado'}")
                    if key == "asaas" and st.button("Testar Asaas", key=f"test_{key}"):
                        st.write(asaas_service.test_connection())
                    if key == "zapsign" and st.button("Testar ZapSign", key=f"test_{key}"):
                        st.write(zapsign_service.test_connection())

        with tab6:
            plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == tenant.plan_id).first() if tenant else None
            if tenant and plan:
                st.write(f"Plano: {plan.name}")
                st.write(f"Status: {tenant.subscription_status}")
                st.write(f"Limite de usuários: {tenant.user_limit}")
            else:
                empty_state("Plano não configurado", "Associe um plano ao tenant.")
    finally:
        db.close()
