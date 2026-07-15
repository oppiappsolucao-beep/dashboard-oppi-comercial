from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_prepared_data, require_auth
from app.services.filters import apply_dashboard_filters, apply_default_period_filters, get_filter_options, parse_dashboard_filters
from app.services.legacy_core import invalidate_sheet_cache
from app.services.proposals import (
    PROPOSAL_STATUS_OPTIONS,
    build_proposals_kpi_cards,
    build_proposals_table,
    default_proposal_chat_messages,
    handle_proposal_chat_message,
    render_proposal_chat_messages,
)
from app.templating import render

router = APIRouter()


def _get_chat_messages(request: Request) -> list[dict]:
    messages = request.session.get("proposals_chat")
    if not messages:
        messages = default_proposal_chat_messages()
        request.session["proposals_chat"] = messages
    return messages


def _parse_proposals_params(request: Request, form: dict | None = None) -> dict:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)

    status_filter = data.get("status_filter", "Todos os status")
    try:
        page = int(data.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(data.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10

    return {
        "status_filter": status_filter if status_filter in PROPOSAL_STATUS_OPTIONS else "Todos os status",
        "page": max(1, page),
        "per_page": per_page if per_page in (10, 25, 50) else 10,
    }


def _proposals_context(request: Request, filters, proposals_params: dict):
    df, columns = get_prepared_data()
    options = get_filter_options(df)
    filters = apply_default_period_filters(filters, df)

    filtered_df = apply_dashboard_filters(df, columns, filters)
    chat_messages = _get_chat_messages(request)

    return {
        "active_page": "proposals",
        "filters": filters,
        "options": options,
        "proposals_params": proposals_params,
        "status_options": PROPOSAL_STATUS_OPTIONS,
        "kpi_cards": build_proposals_kpi_cards(df, columns, filters),
        "table": build_proposals_table(
            filtered_df,
            status_filter=proposals_params["status_filter"],
            page=proposals_params["page"],
            per_page=proposals_params["per_page"],
        ),
        "chat_messages_html": render_proposal_chat_messages(chat_messages),
    }


@router.get("/propostas", response_class=HTMLResponse)
async def proposals_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    filters = parse_dashboard_filters(request)
    proposals_params = _parse_proposals_params(request)
    return render(request, "proposals/index.html", _proposals_context(request, filters, proposals_params))


@router.post("/propostas/filtros", response_class=HTMLResponse)
async def proposals_filters(
    request: Request,
    seller: str = Form("Todos os vendedores"),
    status: str = Form("Todos os status"),
    period_start: str = Form(""),
    period_end: str = Form(""),
    niche: str = Form("Todos os nichos"),
    state: str = Form("Todos os estados"),
    search: str = Form(""),
    status_filter: str = Form("Todos os status"),
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
    proposals_params = _parse_proposals_params(request, {
        "status_filter": status_filter,
        "page": page,
        "per_page": per_page,
    })
    return render(
        request,
        "partials/proposals_content.html",
        _proposals_context(request, filters, proposals_params),
    )


@router.post("/propostas/chat", response_class=HTMLResponse)
async def proposals_chat(request: Request, message: str = Form(...)):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    chat_messages = _get_chat_messages(request)
    chat_messages = handle_proposal_chat_message(message, df, chat_messages)
    request.session["proposals_chat"] = chat_messages

    return render(
        request,
        "partials/proposals_chat.html",
        {"chat_messages_html": render_proposal_chat_messages(chat_messages)},
    )


@router.post("/propostas/atualizar")
async def proposals_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/propostas", status_code=303)


@router.post("/propostas/chat/reset")
async def proposals_chat_reset(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    request.session["proposals_chat"] = default_proposal_chat_messages()
    return RedirectResponse(url="/propostas", status_code=303)
