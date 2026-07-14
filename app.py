import streamlit as st

from auth.authentication import get_current_user, is_authenticated, render_login
from components.sidebar import load_css, render_sidebar
from database.connection import init_db
from views import PAGE_RENDERERS
from views.lead_details import render as render_lead_details


def main():
    st.set_page_config(
        page_title="Oppi CRM Comercial",
        page_icon="💬",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    load_css()

    try:
        init_db()
    except Exception as exc:
        st.error("Não foi possível conectar ao banco de dados.")
        st.caption("Verifique DATABASE_URL no Easypanel e redeploy.")
        st.code(str(exc))
        return

    if not is_authenticated():
        render_login()
        return

    user = get_current_user()
    if user and user.get("must_change_password"):
        st.warning("Por segurança, altere sua senha no primeiro acesso.")
        st.info("Acesse Configurações > Usuários para redefinir a senha com o administrador.")

    if st.session_state.get("selected_lead_id"):
        render_lead_details()
        return

    page = render_sidebar()
    renderer = PAGE_RENDERERS.get(page)
    if renderer:
        renderer()
    else:
        st.error("Página não encontrada.")


main()
