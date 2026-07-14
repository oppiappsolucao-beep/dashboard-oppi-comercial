from datetime import date, datetime, timedelta

import streamlit as st

from auth.authentication import get_current_user
from components.cards import kpi_cards
from components.empty_states import empty_state
from components.tables import data_table
from database.connection import SessionLocal
from database.models import Proposal, ProposalItem
from database.repositories import get_companies, get_leads, get_proposals, get_services, get_tenant, save_model, tenant_query
from services.ai_service import ai_service
from services.pdf_service import generate_proposal_pdf
from services.proposal_service import proposal_service
from services.whatsapp_service import whatsapp_service
from utils.formatters import format_currency
from utils.dates import format_date


def render():
    user = get_current_user()
    db = SessionLocal()
    try:
        st.markdown("## Propostas")
        st.caption("Crie, revise e envie propostas com apoio de IA.")

        from services.crm_service import proposals_summary

        kpi_cards(proposals_summary(db, user["tenant_id"], user))

        col_list, col_detail = st.columns([1.1, 1.3])
        proposals = get_proposals(db, user["tenant_id"])

        with col_list:
            st.markdown("### Lista de propostas")
            if proposals:
                for proposal in proposals:
                    if st.button(f"{proposal.proposal_code} · {proposal.title}", key=f"prop_{proposal.id}", use_container_width=True):
                        st.session_state.selected_proposal_id = proposal.id
            else:
                empty_state("Nenhuma proposta", "Crie sua primeira proposta abaixo.")

        with col_detail:
            st.markdown("### Agente de IA / Formulário")
            prompt = st.text_area(
                "Descreva a proposta",
                placeholder="Ex: Crie uma proposta para a Clínica PetCare com implantação do CRM...",
            )
            companies = get_companies(db, user["tenant_id"])
            services = get_services(db, user["tenant_id"])

            if prompt and st.button("Interpretar com IA"):
                result = ai_service.parse_proposal_request(
                    prompt,
                    [{"company_name": c.company_name} for c in companies],
                    [{"name": s.name, "unit_value": s.unit_value} for s in services],
                )
                if result.get("configured") and not result.get("error"):
                    st.info("Solicitação interpretada. Revise os campos abaixo antes de salvar.")
                    st.code(result.get("raw", ""))
                elif not result.get("configured"):
                    st.warning(result.get("message"))
                else:
                    st.error(result.get("error"))

            with st.form("manual_proposal"):
                leads = get_leads(db, user["tenant_id"])
                lead = st.selectbox("Lead", leads, format_func=lambda l: l.name)
                title = st.text_input("Título *")
                description = st.text_area("Descrição")
                service = st.selectbox("Serviço principal", services, format_func=lambda s: s.name) if services else None
                quantity = st.number_input("Quantidade", min_value=1.0, value=1.0)
                discount = st.number_input("Desconto (R$)", min_value=0.0, value=0.0)
                payment_terms = st.text_input("Forma de pagamento", value="50% na assinatura e 50% na entrega")
                validity = st.date_input("Validade", value=date.today() + timedelta(days=15))
                generate_pdf = st.checkbox("Gerar PDF ao salvar", value=True)
                submitted = st.form_submit_button("Criar proposta", type="primary")

                if submitted:
                    if not title:
                        st.error("Informe o título da proposta.")
                    else:
                        count = tenant_query(db, Proposal, user["tenant_id"]).count()
                        unit = service.unit_value if service else 0
                        subtotal = unit * quantity
                        total = max(subtotal - discount, 0)
                        proposal = Proposal(
                            tenant_id=user["tenant_id"],
                            lead_id=lead.id if lead else None,
                            company_id=lead.company_id if lead else None,
                            assigned_user_id=user["id"],
                            proposal_code=proposal_service.next_code(user["tenant_id"], count),
                            title=title,
                            description=description,
                            subtotal=subtotal,
                            discount=discount,
                            total_value=total,
                            payment_terms=payment_terms,
                            validity_date=validity,
                            status="Rascunho",
                        )
                        db.add(proposal)
                        db.flush()
                        if service:
                            db.add(
                                ProposalItem(
                                    tenant_id=user["tenant_id"],
                                    proposal_id=proposal.id,
                                    service_id=service.id,
                                    description=service.description or service.name,
                                    quantity=quantity,
                                    unit_value=unit,
                                    total_value=subtotal,
                                )
                            )
                        if generate_pdf:
                            tenant = get_tenant(db, user["tenant_id"])
                            client = lead.company if lead and lead.company else None
                            pdf_path = generate_proposal_pdf(
                                {
                                    "proposal_code": proposal.proposal_code,
                                    "title": proposal.title,
                                    "total_value": total,
                                    "payment_terms": payment_terms,
                                    "validity_date": format_date(validity),
                                },
                                {"company_name": tenant.company_name if tenant else "Oppi CRM"},
                                {
                                    "company_name": client.company_name if client else lead.name,
                                    "email": lead.email if lead else "",
                                },
                                [{
                                    "description": service.name if service else title,
                                    "quantity": quantity,
                                    "unit_value": unit,
                                    "total_value": subtotal,
                                }],
                            )
                            proposal.pdf_path = pdf_path
                        db.commit()
                        st.success("Proposta criada com sucesso.")
                        st.rerun()

            selected_id = st.session_state.get("selected_proposal_id")
            if selected_id:
                proposal = tenant_query(db, Proposal, user["tenant_id"]).filter(Proposal.id == selected_id).first()
                if proposal:
                    st.markdown("---")
                    st.markdown(f"**{proposal.proposal_code}** · {proposal.status}")
                    st.write(format_currency(proposal.total_value))
                    c1, c2, c3 = st.columns(3)
                    if proposal.pdf_path and c1.button("Visualizar PDF"):
                        st.markdown(f"[Abrir PDF](file:///{proposal.pdf_path})")
                    if c2.button("Marcar como enviada"):
                        proposal.status = "Enviada"
                        proposal.sent_at = datetime.utcnow()
                        db.commit()
                        st.success("Proposta marcada como enviada.")
                    if c3.button("Enviar WhatsApp"):
                        if lead and lead.whatsapp:
                            result = whatsapp_service.send_message(lead.whatsapp, f"Proposta {proposal.proposal_code}: {proposal.title}")
                            st.write(result)
                        else:
                            st.warning("Lead sem WhatsApp cadastrado.")
    finally:
        db.close()
