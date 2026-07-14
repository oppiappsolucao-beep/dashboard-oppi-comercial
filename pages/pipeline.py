import streamlit as st

from auth.authentication import get_current_user
from components.cards import kpi_cards
from components.charts import funnel_chart
from components.empty_states import empty_state
from components.tables import data_table
from database.connection import SessionLocal
from database.repositories import get_lost_reasons, get_pipeline_stages
from services.crm_service import funnel_steps, hot_opportunities, move_lead_stage, pipeline_kanban
from utils.dates import format_datetime
from utils.formatters import format_currency


def render():
    user = get_current_user()
    db = SessionLocal()
    try:
        st.markdown("## Funil de Vendas")
        st.caption("Visão analítica e kanban do pipeline comercial.")

        view = st.radio("Visualização", ["Funil analítico", "Kanban"], horizontal=True, key="pipeline_view")

        steps = funnel_steps(db, user["tenant_id"], user)
        kpi_cards([
            {"label": s["name"], "value": s["count"], "note": "leads", "icon": "◉", "tone": "purple"}
            for s in steps[:5]
        ] or [{"label": "Leads", "value": 0, "note": "sem dados", "icon": "◉", "tone": "purple"}])

        if view == "Funil analítico":
            col1, col2 = st.columns([1.5, 1])
            with col1:
                st.markdown("### Gráfico de funil")
                funnel_chart(steps)
            with col2:
                st.markdown("### Ações prioritárias")
                hot = hot_opportunities(db, user["tenant_id"], user, limit=5)
                if hot:
                    for item in hot:
                        st.markdown(f"- **{item['Empresa']}** · {item['Etapa']} · {item['Próxima ação']}")
                else:
                    empty_state("Sem prioridades", "Nenhuma oportunidade quente encontrada.")

            st.markdown("### Oportunidades quentes")
            hot_rows = hot_opportunities(db, user["tenant_id"], user)
            if hot_rows:
                data_table([{k: v for k, v in r.items() if k != "lead_id"} for r in hot_rows])
            else:
                empty_state("Sem oportunidades", "Cadastre leads para visualizar oportunidades.")
        else:
            st.markdown("### Kanban")
            board = pipeline_kanban(db, user["tenant_id"], user)
            stages = [s for s in get_pipeline_stages(db, user["tenant_id"]) if s.name not in ("Fechado", "Perdido")]
            cols = st.columns(len(stages) if stages else 1)
            for col, stage in zip(cols, stages):
                with col:
                    st.markdown(f"**{stage.name}**")
                    leads = board.get(stage.id, [])
                    if not leads:
                        st.caption("Nenhum lead")
                    for lead in leads:
                        with st.container(border=True):
                            company = lead.company.company_name if lead.company else lead.name
                            st.markdown(f"**{company}**")
                            st.caption(f"{lead.name} · {format_currency(lead.estimated_value)}")
                            st.caption(f"🌡 {lead.temperature} · {lead.assigned_user.name if lead.assigned_user else '—'}")
                            st.caption(f"Próxima: {lead.next_action_description or '—'}")
                            target_stages = [s for s in stages if s.id != stage.id]
                            if target_stages:
                                target = st.selectbox(
                                    "Mover para",
                                    [s.name for s in target_stages],
                                    key=f"move_{lead.id}",
                                    label_visibility="collapsed",
                                )
                                new_stage = next(s for s in target_stages if s.name == target)
                                closed_value = None
                                lost_reason_id = None
                                if new_stage.name == "Fechado":
                                    closed_value = st.number_input(
                                        "Valor final",
                                        min_value=0.0,
                                        value=float(lead.estimated_value or 0),
                                        key=f"closed_{lead.id}",
                                    )
                                if new_stage.name == "Perdido":
                                    reasons = get_lost_reasons(db, user["tenant_id"])
                                    if reasons:
                                        reason_name = st.selectbox(
                                            "Motivo",
                                            [r.name for r in reasons],
                                            key=f"lost_{lead.id}",
                                        )
                                        lost_reason_id = next(r.id for r in reasons if r.name == reason_name)
                                if st.button("Mover", key=f"btn_move_{lead.id}"):
                                    ok, msg = move_lead_stage(
                                        db,
                                        user["tenant_id"],
                                        user,
                                        lead.id,
                                        new_stage.id,
                                        closed_value,
                                        lost_reason_id,
                                    )
                                    if ok:
                                        st.success(msg)
                                        st.rerun()
                                    else:
                                        st.error(msg)
    finally:
        db.close()
