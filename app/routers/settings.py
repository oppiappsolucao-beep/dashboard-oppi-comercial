from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.app_settings import set_proposal_template
from app.services.legacy_core import invalidate_sheet_cache, normalize_text, parse_money
from app.services.sheet_registration_sync import sync_registration_rows
from app.services.monthly_goals import TEAM_SELLER_LABEL, delete_monthly_goal, parse_commission_rate, set_monthly_goal
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
        "edit_user_id": data.get("edit_user_id", ""),
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
            edit_user_id=settings_params.get("edit_user_id", ""),
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
        "user_success": request.session.pop("settings_user_success", ""),
        "user_error": request.session.pop("settings_user_error", ""),
        "sheet_sync_success": request.session.pop("settings_sheet_sync_success", ""),
        "sheet_sync_error": request.session.pop("settings_sheet_sync_error", ""),
        "seed_fake_success": request.session.pop("settings_seed_fake_success", ""),
        "seed_fake_error": request.session.pop("settings_seed_fake_error", ""),
        "seed_fake_user_success": request.session.pop("settings_seed_fake_user_success", ""),
        "seed_fake_user_error": request.session.pop("settings_seed_fake_user_error", ""),
        "seed_fake_service_success": request.session.pop("settings_seed_fake_service_success", ""),
        "seed_fake_service_error": request.session.pop("settings_seed_fake_service_error", ""),
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
    commission: str = Form("8"),
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
        commission_rate = parse_commission_rate(commission)
        set_monthly_goal(year, month, goal_value, seller, commission_rate=commission_rate)
        request.session["settings_goal_success"] = "Meta do mês salva com sucesso."
    except ValueError as error:
        request.session["settings_goal_error"] = str(error) if str(error) else "Informe um valor válido para a meta."
    except Exception as error:
        request.session["settings_goal_error"] = f"Não consegui salvar a meta: {error}"

    return RedirectResponse(url="/configuracoes?tab=metas", status_code=303)


@router.post("/configuracoes/metas/remover")
async def settings_remove_goal(
    request: Request,
    month: int = Form(...),
    year: int = Form(...),
    seller: str = Form(TEAM_SELLER_LABEL),
    tab: str = Form("metas"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_goal_error"] = "Apenas o administrador pode remover metas."
        return RedirectResponse(url="/configuracoes?tab=metas", status_code=303)

    try:
        delete_monthly_goal(year, month, seller)
        request.session["settings_goal_success"] = "Meta removida com sucesso."
    except ValueError as error:
        request.session["settings_goal_error"] = str(error) if str(error) else "Meta não encontrada."
    except Exception as error:
        request.session["settings_goal_error"] = f"Não consegui remover a meta: {error}"

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


@router.post("/configuracoes/usuarios/adicionar")
async def settings_add_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("Vendedor"),
    active: str = Form("1"),
    tab: str = Form("usuarios"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_user_error"] = "Apenas o administrador pode cadastrar usuários."
        return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)

    from app.services.account_users import create_account_user

    try:
        create_account_user(
            name=name,
            email=email,
            username=username,
            password=password,
            role=role,
            active=active == "1",
        )
        request.session["settings_user_success"] = "Usuário cadastrado com sucesso."
    except ValueError as error:
        request.session["settings_user_error"] = str(error)
    except Exception as error:
        request.session["settings_user_error"] = f"Não consegui cadastrar o usuário: {error}"

    return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)


@router.post("/configuracoes/usuarios/editar")
async def settings_edit_user(
    request: Request,
    user_id: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    role: str = Form("Vendedor"),
    active: str = Form("1"),
    tab: str = Form("usuarios"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_user_error"] = "Apenas o administrador pode editar usuários."
        return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)

    from app.services.account_users import create_account_user, update_account_user

    try:
        if user_id.startswith("sheet:"):
            if not password.strip():
                raise ValueError("Informe uma senha para liberar o acesso deste usuário.")
            create_account_user(
                name=name,
                email=email,
                username=username,
                password=password,
                role=role,
                active=active == "1",
            )
            request.session["settings_user_success"] = "Acesso cadastrado com sucesso."
        else:
            update_account_user(
                user_id,
                name=name,
                email=email,
                username=username,
                role=role,
                active=active == "1",
                password=password or None,
            )
            request.session["settings_user_success"] = "Usuário atualizado com sucesso."
    except ValueError as error:
        request.session["settings_user_error"] = str(error)
    except Exception as error:
        request.session["settings_user_error"] = f"Não consegui salvar o usuário: {error}"

    return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)


@router.post("/configuracoes/usuarios/remover")
async def settings_remove_user(
    request: Request,
    user_id: str = Form(...),
    tab: str = Form("usuarios"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_user_error"] = "Apenas o administrador pode remover usuários."
        return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)

    if user_id.startswith("__admin__") or user_id.startswith("sheet:"):
        request.session["settings_user_error"] = "Este usuário não pode ser removido por aqui."
        return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)

    current_user_id = request.session.get("user_id", "")
    if current_user_id and user_id == current_user_id:
        request.session["settings_user_error"] = "Você não pode remover o usuário da sessão atual."
        return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)

    from app.services.account_users import delete_account_user

    try:
        delete_account_user(user_id)
        request.session["settings_user_success"] = "Usuário removido com sucesso."
    except ValueError as error:
        request.session["settings_user_error"] = str(error)
    except Exception as error:
        request.session["settings_user_error"] = f"Não consegui remover o usuário: {error}"

    return RedirectResponse(url="/configuracoes?tab=usuarios", status_code=303)


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
    from app.services.sheet_crm_storage import ensure_crm_storage_tabs
    from app.services.account_users import invalidate_account_users_cache, load_account_users
    from app.services.app_settings import invalidate_app_settings_cache, load_app_settings
    from app.services.monthly_goals import invalidate_monthly_goals_cache, load_monthly_goals
    from app.services.activities_storage import invalidate_activities_cache, reload_activities_store
    from app.services.lead_actions_storage import invalidate_lead_actions_cache, reload_lead_actions_store

    ensure_crm_storage_tabs()
    invalidate_account_users_cache()
    invalidate_app_settings_cache()
    invalidate_monthly_goals_cache()
    invalidate_activities_cache()
    invalidate_lead_actions_cache()
    load_account_users(force_refresh=True)
    load_app_settings(force_refresh=True)
    load_monthly_goals(force_refresh=True)
    reload_activities_store(force_refresh=True)
    reload_lead_actions_store(force_refresh=True)
    return RedirectResponse(url="/configuracoes", status_code=303)


