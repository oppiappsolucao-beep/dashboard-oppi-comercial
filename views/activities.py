from datetime import datetime

import streamlit as st

from auth.authentication import get_current_user
from components.cards import kpi_cards
from components.empty_states import empty_state
from components.tables import data_table
from database.connection import SessionLocal
from database.models import Activity
from database.repositories import get_activities, get_leads, get_users, save_model
from utils.dates import format_datetime


def render():
    user = get_current_user()
    db = SessionLocal()
    try:
        st.markdown("## Atividades")
        st.caption("Acompanhe tarefas, ligações, reuniões e follow-ups.")

        activities = get_activities(db, user["tenant_id"])
        today = datetime.utcnow().date()
        today_count = len([a for a in activities if a.scheduled_date and a.scheduled_date.date() == today])
        pending = len([a for a in activities if a.status == "Pendente"])
        done = len([a for a in activities if a.status == "Concluída"])
        late = len([a for a in activities if a.status == "Atrasada"])

        kpi_cards([
            {"label": "Atividades hoje", "value": today_count, "note": "agenda", "icon": "📅", "tone": "purple"},
            {"label": "Pendentes", "value": pending, "note": "em aberto", "icon": "⏳", "tone": "blue"},
            {"label": "Concluídas", "value": done, "note": "finalizadas", "icon": "✓", "tone": "green"},
            {"label": "Atrasadas", "value": late, "note": "atenção", "icon": "!", "tone": "pink"},
        ])

        if st.button("＋ Nova Atividade"):
            st.session_state.show_new_activity = True

        if st.session_state.get("show_new_activity"):
            with st.form("new_activity"):
                leads = get_leads(db, user["tenant_id"])
                lead = st.selectbox("Lead", leads, format_func=lambda l: l.name)
                activity_type = st.selectbox("Tipo", ["Ligação", "WhatsApp", "E-mail", "Reunião", "Tarefa", "Follow-up"])
                title = st.text_input("Título *")
                description = st.text_area("Descrição")
                users = get_users(db, user["tenant_id"])
                assigned = st.selectbox("Responsável", users, format_func=lambda u: u.name)
                date_val = st.date_input("Data")
                time_val = st.time_input("Hora")
                priority = st.selectbox("Prioridade", ["Baixa", "Normal", "Alta"])
                if st.form_submit_button("Salvar"):
                    if not title:
                        st.error("Informe o título.")
                    else:
                        scheduled = datetime.combine(date_val, time_val)
                        activity = Activity(
                            tenant_id=user["tenant_id"],
                            lead_id=lead.id if lead else None,
                            company_id=lead.company_id if lead else None,
                            assigned_user_id=assigned.id,
                            created_by_user_id=user["id"],
                            activity_type=activity_type,
                            title=title,
                            description=description,
                            scheduled_date=scheduled,
                            status="Pendente",
                            priority=priority,
                        )
                        save_model(db, activity)
                        st.success("Atividade criada.")
                        st.session_state.show_new_activity = False
                        st.rerun()

        tab1, tab2, tab3, tab4 = st.tabs(["Todas", "Pendentes", "Concluídas", "Atrasadas"])
        tabs = {
            tab1: activities,
            tab2: [a for a in activities if a.status == "Pendente"],
            tab3: [a for a in activities if a.status == "Concluída"],
            tab4: [a for a in activities if a.status == "Atrasada"],
        }
        for tab, items in tabs.items():
            with tab:
                if items:
                    data_table([
                        {
                            "Atividade": a.title,
                            "Tipo": a.activity_type,
                            "Responsável": a.assigned_user.name if a.assigned_user else "—",
                            "Data/Hora": format_datetime(a.scheduled_date),
                            "Status": a.status,
                            "Prioridade": a.priority,
                        }
                        for a in items
                    ])
                else:
                    empty_state("Nenhuma atividade", "Cadastre uma nova atividade para começar.")
    finally:
        db.close()
