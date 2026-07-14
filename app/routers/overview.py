from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.templating import render
from app.services.filters import DashboardFilters, apply_dashboard_filters, get_filter_options
from app.services.legacy_core import invalidate_sheet_cache
from app.services.overview import (
    build_calls_table,
    build_status_cards,
    build_status_summary,
    build_weekly_chart_json,
    compute_overview_metrics,
)

router = APIRouter()


def _parse_filters(request: Request, form: dict | None = None) -> DashboardFilters:
    data = form or {}
    period_start = data.get("period_start") or request.query_params.get("period_start")
    period_end = data.get("period_end") or request.query_params.get("period_end")

    def to_date(value):
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None

    return DashboardFilters(
        seller=data.get("seller") or request.query_params.get("seller", "Todos os vendedores"),
        status=data.get("status") or request.query_params.get("status", "Todos os status"),
        period_start=to_date(period_start),
        period_end=to_date(period_end),
        niche=data.get("niche") or request.query_params.get("niche", "Todos os nichos"),
        state=data.get("state") or request.query_params.get("state", "Todos os estados"),
        search=data.get("search") or request.query_params.get("search", ""),
        selected_card_status=data.get("selected_card_status") or request.query_params.get("selected_card_status"),
    )


def _overview_context(request: Request, filters: DashboardFilters, success: str = ""):
    df, columns = get_prepared_data()
    options = get_filter_options(df)

    if not filters.period_start:
        filters.period_start = options["date_min"]
    if not filters.period_end:
        filters.period_end = options["date_max"]

    filtered_df = apply_dashboard_filters(df, columns, filters)
    metrics = compute_overview_metrics(filtered_df)

    return {
        "active_page": "overview",
        "success": success or request.session.pop("company_registration_success", ""),
        "filters": filters,
        "options": options,
        "metrics": metrics,
        "chart_json": build_weekly_chart_json(filtered_df),
        "status_summary": build_status_summary(filtered_df),
        "status_cards": build_status_cards(filtered_df),
        "calls_table": build_calls_table(
            filtered_df,
            columns,
            filters.selected_card_status,
            filters.search,
            filters.status,
        ),
        "columns": columns,
    }


@router.get("/visao-geral", response_class=HTMLResponse)
async def overview_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = _parse_filters(request)
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

    filters = _parse_filters(request, {
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
