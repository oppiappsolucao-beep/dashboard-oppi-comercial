from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_admin
from app.services.asaas_client import invalidate_cache
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


def _page(request: Request, params: dict, *, force_sync: bool = False, flash: str = ""):
    ctx = build_financeiro_context(params, force_sync=force_sync)
    ctx["flash"] = flash
    return render(request, "financeiro/index.html", ctx)


@router.get("/financeiro", response_class=HTMLResponse)
async def financeiro_page(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    return _page(request, _params(request))


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
    denied = require_admin(request)
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
    denied = require_admin(request)
    if denied:
        return denied
    return RedirectResponse(url="/financeiro", status_code=303)


@router.post("/financeiro/sincronizar")
async def financeiro_sync(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    invalidate_cache()
    params = _params(request)
    return _page(request, params, force_sync=True, flash="Dados sincronizados com o Asaas.")
