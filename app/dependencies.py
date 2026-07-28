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
    def _merge_pending(prepared: pd.DataFrame, columns: dict):
        base = prepared.copy() if prepared is not None and not getattr(prepared, "empty", True) else pd.DataFrame()
        # Remove pendentes antigos do cache antes de remesclar (fonte da verdade = SQLite).
        if not base.empty and "_pending_local" in base.columns:
            try:
                base = base[~base["_pending_local"].fillna(False).astype(bool)].copy()
            except Exception:
                pass
        try:
            from app.services.pending_companies import merge_pending_companies_into_df

            base = merge_pending_companies_into_df(base)
        except Exception:
            pass
        if base is None or base.empty:
            return pd.DataFrame(), columns or {}
        cols = columns or identify_columns(base)
        return base, cols

    if refresh:
        invalidate_sheet_cache()
    else:
        cached = get_cached_prepared_data()
        if cached is not None:
            ensure_sheet_refresh_if_stale()
            prepared, columns = cached
            return _merge_pending(prepared, columns or {})

    try:
        df = load_sheet_data(force_refresh=refresh)
    except Exception:
        return _merge_pending(pd.DataFrame(), {})

    if df is None:
        df = pd.DataFrame()

    try:
        columns = identify_columns(df) if not df.empty else {}
        prepared = prepare_data(df, columns) if not df.empty else pd.DataFrame()
        if not prepared.empty and "_empresa" in prepared.columns:
            prepared = prepared[
                prepared["_empresa"].apply(lambda value: normalize_text(value) != "")
            ].copy()
        # Cacheia só a base da planilha (sem pendentes locais).
        set_cached_prepared_data(prepared if prepared is not None else pd.DataFrame(), columns or {})
        return _merge_pending(prepared, columns or {})
    except Exception:
        pending_only, columns = _merge_pending(pd.DataFrame(), {})
        if not pending_only.empty:
            return pending_only, columns
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
