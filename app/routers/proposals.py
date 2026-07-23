from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import get_prepared_data, require_auth
from app.services.filters import apply_dashboard_filters, apply_default_period_filters, get_filter_options, parse_dashboard_filters
from app.services.commercial_services import get_commercial_service_options
from app.services.legacy_core import invalidate_sheet_cache, normalize_text
from app.services.proposal_pdf import generate_proposal_pdf, proposal_pdf_filename
from app.services.proposals import (
    PROPOSAL_STATUS_OPTIONS,
    build_proposal_company_options,
    build_proposal_form_message,
    build_proposals_kpi_cards,
    build_proposals_table,
    default_proposal_chat_messages,
    get_generated_proposal,
    handle_proposal_chat_message,
    clear_generated_proposal,
    strip_proposal_pdf_cards,
    render_proposal_chat_messages,
    should_show_proposal_quick_form,
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


def _proposals_chat_context(request: Request, chat_messages: list[dict], df) -> dict:
    return {
        "chat_messages_html": render_proposal_chat_messages(chat_messages),
        "show_quick_form": should_show_proposal_quick_form(chat_messages),
        "company_options": build_proposal_company_options(df),
        "service_options": get_commercial_service_options(),
        "pending_company": normalize_text(request.session.get("proposals_pending_company")),
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
        "generated_proposal": get_generated_proposal(request),
        **_proposals_chat_context(request, chat_messages, df),
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
async def proposals_chat(
    request: Request,
    message: str = Form(""),
    empresa: str = Form(""),
    servico: str = Form(""),
    valor_proposta: str = Form(""),
    colaboradores: str = Form(""),
    services_description: str = Form(""),
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    chat_messages = _get_chat_messages(request)
    pending = normalize_text(request.session.get("proposals_pending_company"))

    if empresa.strip() and not services_description.strip() and not message.strip():
        message = build_proposal_form_message(empresa)
    elif services_description.strip():
        message = services_description.strip()
        if not empresa.strip() and pending:
            empresa = pending

    chat_messages, generated, new_pending = handle_proposal_chat_message(
        message,
        df,
        chat_messages,
        columns,
        servico=servico or None,
        colaboradores=colaboradores or None,
        company_override=empresa.strip() or None,
        pending_company=pending or None,
        services_description=services_description.strip() or None,
    )
    request.session["proposals_chat"] = chat_messages
    if new_pending:
        request.session["proposals_pending_company"] = new_pending
    elif generated:
        request.session.pop("proposals_pending_company", None)
    if generated:
        request.session["proposals_generated"] = generated

    return render(
        request,
        "partials/proposals_chat_response.html",
        {
            "generated_proposal": generated,
            **_proposals_chat_context(request, chat_messages, df),
        },
    )


@router.post("/propostas/atualizar")
async def proposals_refresh(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    invalidate_sheet_cache()
    return RedirectResponse(url="/propostas", status_code=303)


@router.post("/propostas/gerada/excluir", response_class=HTMLResponse)
async def proposals_delete_generated(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    clear_generated_proposal(request)
    chat_messages = strip_proposal_pdf_cards(_get_chat_messages(request))
    request.session["proposals_chat"] = chat_messages

    df, _columns = get_prepared_data()
    return render(
        request,
        "partials/proposals_delete_response.html",
        _proposals_chat_context(request, chat_messages, df),
    )


@router.post("/propostas/chat/reset")
async def proposals_chat_reset(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    request.session["proposals_chat"] = default_proposal_chat_messages()
    request.session.pop("proposals_generated", None)
    request.session.pop("proposals_pending_company", None)
    return RedirectResponse(url="/propostas", status_code=303)


@router.get("/propostas/{empresa:path}/pdf")
async def proposals_pdf(
    request: Request,
    empresa: str,
    valor: str = "",
    servico: str = "",
    colaboradores: str = "",
    servicos: str = "",
):
    redirect = require_auth(request)
    if redirect:
        return redirect

    generated = request.session.get("proposals_generated") or {}
    plans_text = ""
    services_description = servicos
    if isinstance(generated, dict) and normalize_text(generated.get("company")) == normalize_text(empresa):
        valor = valor or generated.get("value") or ""
        servico = servico or generated.get("servico") or ""
        colaboradores = colaboradores or generated.get("colaboradores") or ""
        services_description = services_description or generated.get("services_description") or ""
        plans_text = generated.get("plans_text") or ""

    df, columns = get_prepared_data()
    try:
        pdf_bytes = generate_proposal_pdf(
            empresa,
            df,
            columns,
            value=valor or None,
            servico=servico or None,
            colaboradores=colaboradores or None,
            services_description=services_description or None,
            plans_text=plans_text or None,
        )
    except Exception as error:
        return HTMLResponse(f"<p>Erro ao gerar PDF: {error}</p>", status_code=500)

    filename = proposal_pdf_filename(empresa)
    inline = request.query_params.get("inline") == "1"
    disposition = "inline" if inline else "attachment"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )
