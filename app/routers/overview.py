from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.dependencies import get_prepared_data, require_auth
from app.templating import render
from app.services.filters import (
    DashboardFilters,
    apply_dashboard_filters,
    get_filter_options,
    parse_dashboard_filters,
)
from app.services.legacy_core import invalidate_sheet_cache
from app.services.overview import (
    build_conversion_donut_json,
    build_daily_actions,
    build_hot_opportunities,
    build_overdue_activities,
    build_overview_funnel,
    build_overview_kpi_cards,
)

router = APIRouter()


def _overview_context(request: Request, filters: DashboardFilters, success: str = ""):
    df, columns = get_prepared_data()
    options = get_filter_options(df)

    if not filters.period_start:
        filters.period_start = options["date_min"]
    if not filters.period_end:
        filters.period_end = options["date_max"]

    filtered_df = apply_dashboard_filters(df, columns, filters)

    overview_funnel = build_overview_funnel(filtered_df)

    return {
        "active_page": "overview",
        "success": success or request.session.pop("company_registration_success", ""),
        "filters": filters,
        "options": options,
        "kpi_cards": build_overview_kpi_cards(df, columns, filters),
        "overview_funnel": overview_funnel,
        "conversion_donut_json": build_conversion_donut_json(overview_funnel["conversion"]),
        "daily_actions": build_daily_actions(filtered_df, columns),
        "hot_opportunities": build_hot_opportunities(filtered_df),
        "overdue_activities": build_overdue_activities(filtered_df, columns),
        "columns": columns,
    }


@router.get("/visao-geral", response_class=HTMLResponse)
async def overview_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    return render(request, "overview.html", _overview_context(request, filters))


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
    selected_card_status: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    filters = parse_dashboard_filters(request, {
        "seller": seller, "status": status,
        "period_start": period_start, "period_end": period_end,
        "niche": niche, "state": state, "search": search,
        "selected_card_status": selected_card_status or None,
    })
    ctx = _overview_context(request, filters)
    return render(request, "partials/overview_content.html", ctx)


@router.post("/visao-geral/atualizar")
async def overview_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/visao-geral", status_code=303)
