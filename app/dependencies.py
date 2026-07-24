from fastapi import Request
from fastapi.responses import RedirectResponse
import pandas as pd

from app.config import settings
from app.services.legacy_core import (
    PricingSessionStore,
    ensure_sheet_refresh_if_stale,
    get_cached_prepared_data,
    identify_columns,
    invalidate_sheet_cache,
    load_sheet_data,
    normalize_text,
    prepare_data,
    set_cached_prepared_data,
    set_pricing_store,
)


def get_pricing_store(request: Request) -> PricingSessionStore:
    if "pricing" not in request.session:
        request.session["pricing"] = {"threads": {}, "progress": {}, "answers": {}}
    store = PricingSessionStore(request.session["pricing"])
    set_pricing_store(store)
    return store


def get_prepared_data(refresh: bool = False):
    """Carrega a planilha com cache. Nunca derruba a tela por limite 429."""
    if refresh:
        invalidate_sheet_cache()
    else:
        cached = get_cached_prepared_data()
        if cached is not None:
            # Navegação rápida: serve memória e atualiza planilha em background se TTL caiu.
            ensure_sheet_refresh_if_stale()
            return cached

    try:
        df = load_sheet_data(force_refresh=refresh)
    except Exception:
        return pd.DataFrame(), {}

    if df is None:
        df = pd.DataFrame()

    try:
        columns = identify_columns(df) if not df.empty else {}
        prepared = prepare_data(df, columns) if not df.empty else pd.DataFrame()
        if not prepared.empty and "_empresa" in prepared.columns:
            prepared = prepared[
                prepared["_empresa"].apply(lambda value: normalize_text(value) != "")
            ].copy()
        try:
            from app.services.pending_companies import merge_pending_companies_into_df

            prepared = merge_pending_companies_into_df(prepared)
        except Exception:
            pass
        if prepared.empty:
            set_cached_prepared_data(pd.DataFrame(), columns)
            return pd.DataFrame(), columns
        columns = columns or identify_columns(prepared)
        set_cached_prepared_data(prepared, columns)
        return prepared, columns
    except Exception:
        try:
            from app.services.pending_companies import merge_pending_companies_into_df

            pending_only = merge_pending_companies_into_df(pd.DataFrame())
            if not pending_only.empty:
                columns = identify_columns(pending_only)
                set_cached_prepared_data(pending_only, columns)
                return pending_only, columns
        except Exception:
            pass
        return pd.DataFrame(), {}


def require_auth(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)
    return None


def check_credentials(username: str, password: str) -> bool:
    clean_username = normalize_text(username)
    if clean_username == settings.app_username and password == settings.app_password:
        return True

    from app.services.account_users import verify_account_user_credentials

    return verify_account_user_credentials(clean_username, password) is not None


def get_session_user(request: Request) -> dict | None:
    username = normalize_text(request.session.get("username", ""))
    if not username:
        return None

    if username.lower() == settings.app_username.lower():
        return {
            "username": settings.app_username,
            "name": settings.app_username,
            "role": "Administrador",
            "managed": False,
        }

    from app.services.account_users import get_account_user_by_username

    user = get_account_user_by_username(username)
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "name": user["name"],
        "role": user["role"],
        "managed": True,
        "department_id": str(user.get("department_id") or ""),
        "department_name": user.get("department_name") or "",
    }


def is_admin(request: Request) -> bool:
    user = get_session_user(request)
    if user:
        return user.get("role") == "Administrador"
    return bool(request.session.get("authenticated"))
