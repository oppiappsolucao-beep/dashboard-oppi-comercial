from fastapi import Request
from fastapi.responses import RedirectResponse
import pandas as pd
import time

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

# Evita remesclar pendentes + copiar DF em toda troca de aba
_MERGED_PREPARED_DF: pd.DataFrame | None = None
_MERGED_PREPARED_COLUMNS: dict | None = None
_MERGED_PREPARED_AT = 0.0
_MERGED_PREPARED_TTL_SEC = 25.0


def invalidate_merged_prepared_cache() -> None:
    global _MERGED_PREPARED_DF, _MERGED_PREPARED_COLUMNS, _MERGED_PREPARED_AT
    _MERGED_PREPARED_DF = None
    _MERGED_PREPARED_COLUMNS = None
    _MERGED_PREPARED_AT = 0.0


def get_pricing_store(request: Request) -> PricingSessionStore:
    if "pricing" not in request.session:
        request.session["pricing"] = {"threads": {}, "progress": {}, "answers": {}}
    store = PricingSessionStore(request.session["pricing"])
    set_pricing_store(store)
    return store


def get_prepared_data(refresh: bool = False):
    """Carrega cadastros com cache. Prefere Postgres SoT após migração."""
    global _MERGED_PREPARED_DF, _MERGED_PREPARED_COLUMNS, _MERGED_PREPARED_AT

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

    # Postgres SoT (após migração CRM)
    try:
        from app.services.crm_registrations_storage import (
            build_prepared_dataframe,
            is_crm_postgres_ready,
        )

        if is_crm_postgres_ready():
            if refresh:
                invalidate_merged_prepared_cache()
                from app.services.crm_registrations_storage import invalidate_registrations_cache

                invalidate_registrations_cache()
            else:
                now = time.monotonic()
                if (
                    _MERGED_PREPARED_DF is not None
                    and (now - _MERGED_PREPARED_AT) < _MERGED_PREPARED_TTL_SEC
                ):
                    return _MERGED_PREPARED_DF.copy(), dict(_MERGED_PREPARED_COLUMNS or {})
            prepared, columns = build_prepared_dataframe()
            merged_df, merged_cols = _merge_pending(prepared, columns or {})
            _MERGED_PREPARED_DF = merged_df
            _MERGED_PREPARED_COLUMNS = dict(merged_cols or {})
            _MERGED_PREPARED_AT = time.monotonic()
            return merged_df.copy(), dict(merged_cols or {})
    except Exception:
        pass

    def _maybe_cutover_to_postgres(merged_df, merged_cols):
        """Após Folha1 quente no cache, tenta cutover e passa a servir Postgres."""
        try:
            from app.services.crm_db_migrate import try_lazy_crm_postgres_cutover
            from app.services.crm_registrations_storage import (
                build_prepared_dataframe,
                is_crm_postgres_ready,
            )

            if is_crm_postgres_ready():
                prepared, columns = build_prepared_dataframe()
                pg_df, pg_cols = _merge_pending(prepared, columns or {})
                return pg_df, pg_cols
            # Só tenta migrar se ainda não cortou — sem bypass a cada request
            if merged_df is not None and not getattr(merged_df, "empty", True):
                try_lazy_crm_postgres_cutover(bypass_throttle=False)
                if is_crm_postgres_ready():
                    prepared, columns = build_prepared_dataframe()
                    pg_df, pg_cols = _merge_pending(prepared, columns or {})
                    return pg_df, pg_cols
        except Exception:
            pass
        return merged_df, merged_cols

    if refresh:
        invalidate_sheet_cache()
        invalidate_merged_prepared_cache()
    else:
        now = time.monotonic()
        if (
            _MERGED_PREPARED_DF is not None
            and (now - _MERGED_PREPARED_AT) < _MERGED_PREPARED_TTL_SEC
        ):
            ensure_sheet_refresh_if_stale()
            out_df, out_cols = _maybe_cutover_to_postgres(
                _MERGED_PREPARED_DF, _MERGED_PREPARED_COLUMNS
            )
            _MERGED_PREPARED_DF = out_df
            _MERGED_PREPARED_COLUMNS = dict(out_cols or {})
            _MERGED_PREPARED_AT = time.monotonic()
            return out_df.copy(), dict(out_cols or {})

        cached = get_cached_prepared_data()
        if cached is not None:
            ensure_sheet_refresh_if_stale()
            prepared, columns = cached
            merged_df, merged_cols = _merge_pending(prepared, columns or {})
            merged_df, merged_cols = _maybe_cutover_to_postgres(merged_df, merged_cols)
            _MERGED_PREPARED_DF = merged_df
            _MERGED_PREPARED_COLUMNS = dict(merged_cols or {})
            _MERGED_PREPARED_AT = time.monotonic()
            return merged_df.copy(), dict(merged_cols or {})

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
        merged_df, merged_cols = _merge_pending(prepared, columns or {})
        merged_df, merged_cols = _maybe_cutover_to_postgres(merged_df, merged_cols)
        _MERGED_PREPARED_DF = merged_df
        _MERGED_PREPARED_COLUMNS = dict(merged_cols or {})
        _MERGED_PREPARED_AT = time.monotonic()
        return merged_df.copy(), dict(merged_cols or {})
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


def require_admin(request: Request):
    """Login + perfil Administrador. Sem acesso, volta para a visão geral."""
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        return RedirectResponse(url="/visao-geral", status_code=303)
    return None
