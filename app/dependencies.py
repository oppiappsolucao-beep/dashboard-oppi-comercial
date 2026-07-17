from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.services.legacy_core import (
    PricingSessionStore,
    identify_columns,
    invalidate_sheet_cache,
    load_sheet_data,
    normalize_text,
    prepare_data,
    set_pricing_store,
)


def get_pricing_store(request: Request) -> PricingSessionStore:
    if "pricing" not in request.session:
        request.session["pricing"] = {"threads": {}, "progress": {}, "answers": {}}
    store = PricingSessionStore(request.session["pricing"])
    set_pricing_store(store)
    return store


def get_prepared_data(refresh: bool = False):
    if refresh:
        invalidate_sheet_cache()

    try:
        df = load_sheet_data()
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    if df.empty:
        return df, {}

    try:
        columns = identify_columns(df)
        prepared = prepare_data(df, columns)
        prepared = prepared[
            prepared["_empresa"].apply(lambda value: normalize_text(value) != "")
        ].copy()
        return prepared, columns
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"Erro ao processar dados da planilha: {error}",
        ) from error


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
    }


def is_admin(request: Request) -> bool:
    user = get_session_user(request)
    if user:
        return user.get("role") == "Administrador"
    return bool(request.session.get("authenticated"))
