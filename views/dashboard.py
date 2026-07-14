import streamlit as st

from auth.authentication import get_current_user
from components.cards import kpi_cards
from components.charts import funnel_chart
from components.empty_states import empty_state
from components.filters import seller_filter
from components.tables import data_table
from database.connection import SessionLocal
from database.repositories import get_users
from services.crm_service import dashboard_kpis, funnel_steps, hot_opportunities, overdue_activities, today_actions
from utils.dates import format_datetime
from utils.formatters import format_currency


def render():
    user = get_current_user()
    db = SessionLocal()
    try:
        st.markdown("## Visão Geral")
        st.caption("Resumo do desempenho comercial e prioridades do dia.")

        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            users = get_users(db, user["tenant_id"])
            seller_id = seller_filter(users, key="dash_seller")
        with col3:
            if st.button("＋ Novo Lead", type="primary", use_container_width=True):
                st.session_state.current_page = "Leads e Empresas"
                st.session_state.open_new_lead = True
                st.rerun()

        filtered_user = dict(user)
        if seller_id:
            filtered_user = {**user, "id": seller_id, "role": "Vendedor"}

        kpi_cards(dashboard_kpis(db, user["tenant_id"], filtered_user))

        col_left, col_right = st.columns([1.4, 1])
        with col_left:
            st.markdown("### Funil de Vendas")
            steps = funnel_steps(db, user["tenant_id"], filtered_user)
            funnel_chart(steps)
            if steps:
                for step in steps:
                    conv = f"{step['conversion']}%" if step["conversion"] is not None else "—"
                    st.markdown(f"**{step['name']}:** {step['count']} · Conversão {conv}")

        with col_right:
            st.markdown("### Ações do Dia")
            actions = today_actions(db, user["tenant_id"], filtered_user)
            if not actions:
                empty_state("Nenhuma ação para hoje", "Cadastre atividades com data de hoje para acompanhar aqui.")
            for action in actions:
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.write(f"**{format_datetime(action.scheduled_date)}** · {action.activity_type}")
                    st.caption(action.title)
                with c2:
                    if st.button("Concluir", key=f"done_{action.id}"):
                        action.status = "Concluída"
                        db.commit()
                        st.rerun()

        st.markdown("### Oportunidades Quentes")
        hot = hot_opportunities(db, user["tenant_id"], filtered_user)
        if hot:
            display = [{k: v for k, v in row.items() if k != "lead_id"} for row in hot]
            data_table(display)
        else:
            empty_state("Sem oportunidades quentes", "Quando houver leads qualificados, eles aparecerão aqui.")

        st.markdown("### Atividades em Atraso")
        overdue = overdue_activities(db, user["tenant_id"], filtered_user)
        if overdue:
            rows = []
            for act in overdue:
                rows.append({
                    "Atividade": act.title,
                    "Tipo": act.activity_type,
                    "Responsável": act.assigned_user.name if act.assigned_user else "—",
                    "Status": act.status,
                })
            data_table(rows)
        else:
            empty_state("Tudo em dia", "Não há atividades atrasadas no momento.")
    finally:
        db.close()
