from datetime import date
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.activity_service import (
    ActivitiesViewParams,
    atualizar_atividade_inline,
    build_activity_detail_panel,
    build_activity_page_context,
    build_activity_timeline_for_activity,
    build_new_activity_modal_context,
    buscar_acoes_por_etapa,
    buscar_leads_para_atividade,
    cancelar_atividade,
    criar_atividade,
    mover_atividade_kanban,
    sugerir_fluxo_por_resultado,
    atualizar_proxima_acao_atividade,
    _serialize_activity,
)
from app.services.activities_storage import DEFAULT_TENANT_ID, get_activity, soft_delete_activity
from app.services.filters import apply_default_period_filters, apply_dashboard_filters, get_filter_options, parse_dashboard_filters
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
        "current_user": normalize_text(request.session.get("username", "")),
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


def _modal_context(request: Request, error: str = ""):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(parse_dashboard_filters(request), df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))
    current_user = normalize_text(request.session.get("username", "")) or "Usuário"
    return build_new_activity_modal_context(
        seller_options=options["seller_options"],
        current_user=current_user,
        is_admin_user=is_admin(request),
        today_iso=date.today().isoformat(),
        error=error,
    )


@router.get("/atividades", response_class=HTMLResponse)
async def activities_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    activities_params = _parse_activities_params(request)
    return render(request, "activities/index.html", _activities_context(request, filters, activities_params))


@router.get("/atividades/nova/modal", response_class=HTMLResponse)
async def activities_new_modal(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return render(request, "partials/activities_new_modal.html", _modal_context(request))


@router.get("/atividades/{activity_id}/painel", response_class=HTMLResponse)
async def activities_detail_panel(request: Request, activity_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(parse_dashboard_filters(request), df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))
    scoped_df = apply_dashboard_filters(df, columns, filters)

    panel = build_activity_detail_panel(DEFAULT_TENANT_ID, activity_id, scoped_df, columns)
    if not panel:
        return HTMLResponse("Atividade não encontrada.", status_code=404)

    return render(request, "partials/activities_detail_panel.html", panel)


@router.post("/atividades/{activity_id}/proxima-acao")
async def activities_update_next_action(
    request: Request,
    activity_id: str,
    next_action: str = Form(...),
):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse({"error": "Não autenticado."}, status_code=401)

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    normalized, error = atualizar_proxima_acao_atividade(DEFAULT_TENANT_ID, activity_id, next_action, user)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    df, columns = get_prepared_data()
    timeline = build_activity_timeline_for_activity(DEFAULT_TENANT_ID, activity_id, df, columns)
    record = get_activity(DEFAULT_TENANT_ID, activity_id)
    stage = _serialize_activity(record, DEFAULT_TENANT_ID).get("stage", "") if record else ""
    return JSONResponse({
        "ok": True,
        "next_action": normalized,
        "timeline": timeline,
        "stage": stage,
    })


@router.post("/atividades/{activity_id}/mover-etapa", response_class=HTMLResponse)
async def activities_move_stage(
    request: Request,
    activity_id: str,
    stage_target: str = Form(...),
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
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    _, error = mover_atividade_kanban(DEFAULT_TENANT_ID, activity_id, stage_target, user)

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
    })

    if error:
        return _render_content(request, filters, activities_params, error=error)
    return _render_content(
        request,
        filters,
        activities_params,
        success=f"Atividade movida para {stage_target}.",
    )


@router.get("/atividades/api/leads")
async def activities_search_leads(request: Request, q: str = ""):
    redirect = require_auth(request)
    if redirect:
        return redirect
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(parse_dashboard_filters(request), df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))
    scoped_df = apply_dashboard_filters(df, columns, filters)
    current_user = normalize_text(request.session.get("username", ""))
    leads = buscar_leads_para_atividade(
        scoped_df,
        columns,
        DEFAULT_TENANT_ID,
        q,
        current_user=current_user,
        is_admin_user=is_admin(request),
    )
    return JSONResponse({"items": leads})


