from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.app_settings import set_proposal_template
from app.services.legacy_core import invalidate_sheet_cache, parse_money
from app.services.monthly_goals import TEAM_SELLER_LABEL, set_monthly_goal
from app.services.settings_page import (
    ROLE_OPTIONS,
    SETTINGS_TABS,
    build_company_profile,
    build_goals_settings,
    build_integrations,
    build_permissions,
    build_proposal_template_settings,
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
        "goals_settings": build_goals_settings(df),
        "proposal_template_settings": build_proposal_template_settings(),
        "is_admin": is_admin(request),
        "goal_success": request.session.pop("settings_goal_success", ""),
        "goal_error": request.session.pop("settings_goal_error", ""),
        "template_success": request.session.pop("settings_template_success", ""),
        "template_error": request.session.pop("settings_template_error", ""),
        "service_success": request.session.pop("settings_service_success", ""),
        "service_error": request.session.pop("settings_service_error", ""),
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


@router.post("/configuracoes/metas")
async def settings_save_goal(
    request: Request,
    month: int = Form(...),
    year: int = Form(...),
    amount: str = Form(...),
    seller: str = Form(TEAM_SELLER_LABEL),
    tab: str = Form("metas"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_goal_error"] = "Apenas o administrador pode definir a meta do mês."
        return RedirectResponse(url="/configuracoes?tab=metas", status_code=303)

    try:
        goal_value = parse_money(amount)
        if goal_value <= 0:
            raise ValueError("Informe um valor maior que zero para a meta.")
        set_monthly_goal(year, month, goal_value, seller)
        request.session["settings_goal_success"] = "Meta do mês salva com sucesso."
    except ValueError as error:
        request.session["settings_goal_error"] = str(error) if str(error) else "Informe um valor válido para a meta."
    except Exception as error:
        request.session["settings_goal_error"] = f"Não consegui salvar a meta: {error}"

    return RedirectResponse(url="/configuracoes?tab=metas", status_code=303)


@router.post("/configuracoes/servicos/adicionar")
async def settings_add_service(
    request: Request,
    service_name: str = Form(...),
    tab: str = Form("servicos"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_service_error"] = "Apenas o administrador pode cadastrar serviços."
        return RedirectResponse(url="/configuracoes?tab=servicos", status_code=303)

    from app.services.commercial_services import add_commercial_service

    try:
        add_commercial_service(service_name)
        request.session["settings_service_success"] = "Serviço cadastrado com sucesso."
    except ValueError as error:
        request.session["settings_service_error"] = str(error)
    except Exception as error:
        request.session["settings_service_error"] = f"Não consegui cadastrar o serviço: {error}"

    return RedirectResponse(url="/configuracoes?tab=servicos", status_code=303)


@router.post("/configuracoes/servicos/remover")
async def settings_remove_service(
    request: Request,
    service_name: str = Form(...),
    tab: str = Form("servicos"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_service_error"] = "Apenas o administrador pode remover serviços."
        return RedirectResponse(url="/configuracoes?tab=servicos", status_code=303)

    from app.services.commercial_services import remove_commercial_service

    try:
        remove_commercial_service(service_name)
        request.session["settings_service_success"] = "Serviço removido com sucesso."
    except ValueError as error:
        request.session["settings_service_error"] = str(error)
    except Exception as error:
        request.session["settings_service_error"] = f"Não consegui remover o serviço: {error}"

    return RedirectResponse(url="/configuracoes?tab=servicos", status_code=303)


@router.post("/configuracoes/modelo-proposta")
async def settings_save_proposal_template(
    request: Request,
    template_url: str = Form(...),
    tab: str = Form("geral"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_template_error"] = "Apenas o administrador pode alterar o modelo de proposta."
        return RedirectResponse(url="/configuracoes?tab=geral", status_code=303)

    try:
        set_proposal_template(template_url)
        request.session["settings_template_success"] = "Modelo de proposta salvo com sucesso."
    except ValueError as error:
        request.session["settings_template_error"] = str(error)
    except Exception as error:
        request.session["settings_template_error"] = f"Não consegui salvar o modelo: {error}"

    return RedirectResponse(url="/configuracoes?tab=geral", status_code=303)


@router.post("/configuracoes/atualizar")
async def settings_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/configuracoes", status_code=303)
