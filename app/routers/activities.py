from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, require_auth
from app.services.activities import ACTIVITY_TYPES, build_activities_kpi_cards, build_activities_list, build_activities_table
from app.services.filters import apply_dashboard_filters, apply_default_period_filters, get_filter_options, parse_dashboard_filters
from app.services.legacy_core import invalidate_sheet_cache
from app.templating import render

router = APIRouter()


def _parse_activities_params(request: Request, form: dict | None = None) -> dict:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    tab = data.get("tab", "todas")
    activity_type = data.get("activity_type", "Todos os tipos")
    responsible = data.get("responsible", "Todos os responsáveis")
    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(data.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10

    return {
        "tab": tab if tab in ("todas", "pendentes", "concluidas", "atrasadas") else "todas",
        "activity_type": activity_type,
        "responsible": responsible,
        "page": max(1, page),
        "per_page": per_page if per_page in (10, 25, 50) else 10,
    }


def _activities_context(request: Request, filters, activities_params: dict):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)

    filtered_df = apply_dashboard_filters(df, columns, filters)
    activities = build_activities_list(filtered_df, columns)
    table = build_activities_table(
        filtered_df,
        columns,
        tab=activities_params["tab"],
        activity_type=activities_params["activity_type"],
        responsible=activities_params["responsible"],
        page=activities_params["page"],
        per_page=activities_params["per_page"],
    )

    responsible_options = ["Todos os responsáveis"] + options["seller_options"]

    return {
        "active_page": "activities",
        "filters": filters,
        "options": options,
        "activities_params": activities_params,
        "type_options": ["Todos os tipos"] + ACTIVITY_TYPES,
        "responsible_options": responsible_options,
        "kpi_cards": build_activities_kpi_cards(activities),
        "table": table,
    }


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
    responsible: str = Form("Todos os responsáveis"),
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
        "responsible": responsible,
        "page": page,
        "per_page": per_page,
    })
    return render(
        request,
        "partials/activities_content.html",
        _activities_context(request, filters, activities_params),
    )


@router.post("/atividades/atualizar")
async def activities_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/atividades", status_code=303)