@router.get("/atividades/api/acoes")
async def activities_stage_actions(request: Request, stage: str = "Novo Lead"):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return JSONResponse({"items": buscar_acoes_por_etapa(stage)})


@router.get("/atividades/api/sugerir-resultado")
async def activities_suggest_result(request: Request, result: str = "", stage: str = ""):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return JSONResponse(sugerir_fluxo_por_resultado(result, stage))


@router.post("/atividades/nova", response_class=HTMLResponse)
async def activities_create(
    request: Request,
    sheet_row: int = Form(0),
    empresa: str = Form(""),
    contato: str = Form(""),
    stage: str = Form(""),
    activity_type: str = Form(""),
    process_action: str = Form(""),
    channel: str = Form("WhatsApp"),
    channel_other: str = Form(""),
    assigned_user_id: str = Form(""),
    scheduled_date: str = Form(""),
    scheduled_time: str = Form("09:00"),
    status: str = Form("pendente", alias="activity_status"),
    priority: str = Form("Média"),
    description: str = Form(""),
    result: str = Form(""),
    note: str = Form(""),
    next_action: str = Form(""),
    next_action_date: str = Form(""),
    next_action_time: str = Form("10:00"),
    next_action_channel: str = Form(""),
    next_action_assigned: str = Form(""),
    move_stage: str = Form(""),
    move_stage_confirm: str = Form(""),
    lost_reason: str = Form(""),
    close_value: str = Form(""),
    close_payment: str = Form(""),
    tab: str = Form("todas"),
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
    admin_user = is_admin(request)

    df, columns = get_prepared_data()
    if sheet_row:
        row_match = df[df["_sheet_row"] == sheet_row]
        if row_match.empty:
            response = render(request, "partials/activities_new_modal.html", _modal_context(request, "Lead não encontrado."))
            response.status_code = 422
            response.headers["HX-Retarget"] = "#activity-modal-root"
            response.headers["HX-Reswap"] = "innerHTML"
            return response
        row = row_match.iloc[0]
        from app.services.activity_service import _lead_is_accessible
        if not _lead_is_accessible(row, user, admin_user):
            response = render(request, "partials/activities_new_modal.html", _modal_context(request, "Sem permissão para este lead."))
            response.status_code = 403
            response.headers["HX-Retarget"] = "#activity-modal-root"
            response.headers["HX-Reswap"] = "innerHTML"
            return response

    payload = {
        "sheet_row": sheet_row,
        "empresa": empresa,
        "contato": contato,
        "stage": stage,
        "activity_type": activity_type,
        "process_action": process_action,
        "channel": channel,
        "channel_other": channel_other,
        "assigned_user_id": assigned_user_id,
        "scheduled_date": scheduled_date,
        "scheduled_time": scheduled_time,
        "status": status,
        "priority": priority,
        "description": description,
        "result": result,
        "note": note,
        "next_action": next_action,
        "next_action_date": next_action_date,
        "next_action_time": next_action_time,
        "next_action_channel": next_action_channel,
        "next_action_assigned": next_action_assigned,
        "move_stage": move_stage,
        "move_stage_confirm": move_stage_confirm,
        "lost_reason": lost_reason,
        "close_value": close_value,
        "close_payment": close_payment,
    }

    _, error = criar_atividade(DEFAULT_TENANT_ID, payload, user, is_admin_user=admin_user)
    if error:
        response = render(request, "partials/activities_new_modal.html", _modal_context(request, error))
        response.status_code = 422
        response.headers["HX-Retarget"] = "#activity-modal-root"
        response.headers["HX-Reswap"] = "innerHTML"
        return response

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
        "page": page,
        "per_page": per_page,
    })
    response = _render_content(request, filters, activities_params, success="Atividade criada com sucesso.")
    response.headers["HX-Trigger"] = json.dumps({"activityModalClose": True})
    return response


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
