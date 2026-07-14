from datetime import datetime

import streamlit as st

from auth.authentication import get_current_user
from auth.permissions import require_permission
from components.cards import kpi_cards
from components.charts import revenue_chart
from components.empty_states import empty_state
from components.tables import data_table
from database.connection import SessionLocal
from database.repositories import get_users
from services.crm_service import goals_report
from utils.formatters import format_currency


def render():
    user = get_current_user()
    if not require_permission("view_reports"):
        return

    db = SessionLocal()
    try:
        st.markdown("## Metas e Relatórios")
        st.caption("Acompanhe metas, faturamento e performance da equipe.")

        now = datetime.utcnow()
        col1, col2, col3 = st.columns(3)
        month = col1.selectbox("Mês", list(range(1, 13)), index=now.month - 1)
        year = col2.number_input("Ano", min_value=2020, max_value=2100, value=now.year)
        users = get_users(db, user["tenant_id"])
        seller = col3.selectbox("Vendedor", ["Todos"] + [u.name for u in users])

        rows = goals_report(db, user["tenant_id"], month, year)
        if seller != "Todos":
            rows = [r for r in rows if r["Vendedor"] == seller]

        total_meta = sum(r["Meta"] for r in rows)
        total_realizado = sum(r["Realizado"] for r in rows)
        kpi_cards([
            {"label": "Meta do mês", "value": format_currency(total_meta), "note": "equipe", "icon": "🎯", "tone": "purple"},
            {"label": "Faturamento realizado", "value": format_currency(total_realizado), "note": "fechado", "icon": "💰", "tone": "green"},
            {"label": "Taxa de conversão", "value": "—", "note": "por etapa", "icon": "%", "tone": "blue"},
            {"label": "Ticket médio", "value": format_currency(total_realizado / max(len(rows), 1)), "note": "estimado", "icon": "📈", "tone": "pink"},
        ])

        st.markdown("### Evolução de Faturamento")
        chart_data = [{"month": f"{month:02d}/{year}", "meta": total_meta, "realizado": total_realizado}]
        revenue_chart(chart_data)

        st.markdown("### Performance por vendedor")
        if rows:
            data_table(rows)
        else:
            empty_state("Sem metas cadastradas", "Configure metas na aba Configurações ou execute o seed de desenvolvimento.")

        st.markdown("### Relatório consolidado")
        if rows:
            data_table(rows)
        else:
            st.info("Nenhum dado consolidado para o período selecionado.")
    finally:
        db.close()
