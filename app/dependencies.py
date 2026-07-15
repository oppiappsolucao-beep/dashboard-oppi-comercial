from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.services.legacy_core import (
    PricingSessionStore,
    identify_columns,
    load_sheet_data,
    prepare_data,
    set_pricing_store,
)


def get_pricing_store(request: Request) -> PricingSessionStore:
    if "pricing" not in request.session:
        request.session["pricing"] = {"threads": {}, "progress": {}, "answers": {}}
    store = PricingSessionStore(request.session["pricing"])
    set_pricing_store(store)
    return store


def get_prepared_data():
    try:
        df = load_sheet_data()
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    if df.empty:
        return df, {}

    try:
        columns = identify_columns(df)
        prepared = prepare_data(df, columns)
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
    return username == settings.app_username and password == settings.app_password
