from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.dependencies import get_prepared_data, require_auth
from app.services.legacy_core import invalidate_sheet_cache
from app.services.settings_page import (
    ROLE_OPTIONS,
    SETTINGS_TABS,
    build_company_profile,
    build_integrations,
    build_permissions,
    build_services_list,
    build_settings_kpi_cards,
    build_users_table,
)
from app.templating import render

router = APIRouter()


def _get_permissions(request: Request) -> dict:
    stored = request.session.get("settings_permissions")
    if isinstance(stored, dict):
        return stored
    return {}


def _parse_settings_params(request: Request, form: dict | None = None) -> dict:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    tab = data.get("tab", "usuarios")
    valid_tabs = {tab_id for tab_id, _ in SETTINGS_TABS}
    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(data.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10

    return {
        "tab": tab if tab in valid_tabs else "usuarios",
        "search": data.get("search", ""),
        "role": data.get("role", "Todos os perfis"),
        "page": max(1, page),
        "per_page": per_page if per_page in (10, 25, 50) else 10,
    }


def _settings_context(request: Request, settings_params: dict):
    df, _columns = get_prepared_data()
    integrations = build_integrations()

    return {
        "active_page": "settings",
        "settings_params": settings_params,
        "settings_tabs": SETTINGS_TABS,
        "role_options": ["Todos os perfis"] + ROLE_OPTIONS,
        "kpi_cards": build_settings_kpi_cards(df, integrations),
        "users_table": build_users_table(
            df,
            settings.app_username,
            search=settings_params["search"],
            role=settings_params["role"],
            page=settings_params["page"],
            per_page=settings_params["per_page"],
        ),
        "services": build_services_list(),
        "permissions": build_permissions(_get_permissions(request)),
        "integrations": integrations,
        "company": build_company_profile(df),
    }


@router.get("/configuracoes", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    settings_params = _parse_settings_params(request)
    return render(request, "settings/index.html", _settings_context(request, settings_params))


@router.post("/configuracoes/filtros", response_class=HTMLResponse)
async def settings_filters(
    request: Request,
    tab: str = Form("usuarios"),
    search: str = Form(""),
    role: str = Form("Todos os perfis"),
    page: int = Form(1),
    per_page: int = Form(10),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    settings_params = _parse_settings_params(request, {
        "tab": tab,
        "search": search,
        "role": role,
        "page": page,
        "per_page": per_page,
    })
    return render(
        request,
        "partials/settings_content.html",
        _settings_context(request, settings_params),
    )


@router.post("/configuracoes/permissoes", response_class=HTMLResponse)
async def settings_permissions_toggle(
    request: Request,
    permission_key: str = Form(...),
    tab: str = Form("usuarios"),
    search: str = Form(""),
    role: str = Form("Todos os perfis"),
    page: int = Form(1),
    per_page: int = Form(10),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    from app.services.settings_page import DEFAULT_PERMISSIONS

    permissions = _get_permissions(request)
    default = DEFAULT_PERMISSIONS.get(permission_key, False)
    permissions[permission_key] = not permissions.get(permission_key, default)
    request.session["settings_permissions"] = permissions

    settings_params = _parse_settings_params(request, {
        "tab": tab,
        "search": search,
        "role": role,
        "page": page,
        "per_page": per_page,
    })
    return render(
        request,
        "partials/settings_content.html",
        _settings_context(request, settings_params),
    )


@router.post("/configuracoes/atualizar")
async def settings_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/configuracoes", status_code=303)
