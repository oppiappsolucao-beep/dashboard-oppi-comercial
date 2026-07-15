from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, require_auth
from app.services.filters import apply_dashboard_filters, apply_default_period_filters, get_filter_options, parse_dashboard_filters
from app.services.leads import ETAPA_STAGES, build_leads_kpi_cards, build_leads_table
from app.services.legacy_core import invalidate_sheet_cache
from app.templating import render

router = APIRouter()


def _parse_leads_params(request: Request, form: dict | None = None) -> dict:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    tab = data.get("tab", "todos")
    stage = data.get("stage", "Todas as etapas")
    sort = data.get("sort", "recent")
    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(data.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10

    return {
        "tab": tab if tab in ("todos", "leads", "empresas") else "todos",
        "stage": stage,
        "sort": sort if sort in ("recent", "name", "value") else "recent",
        "page": max(1, page),
        "per_page": per_page if per_page in (10, 25, 50) else 10,
    }


def _leads_context(request: Request, filters, leads_params: dict):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)

    filtered_df = apply_dashboard_filters(df, columns, filters)
    table = build_leads_table(
        filtered_df,
        columns,
        tab=leads_params["tab"],
        stage=leads_params["stage"],
        sort=leads_params["sort"],
        page=leads_params["page"],
        per_page=leads_params["per_page"],
    )

    return {
        "active_page": "leads",
        "filters": filters,
        "options": options,
        "leads_params": leads_params,
        "stage_options": ["Todas as etapas"] + ETAPA_STAGES,
        "kpi_cards": build_leads_kpi_cards(filtered_df),
        "table": table,
    }


@router.get("/leads-e-empresas", response_class=HTMLResponse)
async def leads_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    leads_params = _parse_leads_params(request)
    return render(request, "leads/index.html", _leads_context(request, filters, leads_params))


@router.post("/leads-e-empresas/filtros", response_class=HTMLResponse)
async def leads_filters(
    request: Request,
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    tab: str = Form("todos"),
    stage: str = Form("Todas as etapas"),
    sort: str = Form("recent"),
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
    leads_params = _parse_leads_params(request, {
        "tab": tab,
        "stage": stage,
        "sort": sort,
        "page": page,
        "per_page": per_page,
    })
    return render(request, "partials/leads_content.html", _leads_context(request, filters, leads_params))


@router.post("/leads-e-empresas/atualizar")
async def leads_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/leads-e-empresas", status_code=303)
