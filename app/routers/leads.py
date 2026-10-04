import csv
import io

from dataclasses import replace

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from app.dependencies import get_prepared_data, require_auth
from app.services.filters import apply_dashboard_filters, apply_last_days_period_filters, get_filter_options, parse_dashboard_filters
from app.services.lead_actions_storage import DEFAULT_TENANT_ID
from app.services.leads import (
    COMPANY_ALPHABET,
    ETAPA_STAGES,
    apply_leads_view,
    atualizar_proxima_acao_lead,
    build_company_alphabet,
    build_leads_export_rows,
    build_leads_kpi_cards,
    build_leads_table,
    build_raissa_empresas_table,
    raissa_kpi_cards,
)
from app.services.legacy_core import invalidate_sheet_cache, normalize_text
from app.templating import render

router = APIRouter()


def _parse_leads_params(request: Request, form: dict | None = None) -> dict:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    tab = data.get("tab", "empresas")
    stage = data.get("stage", "Todas as etapas")
    sort = data.get("sort", "recent")
    letter = normalize_text(data.get("letter") or "A").upper()[:1] or "A"
    if letter not in COMPANY_ALPHABET and letter != "#":
        letter = "A"
    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(data.get("per_page", 50))
    except (TypeError, ValueError):
        per_page = 50

    return {
        "tab": "empresas",
        "stage": stage,
        "sort": sort if sort in ("recent", "name") else "recent",
        "letter": letter,
        "page": max(1, page),
        "per_page": per_page if per_page in (10, 25, 50) else 50,
    }


def _leads_context(request: Request, filters, leads_params: dict):
    if leads_params.get("tab") == "empresas":
        raissa_context = _raissa_empresas_context(filters, leads_params)
        if raissa_context is not None:
            return raissa_context

    df, columns = get_prepared_data()
    options = get_filter_options(df)
    # Empresas: lista completa — sem janela de datas (migração não pode sumir por período).
    if leads_params.get("tab") == "empresas":
        filters = replace(
            filters,
            period_start=None,
            period_end=None,
            status="Todos os status",
        )
    else:
        filters = apply_last_days_period_filters(filters, days=7)
        filters = replace(filters, status="Todos os status")

    filtered_df = apply_dashboard_filters(df, columns, filters)
    empresas_df = apply_leads_view(
        filtered_df,
        tab=leads_params["tab"],
        stage=leads_params["stage"],
        sort=leads_params["sort"],
        tenant_id=DEFAULT_TENANT_ID,
        columns=columns,
    )
    searching = bool(normalize_text(filters.search))
    table = build_leads_table(
        filtered_df,
        columns,
        tab=leads_params["tab"],
        stage=leads_params["stage"],
        sort=leads_params["sort"],
        page=leads_params["page"],
        per_page=leads_params["per_page"],
        tenant_id=DEFAULT_TENANT_ID,
        letter=leads_params["letter"],
        apply_letter=not searching,
    )
    if searching:
        base_df = apply_dashboard_filters(df, columns, replace(filters, search=""))
        base_view = apply_leads_view(
            base_df,
            tab=leads_params["tab"],
            stage=leads_params["stage"],
            sort=leads_params["sort"],
            tenant_id=DEFAULT_TENANT_ID,
            columns=columns,
        )
        table["alphabet"] = build_company_alphabet(base_view)

    return {
        "active_page": "leads",
        "filters": filters,
        "options": options,
        "leads_params": leads_params,
        "stage_options": ["Todas as etapas"] + ETAPA_STAGES,
        "kpi_cards": build_leads_kpi_cards(empresas_df),
        "table": table,
        "raissa_aviso": "",
    }


