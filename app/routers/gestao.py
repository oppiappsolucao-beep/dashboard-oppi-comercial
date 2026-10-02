"""Gestão comercial — espelho do tráfego por data. Começa zerado."""
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.dependencies import require_auth
from app.templating import render

router = APIRouter(tags=["gestao"])


@router.get("/gestao", response_class=HTMLResponse)
async def gestao_page(request: Request):
    require_auth(request)
    today = date.today().isoformat()
    inicio = (request.query_params.get("inicio") or today).strip() or today
    fim = (request.query_params.get("fim") or today).strip() or today
    return render(
        request,
        "gestao/index.html",
        {
            "active_page": "gestao",
            "inicio": inicio,
            "fim": fim,
        },
    )
