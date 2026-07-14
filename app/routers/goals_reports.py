from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, require_auth
from app.services.filters import get_filter_options, parse_dashboard_filters
from app.services.goals_reports import MONTHS_PT, build_goals_page_context
from app.services.legacy_core import invalidate_sheet_cache
from app.templating import render

router = APIRouter()


def _parse_goals_params(request: Request, form: dict | None = None) -> dict:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    today = date.today()
    try:
        month = int(data.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month
    try:
        year = int(data.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year

    month = min(12, max(1, month))
    year = min(2100, max(2020, year))
    seller = data.get("seller", "Todos os vendedores")

    return {
        "month": month,
        "year": year,
        "seller": seller or "Todos os vendedores",
    }


def _goals_context(request: Request, filters, goals_params: dict):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    seller_options = ["Todos os vendedores"] + options["seller_options"]

    page = build_goals_page_context(
        df,
        columns,
        filters,
        month=goals_params["month"],
        year=goals_params["year"],
        seller=goals_params["seller"],
    )

    return {
        "active_page": "goals",
        "filters": filters,
        "options": options,
        "goals_params": goals_params,
        "seller_options": seller_options,
        "months_pt": MONTHS_PT,
        **page,
    }


@router.get("/metas-e-relatorios", response_class=HTMLResponse)
async def goals_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    goals_params = _parse_goals_params(request)
    return render(request, "goals/index.html", _goals_context(request, filters, goals_params))


@router.post("/metas-e-relatorios/filtros", response_class=HTMLResponse)
async def goals_filters(
    request: Request,
    month: int = Form(0),
    year: int = Form(0),
    seller: str = Form("Todos os vendedores"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    filters = parse_dashboard_filters(request)
    goals_params = _parse_goals_params(request, {
        "month": month or date.today().month,
        "year": year or date.today().year,
        "seller": seller,
    })
    return render(
        request,
        "partials/goals_content.html",
        _goals_context(request, filters, goals_params),
    )


@router.post("/metas-e-relatorios/atualizar")
async def goals_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/metas-e-relatorios", status_code=303)
