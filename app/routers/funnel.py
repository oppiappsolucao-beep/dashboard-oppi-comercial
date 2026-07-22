from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config.crm_options import PIPELINE_STAGE_OPTIONS
from app.dependencies import get_prepared_data, require_auth
from app.services.filters import apply_dashboard_filters, apply_default_period_filters, get_filter_options, parse_dashboard_filters
from app.services.lead_actions_storage import DEFAULT_TENANT_ID
from app.services.leads import build_leads_table
from app.services.legacy_core import invalidate_sheet_cache, normalize_text
from app.services.overview import (
    build_funnel_page_actions,
    build_funnel_page_kpi_cards,
    build_funnel_page_steps,
    build_funnel_process_chart_json,
    build_funnel_value_chart_json,
)
from app.templating import render

router = APIRouter()

FUNNEL_TAB_OPTIONS = ("leads", "todos", "empresas")
FUNNEL_SORT_OPTIONS = ("recent", "name", "value")


def _parse_funnel_view_params(source) -> dict:
    raw_tab = normalize_text(source.get("tab") if hasattr(source, "get") else "")
    tab = raw_tab if raw_tab in FUNNEL_TAB_OPTIONS else "leads"
    raw_stage = normalize_text(source.get("stage") if hasattr(source, "get") else "") or "Todas as etapas"
    stage = raw_stage if raw_stage == "Todas as etapas" or raw_stage in PIPELINE_STAGE_OPTIONS else "Todas as etapas"
    raw_sort = normalize_text(source.get("sort") if hasattr(source, "get") else "")
    sort = raw_sort if raw_sort in FUNNEL_SORT_OPTIONS else "recent"
    try:
        page = max(1, int(source.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(source.get("per_page") or 10)
    except (TypeError, ValueError):
        per_page = 10
    if per_page not in {10, 25, 50}:
        per_page = 10
    return {"tab": tab, "stage": stage, "sort": sort, "page": page, "per_page": per_page}


def _funnel_context(request: Request, filters, view_params: dict | None = None):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)
    view_params = view_params or _parse_funnel_view_params(request.query_params)

    filtered_df = apply_dashboard_filters(df, columns, filters)
    funnel_steps = build_funnel_page_steps(filtered_df, tenant_id=DEFAULT_TENANT_ID)
    leads_table = build_leads_table(
        filtered_df,
        columns,
        tab=view_params["tab"],
        stage=view_params["stage"],
        sort=view_params["sort"],
        page=view_params["page"],
        per_page=view_params["per_page"],
        tenant_id=DEFAULT_TENANT_ID,
    )

    return {
        "active_page": "funnel",
        "filters": filters,
        "options": options,
        "funnel_params": view_params,
        "stage_options": ["Todas as etapas", *PIPELINE_STAGE_OPTIONS],
        "kpi_cards": build_funnel_page_kpi_cards(df, columns, filters, tenant_id=DEFAULT_TENANT_ID),
        "funnel_steps": funnel_steps,
        "process_chart_json": build_funnel_process_chart_json(funnel_steps),
        "value_chart_json": build_funnel_value_chart_json(filtered_df, tenant_id=DEFAULT_TENANT_ID),
        "action_items": build_funnel_page_actions(filtered_df),
        "leads_table": leads_table,
    }


@router.get("/funil-de-vendas", response_class=HTMLResponse)
async def funnel_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    view_params = _parse_funnel_view_params(request.query_params)
    return render(request, "funnel/index.html", _funnel_context(request, filters, view_params))


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
    tab: str = Form("leads"),
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
    view_params = _parse_funnel_view_params({
        "tab": tab,
        "stage": stage,
        "sort": sort,
        "page": page,
        "per_page": per_page,
    })
    return render(request, "partials/funnel_content.html", _funnel_context(request, filters, view_params))


@router.post("/funil-de-vendas/atualizar")
async def funnel_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/funil-de-vendas", status_code=303)
