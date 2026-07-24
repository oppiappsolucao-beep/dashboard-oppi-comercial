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
    USERS_SUBTABS,
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
    # Compatibilidade com URLs antigas
    if tab == "metas":
        tab = "usuarios"
        data = {**data, "subtab": data.get("subtab") or "metas"}
    if tab == "setores":
        tab = "usuarios"
        data = {**data, "subtab": data.get("subtab") or "setores"}

    valid_tabs = {tab_id for tab_id, _ in SETTINGS_TABS}
    valid_subtabs = {tab_id for tab_id, _ in USERS_SUBTABS}
    subtab = data.get("subtab", "usuarios")
    if data.get("edit_user_id"):
        subtab = "usuarios"
    if tab == "usuarios" and subtab not in valid_subtabs:
        subtab = "usuarios"
    if tab != "usuarios":
        subtab = ""

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
        "subtab": subtab,
        "search": data.get("search", ""),
        "role": data.get("role", "Todos os perfis"),
        "page": max(1, page),
        "per_page": per_page if per_page in (10, 25, 50) else 10,
        "edit_user_id": data.get("edit_user_id", ""),
    }


def _settings_context(request: Request, settings_params: dict):
    df, _columns = get_prepared_data()
    integrations = build_integrations()

    niches_rows: list[dict] = []
    sectors_rows: list[dict] = []
    attendance_tags_rows: list[dict] = []
    account_users_options: list[dict] = []
    try:
        from app.services.niches import list_niches_rows
        from app.services.sectors import list_sectors
        from app.services.attendance_tags import list_attendance_tags
        from app.services.account_users import load_account_users

        niches_rows = list_niches_rows()
        sectors_rows = list_sectors(active_only=False)
        attendance_tags_rows = list_attendance_tags(active_only=False)
        account_users_options = [
            {
                "id": u.get("id"),
                "name": u.get("name") or u.get("username") or "",
                "active": bool(u.get("active", True)),
            }
            for u in load_account_users()
            if u.get("id") and u.get("active", True)
        ]
    except Exception:
        niches_rows = []
        sectors_rows = []
        attendance_tags_rows = []
        account_users_options = []

    return {
        "active_page": "settings",
        "settings_params": settings_params,
        "settings_tabs": SETTINGS_TABS,
        "users_subtabs": USERS_SUBTABS,
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
        "niches": niches_rows,
        "sectors": sectors_rows,
        "attendance_tags": attendance_tags_rows,
        "sector_options": [{"id": s["id"], "name": s["name"]} for s in sectors_rows if s.get("active", True)],
        "account_users_options": account_users_options,
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
        "niche_success": request.session.pop("settings_niche_success", ""),
        "niche_error": request.session.pop("settings_niche_error", ""),
        "sector_success": request.session.pop("settings_sector_success", ""),
        "sector_error": request.session.pop("settings_sector_error", ""),
        "tag_success": request.session.pop("settings_tag_success", ""),
        "tag_error": request.session.pop("settings_tag_error", ""),
        "user_success": request.session.pop("settings_user_success", ""),
        "user_error": request.session.pop("settings_user_error", ""),
        "sheet_sync_success": request.session.pop("settings_sheet_sync_success", ""),
        "sheet_sync_error": request.session.pop("settings_sheet_sync_error", ""),
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
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=metas", status_code=303)

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

    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=metas", status_code=303)


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
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=metas", status_code=303)

    try:
        delete_monthly_goal(year, month, seller)
        request.session["settings_goal_success"] = "Meta removida com sucesso."
    except ValueError as error:
        request.session["settings_goal_error"] = str(error) if str(error) else "Meta não encontrada."
    except Exception as error:
        request.session["settings_goal_error"] = f"Não consegui remover a meta: {error}"

    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=metas", status_code=303)


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


@router.post("/configuracoes/nichos/adicionar")
async def settings_add_niche(
    request: Request,
    niche_name: str = Form(...),
    tab: str = Form("nichos"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        request.session["settings_niche_error"] = "Apenas o administrador pode cadastrar nichos."
        return RedirectResponse(url="/configuracoes?tab=nichos", status_code=303)
    from app.services.niches import add_niche

    try:
        add_niche(niche_name)
        request.session["settings_niche_success"] = "Nicho cadastrado com sucesso."
    except ValueError as error:
        request.session["settings_niche_error"] = str(error)
    except Exception as error:
        request.session["settings_niche_error"] = f"Não consegui cadastrar o nicho: {error}"
    return RedirectResponse(url="/configuracoes?tab=nichos", status_code=303)


@router.post("/configuracoes/nichos/remover")
async def settings_remove_niche(
    request: Request,
    niche_name: str = Form(...),
    tab: str = Form("nichos"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        request.session["settings_niche_error"] = "Apenas o administrador pode remover nichos."
        return RedirectResponse(url="/configuracoes?tab=nichos", status_code=303)
    from app.services.niches import remove_niche

    try:
        remove_niche(niche_name)
        request.session["settings_niche_success"] = "Nicho removido/desativado."
    except ValueError as error:
        request.session["settings_niche_error"] = str(error)
    except Exception as error:
        request.session["settings_niche_error"] = f"Não consegui remover o nicho: {error}"
    return RedirectResponse(url="/configuracoes?tab=nichos", status_code=303)


@router.post("/configuracoes/setores/adicionar")
async def settings_add_sector(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        request.session["settings_sector_error"] = "Apenas o administrador pode cadastrar setores."
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=setores", status_code=303)

    form = await request.form()
    from app.services.sectors import add_sector, link_users_to_sector

    user_ids = form.getlist("user_ids") if hasattr(form, "getlist") else []
    try:
        created = add_sector(form.get("sector_name", ""), user_ids=list(user_ids))
        link_users_to_sector(created["id"], list(user_ids))
        request.session["settings_sector_success"] = "Setor cadastrado e usuários vinculados."
    except ValueError as error:
        request.session["settings_sector_error"] = str(error)
    except Exception as error:
        request.session["settings_sector_error"] = f"Não consegui cadastrar o setor: {error}"
    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=setores", status_code=303)


@router.post("/configuracoes/setores/editar")
async def settings_edit_sector(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        request.session["settings_sector_error"] = "Apenas o administrador pode editar setores."
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=setores", status_code=303)

    form = await request.form()
    from app.services.sectors import link_users_to_sector, update_sector

    user_ids = form.getlist("user_ids") if hasattr(form, "getlist") else []
    try:
        update_sector(form.get("sector_id"), name=form.get("sector_name"))
        link_users_to_sector(form.get("sector_id"), list(user_ids))
        request.session["settings_sector_success"] = "Setor e vínculos atualizados."
    except ValueError as error:
        request.session["settings_sector_error"] = str(error)
    except Exception as error:
        request.session["settings_sector_error"] = f"Não consegui atualizar o setor: {error}"
    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=setores", status_code=303)


@router.post("/configuracoes/setores/remover")
async def settings_remove_sector(
    request: Request,
    sector_id: str = Form(...),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        request.session["settings_sector_error"] = "Apenas o administrador pode remover setores."
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=setores", status_code=303)
    from app.services.sectors import delete_sector

    try:
        delete_sector(sector_id)
        request.session["settings_sector_success"] = "Setor removido."
    except ValueError as error:
        request.session["settings_sector_error"] = str(error)
    except Exception as error:
        request.session["settings_sector_error"] = f"Não consegui remover o setor: {error}"
    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=setores", status_code=303)


@router.post("/configuracoes/tags/adicionar")
async def settings_add_tag(
    request: Request,
    tag_name: str = Form(...),
    tab: str = Form("atendimentos"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        request.session["settings_tag_error"] = "Apenas o administrador pode cadastrar tags."
        return RedirectResponse(url="/configuracoes?tab=atendimentos", status_code=303)
    from app.services.attendance_tags import add_attendance_tag

    try:
        add_attendance_tag(tag_name)
        request.session["settings_tag_success"] = "Tag cadastrada com sucesso."
    except ValueError as error:
        request.session["settings_tag_error"] = str(error)
    except Exception as error:
        request.session["settings_tag_error"] = f"Não consegui cadastrar a tag: {error}"
    return RedirectResponse(url="/configuracoes?tab=atendimentos", status_code=303)


@router.post("/configuracoes/tags/remover")
async def settings_remove_tag(
    request: Request,
    tag_name: str = Form(...),
    tab: str = Form("atendimentos"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        request.session["settings_tag_error"] = "Apenas o administrador pode remover tags."
        return RedirectResponse(url="/configuracoes?tab=atendimentos", status_code=303)
    from app.services.attendance_tags import remove_attendance_tag

    try:
        remove_attendance_tag(tag_name)
        request.session["settings_tag_success"] = "Tag removida/desativada."
    except ValueError as error:
        request.session["settings_tag_error"] = str(error)
    except Exception as error:
        request.session["settings_tag_error"] = f"Não consegui remover a tag: {error}"
    return RedirectResponse(url="/configuracoes?tab=atendimentos", status_code=303)


@router.post("/configuracoes/usuarios/adicionar")
async def settings_add_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("Vendedor"),
    active: str = Form("1"),
    department_id: str = Form(""),
    tab: str = Form("usuarios"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_user_error"] = "Apenas o administrador pode cadastrar usuários."
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)

    from app.services.account_users import create_account_user

    try:
        create_account_user(
            name=name,
            email=email,
            username=username,
            password=password,
            role=role,
            active=active == "1",
            department_id=department_id,
        )
        request.session["settings_user_success"] = "Usuário cadastrado com sucesso."
    except ValueError as error:
        request.session["settings_user_error"] = str(error)
    except Exception as error:
        request.session["settings_user_error"] = f"Não consegui cadastrar o usuário: {error}"

    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)


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
    department_id: str = Form(""),
    tab: str = Form("usuarios"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    if not is_admin(request):
        request.session["settings_user_error"] = "Apenas o administrador pode editar usuários."
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)

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
                department_id=department_id,
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
                department_id=department_id,
            )
            request.session["settings_user_success"] = "Usuário atualizado com sucesso."
    except ValueError as error:
        request.session["settings_user_error"] = str(error)
    except Exception as error:
        request.session["settings_user_error"] = f"Não consegui salvar o usuário: {error}"

    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)


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
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)

    if user_id.startswith("__admin__") or user_id.startswith("sheet:"):
        request.session["settings_user_error"] = "Este usuário não pode ser removido por aqui."
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)

    current_user_id = request.session.get("user_id", "")
    if current_user_id and user_id == current_user_id:
        request.session["settings_user_error"] = "Você não pode remover o usuário da sessão atual."
        return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)

    from app.services.account_users import delete_account_user

    try:
        delete_account_user(user_id)
        request.session["settings_user_success"] = "Usuário removido com sucesso."
    except ValueError as error:
        request.session["settings_user_error"] = str(error)
    except Exception as error:
        request.session["settings_user_error"] = f"Não consegui remover o usuário: {error}"

    return RedirectResponse(url="/configuracoes?tab=usuarios&subtab=usuarios", status_code=303)


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
