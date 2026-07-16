from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, is_admin, require_auth
from app.services.filters import (
    DashboardFilters,
    apply_default_period_filters,
    get_filter_options,
    parse_dashboard_filters,
)
from app.services.followup_service import (
    OperationalFilters,
    apply_seller_scope,
    build_operational_overview_context,
    parse_operational_filters,
)
from app.services.legacy_core import invalidate_sheet_cache, normalize_text
from app.services.lead_actions_storage import DEFAULT_TENANT_ID, complete_activity, get_lead_action
from app.templating import render

router = APIRouter()


def _overview_context(request: Request, filters: DashboardFilters, operational: OperationalFilters, success: str = ""):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)
    filters = apply_seller_scope(request, filters, options["seller_options"], is_admin(request))

    operational_ctx = build_operational_overview_context(
        df,
        columns,
        filters,
        operational,
        tenant_id=DEFAULT_TENANT_ID,
    )

    return {
        "active_page": "overview",
        "success": success or request.session.pop("company_registration_success", "") or request.session.pop("overview_success", ""),
        "overview_error": request.session.pop("overview_error", ""),
        "filters": filters,
        "options": options,
        "columns": columns,
        "is_admin": is_admin(request),
        "current_user": normalize_text(request.session.get("username", "")),
        **operational_ctx,
    }


@router.get("/visao-geral", response_class=HTMLResponse)
async def overview_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    operational = parse_operational_filters(request)
    return render(request, "overview.html", _overview_context(request, filters, operational))


@router.post("/visao-geral/filtros", response_class=HTMLResponse)
async def overview_filters(
    request: Request,
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    op_priority: str = Form("Todas"),
    op_stage: str = Form("Todas as etapas"),
    op_action_type: str = Form("Todos os tipos"),
    op_status: str = Form("Todos"),
    op_channel: str = Form("Todos os canais"),
    overdue_only: str = Form(""),
    no_next_action_only: str = Form(""),
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
    operational = parse_operational_filters(request, {
        "op_priority": op_priority,
        "op_stage": op_stage,
        "op_action_type": op_action_type,
        "op_status": op_status,
        "op_channel": op_channel,
        "overdue_only": overdue_only,
        "no_next_action_only": no_next_action_only,
    })
    ctx = _overview_context(request, filters, operational)
    return render(request, "partials/overview_content.html", ctx)


@router.post("/visao-geral/atualizar")
async def overview_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/visao-geral", status_code=303)


@router.get("/visao-geral/acoes/{sheet_row}/concluir", response_class=HTMLResponse)
async def overview_complete_action_form(request: Request, sheet_row: int):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    row_match = df[df["_sheet_row"] == sheet_row]
    if row_match.empty:
        return RedirectResponse(url="/visao-geral", status_code=303)

    row = row_match.iloc[0]
    stored = get_lead_action(DEFAULT_TENANT_ID, sheet_row) or {}
    return render(
        request,
        "overview_complete_action.html",
        {
            "active_page": "overview",
            "sheet_row": sheet_row,
            "empresa": normalize_text(row.get("_empresa", "")) or "—",
            "stored": stored,
            "today": date.today().isoformat(),
            "overview_error": request.session.pop("overview_error", ""),
        },
    )


@router.post("/visao-geral/acoes/{sheet_row}/concluir")
async def overview_complete_action_submit(
    request: Request,
    sheet_row: int,
    result: str = Form(...),
    note: str = Form(""),
    next_action_date: str = Form(""),
    next_action_time: str = Form("09:00"),
    next_action_type: str = Form("whatsapp"),
    next_action_description: str = Form(""),
    move_stage: str = Form(""),
    lead_closed: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    parsed_date = None
    if next_action_date:
        try:
            parsed_date = date.fromisoformat(next_action_date)
        except ValueError:
            parsed_date = None

    if not lead_closed and not parsed_date:
        request.session["overview_error"] = "Defina a próxima ação antes de concluir."
        return RedirectResponse(url=f"/visao-geral/acoes/{sheet_row}/concluir", status_code=303)

    complete_activity(
        DEFAULT_TENANT_ID,
        sheet_row,
        result=result,
        note=note,
        user=user,
        next_action_date=parsed_date,
        next_action_time=next_action_time,
        next_action_type=next_action_type,
        next_action_description=next_action_description,
        move_stage=move_stage,
    )
    request.session["overview_success"] = "Atividade concluída e histórico atualizado."
    return RedirectResponse(url="/visao-geral", status_code=303)
