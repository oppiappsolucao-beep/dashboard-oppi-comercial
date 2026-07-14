from pathlib import Path

import streamlit as st

from auth.authentication import get_current_user, logout
from config.settings import settings
from utils.helpers import initials


MENU_ITEMS = [
    ("Visão Geral", "▦"),
    ("Funil de Vendas", "⛃"),
    ("Leads e Empresas", "👥"),
    ("Atividades", "📅"),
    ("Propostas", "📄"),
    ("Metas e Relatórios", "📊"),
    ("Configurações", "⚙"),
]


def load_css() -> None:
    css_parts = []
    assets = settings.assets_dir
    for name in ("styles.css", "dashboard.css"):
        path = assets / name
        if path.exists():
            css_parts.append(path.read_text(encoding="utf-8"))
    legacy = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "dashboard.css"
    if legacy.exists():
        css_parts.append(legacy.read_text(encoding="utf-8"))
    if css_parts:
        st.markdown(f"<style>{''.join(css_parts)}</style>", unsafe_allow_html=True)


def render_sidebar() -> str:
    user = get_current_user() or {}
    if "current_page" not in st.session_state:
        st.session_state.current_page = "Visão Geral"

    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
              <div class="brand-mark"></div>
              <div>
                <div class="sidebar-title">Oppi CRM</div>
                <div class="sidebar-subtitle">Comercial</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        current = st.session_state.current_page
        for label, icon in MENU_ITEMS:
            active = current == label
            btn_type = "primary" if active else "secondary"
            if st.button(f"{icon}  {label}", key=f"nav_{label}", use_container_width=True, type=btn_type):
                st.session_state.current_page = label
                st.rerun()

        st.markdown("<div class='sidebar-spacer'></div>", unsafe_allow_html=True)

        st.markdown(
            f"""
            <div class="sidebar-user">
              <div class="sidebar-avatar">{initials(user.get('name', 'OP'))}</div>
              <div>
                <div class="sidebar-user-name">{user.get('name', '')}</div>
                <div class="sidebar-user-role">{user.get('role', 'Comercial')}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("Sair", use_container_width=True, key="logout_btn"):
            logout()
            st.rerun()

    return st.session_state.current_page
