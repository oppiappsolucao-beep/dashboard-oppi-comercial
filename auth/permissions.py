import streamlit as st

from database.connection import SessionLocal
from database.repositories import get_permissions

ROLE_PERMISSIONS = {
    "Administrador": {"*"},
    "Gestor": {
        "view_all_leads",
        "manage_team",
        "view_reports",
        "view_financial",
        "edit_pipeline",
        "view_proposals",
        "manage_users",
    },
    "Vendedor": {
        "view_own_leads",
        "create_lead",
        "create_activity",
        "create_proposal",
        "move_pipeline",
    },
    "Financeiro": {
        "view_financial",
        "view_proposals",
        "view_approved_proposals",
    },
    "Analista": {
        "view_reports",
        "view_leads",
    },
}


def _custom_permissions(user_id: int, tenant_id: int) -> dict[str, bool]:
    db = SessionLocal()
    try:
        return get_permissions(db, tenant_id, user_id)
    finally:
        db.close()


def can(permission: str, user: dict | None = None) -> bool:
    user = user or st.session_state.get("user")
    if not user:
        return False

    role = user.get("role", "")
    allowed = ROLE_PERMISSIONS.get(role, set())
    if "*" in allowed:
        return True
    if permission in allowed:
        return True

    custom = _custom_permissions(user["id"], user["tenant_id"])
    if permission in custom:
        return custom[permission]
    return False


def require_permission(permission: str) -> bool:
    if not can(permission):
        st.warning("Você não possui permissão para acessar esta funcionalidade.")
        return False
    return True


def lead_scope_user_id(user: dict) -> int | None:
    if can("view_all_leads", user):
        return None
    return user["id"]