@router.post("/configuracoes/seed/empresa-fake")
async def settings_seed_fake_company(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    try:
        from app.services.legacy_core import invalidate_sheet_cache
        from app.services.seed_fake_company import seed_fake_test_company

        user = normalize_text(request.session.get("username", "")) or "admin"
        result = seed_fake_test_company(user=user)
        invalidate_sheet_cache()
        request.session["settings_seed_fake_success"] = result["message"]
    except Exception as error:
        request.session["settings_seed_fake_error"] = f"Não consegui cadastrar a empresa FAKE: {error}"

    return RedirectResponse(url="/configuracoes?tab=integracoes", status_code=303)


@router.post("/configuracoes/seed/usuario-fake")
async def settings_seed_fake_user(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    try:
        from app.services.account_users import invalidate_account_users_cache, load_account_users
        from app.services.seed_fake_user import seed_fake_test_user

        result = seed_fake_test_user()
        invalidate_account_users_cache()
        load_account_users(force_refresh=True)
        request.session["settings_seed_fake_user_success"] = result["message"]
    except Exception as error:
        request.session["settings_seed_fake_user_error"] = f"Não consegui cadastrar o usuário FAKE: {error}"

    return RedirectResponse(url="/configuracoes?tab=integracoes", status_code=303)


@router.post("/configuracoes/seed/servico-fake")
async def settings_seed_fake_service(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    try:
        from app.services.app_settings import invalidate_app_settings_cache, load_app_settings
        from app.services.seed_fake_service import seed_fake_test_service

        result = seed_fake_test_service()
        invalidate_app_settings_cache()
        load_app_settings(force_refresh=True)
        request.session["settings_seed_fake_service_success"] = result["message"]
    except Exception as error:
        request.session["settings_seed_fake_service_error"] = f"Não consegui cadastrar o serviço FAKE: {error}"

    return RedirectResponse(url="/configuracoes?tab=integracoes", status_code=303)


@router.post("/configuracoes/planilha/sincronizar")
async def settings_sync_sheet(request: Request, apply: str = Form("0"), limit: str = Form("")):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_sheet_sync_error"] = "Somente administradores podem sincronizar a planilha."
        return RedirectResponse(url="/configuracoes?tab=integracoes", status_code=303)

    apply_changes = normalize_text(apply) in {"1", "on", "true", "yes", "sim"}
    row_limit: int | None = None
    if normalize_text(limit):
        try:
            row_limit = max(1, int(limit))
        except ValueError:
            request.session["settings_sheet_sync_error"] = "Limite inválido. Use apenas números."
            return RedirectResponse(url="/configuracoes?tab=integracoes", status_code=303)

    try:
        stats = sync_registration_rows(apply_changes=apply_changes, limit=row_limit)
        mode = "aplicada" if apply_changes else "simulada"
        request.session["settings_sheet_sync_success"] = (
            f"Sincronização {mode}: {stats['rows_updated']} de {stats['rows_seen']} linhas "
            f"{'corrigidas' if apply_changes else 'precisariam de correção'}."
        )
    except Exception as error:
        request.session["settings_sheet_sync_error"] = f"Não consegui sincronizar a planilha: {error}"

    return RedirectResponse(url="/configuracoes?tab=integracoes", status_code=303)
