from datetime import datetime, timedelta

import streamlit as st

from auth.password import verify_password
from config.settings import settings
from database.connection import SessionLocal
from database.repositories import get_user_by_login, touch_user_access


def _session_expired() -> bool:
    expires_at = st.session_state.get("auth_expires_at")
    if not expires_at:
        return True
    return datetime.utcnow() > expires_at


def is_authenticated() -> bool:
    if not st.session_state.get("authenticated"):
        return False
    if _session_expired():
        logout()
        return False
    return True


def get_current_user() -> dict | None:
    if not is_authenticated():
        return None
    return st.session_state.get("user")


def logout() -> None:
    for key in ["authenticated", "user", "auth_expires_at", "current_page", "selected_lead_id"]:
        st.session_state.pop(key, None)


def render_login() -> None:
    st.markdown(
        """
        <div class="login-shell">
          <div class="login-card">
            <div class="login-brand">
              <div class="brand-mark"></div>
              <div>
                <div class="login-title">Oppi CRM</div>
                <div class="login-subtitle">Comercial</div>
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("### Entrar na plataforma")
        st.caption("Acesse sua conta comercial com segurança.")

        login = st.text_input("Usuário", placeholder="oppitech")
        password = st.text_input("Senha", type="password", placeholder="Sua senha")
        remember = st.checkbox("Manter conectado", value=True)

        if st.button("Entrar", type="primary", use_container_width=True):
            if not login or not password:
                st.error("Informe usuário e senha.")
                return

            db = SessionLocal()
            try:
                user = get_user_by_login(db, login)
                if not user or not verify_password(password, user.password_hash):
                    st.error("Usuário ou senha inválidos.")
                    return

                touch_user_access(db, user)
                timeout = settings.session_timeout_minutes if remember else 120
                st.session_state.authenticated = True
                st.session_state.auth_expires_at = datetime.utcnow() + timedelta(minutes=timeout)
                st.session_state.user = {
                    "id": user.id,
                    "tenant_id": user.tenant_id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                    "must_change_password": user.must_change_password,
                }
                st.session_state.current_page = "Visão Geral"
                st.rerun()
            finally:
                db.close()

        st.markdown("---")
        st.caption("Esqueceu a senha? Entre em contato com o administrador da sua empresa.")
