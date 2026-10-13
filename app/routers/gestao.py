"""Gestão de tráfego — espelho do Meta Ads por período."""
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.dependencies import require_auth
from app.services.meta_ads import MetaAdsError, load_campaign_mirror
from app.templating import render

router = APIRouter(tags=["gestao"])


@router.get("/gestao", response_class=HTMLResponse)
async def gestao_page(request: Request):
    require_auth(request)
    today = date.today().isoformat()
    inicio = (request.query_params.get("inicio") or today).strip() or today
    fim = (request.query_params.get("fim") or today).strip() or today
    try:
        mirror = load_campaign_mirror(inicio, fim)
    except MetaAdsError as exc:
        mirror = {
            "configured": True,
            "error": str(exc),
            "account_name": "",
            "rows": [],
            "totals": {
                "impressions_label": "0",
                "clicks_label": "0",
                "spend_label": "R$ 0,00",
                "ctr_label": "0,00%",
                "conversations_label": "0",
                "cost_label": "—",
                "ads_label": "0",
            },
            "start": inicio,
            "end": fim,
        }
    return render(
        request,
        "gestao/index.html",
        {
            "active_page": "gestao",
            "inicio": mirror["start"],
            "fim": mirror["end"],
            "mirror": mirror,
        },
    )