def _raissa_empresas_context(filters, leads_params: dict):
    from app.services.campaign_leads import read_raissa_companies

    loaded = read_raissa_companies()
    empresas = loaded.get("empresas") or []
    searching = bool(normalize_text(filters.search))
    table, _page_rows = build_raissa_empresas_table(
        empresas,
        search=filters.search,
        letter=leads_params["letter"] or "A",
        page=leads_params["page"],
        per_page=leads_params["per_page"],
        apply_letter=not searching,
    )
    if searching:
        full_table, _rows = build_raissa_empresas_table(
            empresas,
            search="",
            letter=leads_params["letter"],
            page=1,
            per_page=leads_params["per_page"],
            apply_letter=False,
        )
        table["alphabet"] = full_table["alphabet"]
    return {
        "active_page": "leads",
        "filters": filters,
        "options": {
            "seller_options": [],
            "niche_options": [],
            "state_options": [],
            "date_min": None,
            "date_max": None,
            "has_reference_dates": False,
            "status_options": [],
            "total_companies": len(empresas),
            "total_capital": 0.0,
        },
        "leads_params": leads_params,
        "stage_options": ["Todas as etapas"],
        "kpi_cards": raissa_kpi_cards(empresas),
        "table": table,
        "raissa_aviso": "" if empresas else (loaded.get("aviso") or "A aba Raissa não trouxe empresas."),
        "raissa_total": loaded.get("total") or len(empresas),
    }


@router.get("/api/raissa/clientes")
async def api_raissa_clientes(request: Request):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse(
            {"aba": "", "total": 0, "colunas": [], "clientes": [], "aviso": "Faça login para consultar a aba Raissa."},
            status_code=401,
        )
    from app.services.campaign_leads import read_raissa_sheet

    return JSONResponse(read_raissa_sheet())


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
    tab: str = Form("leads"),
    stage: str = Form("Todas as etapas"),
    sort: str = Form("recent"),
    letter: str = Form("A"),
    page: int = Form(1),
    per_page: int = Form(10),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    filters = parse_dashboard_filters(request, {
        "seller": seller,
        "status": "Todos os status",
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
        "letter": letter,
        "page": page,
        "per_page": per_page,
    })
    return render(request, "partials/leads_content.html", _leads_context(request, filters, leads_params))


@router.post("/leads-e-empresas/{sheet_row}/proxima-acao")
async def leads_update_next_action(
    request: Request,
    sheet_row: int,
    next_action: str = Form(...),
):
    redirect = require_auth(request)
    if redirect:
        return JSONResponse({"error": "Não autenticado."}, status_code=401)

    user = normalize_text(request.session.get("username", "")) or "Usuário"
    try:
        normalized = atualizar_proxima_acao_lead(DEFAULT_TENANT_ID, sheet_row, next_action, user)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse({"ok": True, "next_action": normalized})


@router.get("/leads-e-empresas/exportar")
async def leads_export(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    filters = parse_dashboard_filters(request)
    leads_params = _parse_leads_params(request)
    from app.services.campaign_leads import read_raissa_companies

    loaded = read_raissa_companies()
    searching = bool(normalize_text(filters.search))
    table, _listed = build_raissa_empresas_table(
        loaded.get("empresas") or [],
        search=filters.search,
        letter=leads_params["letter"] or "A",
        page=1,
        per_page=max(len(loaded.get("empresas") or []), 1),
        apply_letter=not searching,
    )
    rows = [
        {
            "nome": item["nome"],
            "empresa": item["empresa"],
            "tipo_label": item["tipo_label"],
            "telefone": item["telefone"],
            "email": item["email"],
            "etapa": item["etapa"],
            "vendedor": item["vendedor"],
            "ultimo_contato": item["ultimo_contato"],
            "closed_services_title": item["closed_services_title"],
            "closed_services_meta": item["closed_services_meta"],
        }
        for item in table["rows"]
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Nome", "Empresa", "Tipo", "Telefone", "E-mail",
        "Etapa", "Responsável", "Último contato", "Serviços fechados", "Proposta",
    ])
    for row in rows:
        writer.writerow([
            row["nome"], row["empresa"], row["tipo_label"], row["telefone"], row["email"],
            row["etapa"], row["vendedor"], row["ultimo_contato"],
            row["closed_services_title"], row["closed_services_meta"],
        ])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=empresas-raissa.csv"},
    )


@router.post("/leads-e-empresas/atualizar")
async def leads_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/leads-e-empresas", status_code=303)
