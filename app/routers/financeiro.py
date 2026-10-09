import calendar
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_auth
from app.services.asaas_client import invalidate_cache
from app.services.company_payables import (
    create_payable,
    delete_payable,
    mark_payable_paid,
    parse_money,
    reopen_payable,
)
from app.services.financeiro import build_financeiro_context
from app.services.legacy_core import normalize_text
from app.templating import render

router = APIRouter()


def _params(request: Request, form: dict | None = None) -> dict:
    data = form or {}
    if not data and request.query_params:
        data = dict(request.query_params)
    return {
        "tab": normalize_text(data.get("tab") or "visao") or "visao",
        "status": normalize_text(data.get("status")),
        "forma": normalize_text(data.get("forma")),
        "search": normalize_text(data.get("search")),
        "period_start": normalize_text(data.get("period_start")),
        "period_end": normalize_text(data.get("period_end")),
    }


def _screen(
    request: Request,
    params: dict,
    template: str,
    active_page: str,
    *,
    force_sync: bool = False,
    flash: str = "",
):
    ctx = build_financeiro_context(params, force_sync=force_sync)
    ctx["flash"] = flash
    ctx["active_page"] = active_page
    return render(request, template, ctx)


def _page(request: Request, params: dict, *, force_sync: bool = False, flash: str = ""):
    return _screen(
        request,
        params,
        "financeiro/index.html",
        "financeiro",
        force_sync=force_sync,
        flash=flash,
    )


@router.get("/financeiro", response_class=HTMLResponse)
async def financeiro_page(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    params = _params(request)
    flash = normalize_text(request.query_params.get("flash"))
    return _page(request, params, flash=flash)


@router.get("/financeiro/entrada", response_class=HTMLResponse)
async def financeiro_entrada_page(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    params = _params(request)
    params["tab"] = "entradas"
    flash = normalize_text(request.query_params.get("flash"))
    return _screen(
        request,
        params,
        "financeiro/entrada.html",
        "entrada",
        flash=flash,
    )


@router.get("/financeiro/contas-a-pagar", response_class=HTMLResponse)
async def financeiro_pagar_page(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    params = _params(request)
    params["tab"] = "pagar"
    flash = normalize_text(request.query_params.get("flash"))
    return _screen(
        request,
        params,
        "financeiro/contas_a_pagar.html",
        "contas_pagar",
        flash=flash,
    )


@router.post("/financeiro/filtros", response_class=HTMLResponse)
async def financeiro_filters(
    request: Request,
    tab: str = Form("visao"),
    status: str = Form(""),
    forma: str = Form(""),
    search: str = Form(""),
    period_start: str = Form(""),
    period_end: str = Form(""),
):
    denied = require_auth(request)
    if denied:
        return denied
    params = {
        "tab": tab,
        "status": status,
        "forma": forma,
        "search": search,
        "period_start": period_start,
        "period_end": period_end,
    }
    ctx = build_financeiro_context(params)
    ctx["flash"] = ""
    return render(request, "partials/financeiro_content.html", ctx)


@router.post("/financeiro/atualizar")
async def financeiro_refresh(request: Request):
    denied = require_auth(request)
    if denied:
        return denied
    return RedirectResponse(url="/financeiro", status_code=303)


@router.post("/financeiro/sincronizar")
async def financeiro_sync(
    request: Request,
    destino: str = Form(""),
    period_start: str = Form(""),
    period_end: str = Form(""),
    search: str = Form(""),
):
    denied = require_auth(request)
    if denied:
        return denied
    invalidate_cache()
    params = {
        "tab": "visao",
        "status": "",
        "forma": "",
        "search": search,
        "period_start": period_start,
        "period_end": period_end,
    }
    flash = "Dados sincronizados com o Asaas."
    if normalize_text(destino) == "entrada":
        params["tab"] = "entradas"
        return _screen(
            request,
            params,
            "financeiro/entrada.html",
            "entrada",
            force_sync=True,
            flash=flash,
        )
    return _page(request, params, force_sync=True, flash=flash)


def _payable_back(period_start: str, period_end: str, flash: str) -> RedirectResponse:
    query = urlencode(
        {
            "period_start": period_start,
            "period_end": period_end,
            "flash": flash,
        }
    )
    return RedirectResponse(url=f"/financeiro/contas-a-pagar?{query}", status_code=303)


def _month_bounds(due: date) -> tuple[str, str]:
    last = calendar.monthrange(due.year, due.month)[1]
    start = date(due.year, due.month, 1)
    end = date(due.year, due.month, last)
    return start.isoformat(), end.isoformat()


@router.post("/financeiro/contas-a-pagar")
async def financeiro_payable_create(
    request: Request,
    description: str = Form(""),
    amount: str = Form(""),
    due_date: str = Form(""),
    period_start: str = Form(""),
    period_end: str = Form(""),
):
    denied = require_auth(request)
    if denied:
        return denied
    name = normalize_text(description)
    value = parse_money(amount)
    try:
        due = date.fromisoformat(normalize_text(due_date)[:10])
    except ValueError:
        due = None
    back_start = normalize_text(period_start)
    back_end = normalize_text(period_end)
    if not name or value is None or due is None:
        return _payable_back(back_start, back_end, "Informe descrição, valor e vencimento.")
    create_payable(name, value, due)
    start, end = _month_bounds(due)
    return _payable_back(start, end, "Conta a pagar lançada.")


@router.post("/financeiro/contas-a-pagar/{payable_id}/pagar")
async def financeiro_payable_pay(
    request: Request,
    payable_id: int,
    period_start: str = Form(""),
    period_end: str = Form(""),
):
    denied = require_auth(request)
    if denied:
        return denied
    mark_payable_paid(payable_id)
    return _payable_back(period_start, period_end, "Conta marcada como paga.")


@router.post("/financeiro/contas-a-pagar/{payable_id}/reabrir")
async def financeiro_payable_reopen(
    request: Request,
    payable_id: int,
    period_start: str = Form(""),
    period_end: str = Form(""),
):
    denied = require_auth(request)
    if denied:
        return denied
    reopen_payable(payable_id)
    return _payable_back(period_start, period_end, "Conta voltou para a pagar.")


@router.post("/financeiro/contas-a-pagar/{payable_id}/excluir")
async def financeiro_payable_delete(
    request: Request,
    payable_id: int,
    period_start: str = Form(""),
    period_end: str = Form(""),
):
    denied = require_auth(request)
    if denied:
        return denied
    delete_payable(payable_id)
    return _payable_back(period_start, period_end, "Conta excluída.")
