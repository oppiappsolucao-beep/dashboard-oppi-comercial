from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.activity_service import (
    ActivitiesViewParams,
    atualizar_atividade_inline,
    build_activity_page_context,
    cancelar_atividade,
)
from app.services.activities_storage import DEFAULT_TENANT_ID, soft_delete_activity
from app.services.filters import apply_default_period_filters, get_filter_options, parse_dashboard_filters
from app.services.followup_service import apply_seller_scope
from app.services.legacy_core import invalidate_sheet_cache, normalize_text
from app.templating import render

router = APIRouter()


def _parse_activities_params(request: Request, form: dict | None = None) -> ActivitiesViewParams:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(data.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10

    tab = data.get("tab", "todas")
    return ActivitiesViewParams(
        tab=tab if tab in ("todas", "pendentes", "concluidas", "atrasadas") else "todas",
        activity_type=data.get("activity_type", "Todos os tipos"),
        channel=data.get("channel", "Todos os canais"),
        responsible=data.get("responsible", "Todos os responsáveis"),
        stage=data.get("stage", "Todas as etapas"),
        page=max(1, page),
        per_page=per_page if per_page in (10, 25, 50) else 10,
    )


def _activities_context(request: Request, filters, activities_params: ActivitiesViewParams, success: str = "", error: str = ""):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))

    ctx = build_activity_page_context(df, columns, filters, activities_params, DEFAULT_TENANT_ID)
    responsible_options = ["Todos os responsáveis"] + options["seller_options"]

    return {
        "active_page": "activities",
        "filters": filters,
        "options": options,
        "activities_params": activities_params,
        "responsible_options": responsible_options,
        "is_admin": is_admin(request),
        "success": success or request.session.pop("activities_success", ""),
        "error": error or request.session.pop("activities_error", ""),
        **ctx,
    }


def _render_content(request, filters, activities_params, success="", error=""):
    return render(
        request,
        "partials/activities_content.html",
        _activities_context(request, filters, activities_params, success, error),
    )


@router.get("/atividades", response_class=HTMLResponse)
async def activities_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    activities_params = _parse_activities_params(request)
    return render(request, "activities/index.html", _activities_context(request, filters, activities_params))


@router.post("/atividades/filtros", response_class=HTMLResponse)
async def activities_filters(
    request: Request,
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    tab: str = Form("todas"),
    activity_type: str = Form("Todos os tipos"),
    channel: str = Form("Todos os canais"),
    responsible: str = Form("Todos os responsáveis"),
    stage: str = Form("Todas as etapas"),
    page: int = Form(1),
    per_page: int = Form(10),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    filters = parse_dashboard_filters(request, {
        "seller": seller,
        "status": status,
        "period_start": period_start,
        "period_end": period_end,
        "niche": niche,
        "state": state,
        "search": search,
    })
    activities_params = _parse_activities_params(request, {
        "tab": tab,
        "activity_type": activity_type,
        "channel": channel,
        "responsible": responsible,
        "stage": stage,
        "page": page,
        "per_page": per_page,
    })
    return _render_content(request, filters, activities_params)


@router.post("/atividades/{activity_id}/salvar", response_class=HTMLResponse)
async def activities_save_inline(
    request: Request,
    activity_id: str,
    status: str = Form("pendente"),
    result: str = Form(""),
    next_action: str = Form(""),
    next_action_date: str = Form(""),
    next_action_time: str = Form("09:00"),
    next_action_channel: str = Form("WhatsApp"),
    channel: str = Form("WhatsApp"),
    assigned_user_id: str = Form(""),
    note: str = Form(""),
    result_notes: str = Form(""),
    move_stage: str = Form(""),
    scheduled_date: str = Form(""),
    scheduled_time: str = Form(""),
    tab: str = Form("todas"),
    activity_type: str = Form("Todos os tipos"),
    channel_filter: str = Form("Todos os canais"),
    responsible: str = Form("Todos os responsáveis"),
    stage_filter: str = Form("Todas as etapas"),
    page: int = Form(1),
    per_page: int = Form(10),
    seller: str = Form("Todos os vendedores"),
    status_filter: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    _, error = atualizar_atividade_inline(
        DEFAULT_TENANT_ID,
        activity_id,
        {
            "status": status,
            "result": result,
            "next_action": next_action,
            "next_action_date": next_action_date,
            "next_action_time": next_action_time,
            "next_action_channel": next_action_channel,
            "channel": channel,
            "assigned_user_id": assigned_user_id,
            "note": note,
            "result_notes": result_notes,
            "move_stage": move_stage,
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
        },
        user,
    )

    filters = parse_dashboard_filters(request, {
        "seller": seller,
        "status": status_filter,
        "period_start": period_start,
        "period_end": period_end,
        "niche": niche,
        "state": state,
        "search": search,
    })
    activities_params = _parse_activities_params(request, {
        "tab": tab,
        "activity_type": activity_type,
        "channel": channel_filter,
        "responsible": responsible,
        "stage": stage_filter,
        "page": page,
        "per_page": per_page,
    })

    if error:
        return _render_content(request, filters, activities_params, error=error)
    return _render_content(request, filters, activities_params, success="Atividade atualizada com sucesso.")


@router.post("/atividades/{activity_id}/cancelar", response_class=HTMLResponse)
async def activities_cancel(request: Request, activity_id: str, reason: str = Form("")):
    redirect = require_auth(request)
    if redirect:
        return redirect
    user = normalize_text(request.session.get("username", "")) or "Usuário"
    cancelar_atividade(DEFAULT_TENANT_ID, activity_id, user, reason)
    filters = parse_dashboard_filters(request)
    return _render_content(request, filters, _parse_activities_params(request), success="Atividade cancelada.")


@router.post("/atividades/{activity_id}/excluir", response_class=HTMLResponse)
async def activities_delete(request: Request, activity_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        filters = parse_dashboard_filters(request)
        return _render_content(request, filters, _parse_activities_params(request), error="Sem permissão para excluir.")
    soft_delete_activity(DEFAULT_TENANT_ID, activity_id)
    filters = parse_dashboard_filters(request)
    return _render_content(request, filters, _parse_activities_params(request), success="Atividade excluída.")


@router.post("/atividades/atualizar")
async def activities_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/atividades", status_code=303)
