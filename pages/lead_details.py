import streamlit as st

from auth.authentication import get_current_user
from components.empty_states import empty_state
from components.tables import data_table
from database.connection import SessionLocal
from database.repositories import get_history, get_lead
from utils.dates import format_datetime
from utils.formatters import format_currency


def render():
    user = get_current_user()
    lead_id = st.session_state.get("selected_lead_id")
    if not lead_id:
        empty_state("Selecione um lead", "Abra um lead na listagem para ver os detalhes.")
        return

    db = SessionLocal()
    try:
        lead = get_lead(db, user["tenant_id"], lead_id)
        if not lead:
            st.error("Lead não encontrado.")
            return

        st.markdown(f"## {lead.company.company_name if lead.company else lead.name}")
        st.caption(f"Contato: {lead.name} · {lead.stage.name if lead.stage else '—'}")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Valor estimado", format_currency(lead.estimated_value))
        col2.metric("Temperatura", lead.temperature or "—")
        col3.metric("Probabilidade", f"{lead.probability or 0}%")
        col4.metric("Responsável", lead.assigned_user.name if lead.assigned_user else "—")

        tab1, tab2, tab3, tab4 = st.tabs(["Resumo", "Histórico", "Atividades", "Propostas"])
        with tab1:
            st.write(f"Telefone: {lead.phone or '—'}")
            st.write(f"WhatsApp: {lead.whatsapp or '—'}")
            st.write(f"E-mail: {lead.email or '—'}")
            st.write(f"Próxima ação: {lead.next_action_description or '—'}")
            st.write(f"Data próxima ação: {format_datetime(lead.next_action_date)}")

        with tab2:
            history = get_history(db, user["tenant_id"], lead.id)
            if history:
                data_table([
                    {
                        "Evento": h.event_type,
                        "Título": h.title,
                        "Descrição": h.description or "—",
                        "Data": format_datetime(h.created_at),
                    }
                    for h in history
                ])
            else:
                empty_state("Sem histórico", "Alterações e interações aparecerão aqui.")

        with tab3:
            empty_state("Atividades do lead", "Gerencie atividades na tela Atividades.")

        with tab4:
            empty_state("Propostas do lead", "Crie propostas na tela Propostas.")

        if st.button("← Voltar para Leads"):
            st.session_state.pop("selected_lead_id", None)
            st.session_state.current_page = "Leads e Empresas"
            st.rerun()
    finally:
        db.close()
