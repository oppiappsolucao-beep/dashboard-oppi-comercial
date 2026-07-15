from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, require_auth
from app.services.filters import apply_dashboard_filters, apply_default_period_filters, get_filter_options, parse_dashboard_filters
from app.services.legacy_core import invalidate_sheet_cache
from app.services.overview import (
    build_calls_table,
    build_funnel_page_actions,
    build_funnel_page_kpi_cards,
    build_funnel_page_steps,
)
from app.templating import render

router = APIRouter()


def _funnel_context(request: Request, filters):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)

    filtered_df = apply_dashboard_filters(df, columns, filters)

    return {
        "active_page": "funnel",
        "filters": filters,
        "options": options,
        "kpi_cards": build_funnel_page_kpi_cards(df, columns, filters),
        "funnel_steps": build_funnel_page_steps(filtered_df),
        "action_items": build_funnel_page_actions(filtered_df),
        "calls_table": build_calls_table(
            filtered_df,
            columns,
            None,
            filters.search,
            filters.status,
        ),
    }


@router.get("/funil-de-vendas", response_class=HTMLResponse)
async def funnel_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    return render(request, "funnel/index.html", _funnel_context(request, filters))


@router.post("/funil-de-vendas/filtros", response_class=HTMLResponse)
async def funnel_filters(
    request: Request,
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
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
    return render(request, "partials/funnel_content.html", _funnel_context(request, filters))


@router.post("/funil-de-vendas/atualizar")
async def funnel_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/funil-de-vendas", status_code=303)
