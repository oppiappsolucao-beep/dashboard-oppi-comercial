"""Agenda dos treinamentos marcados para cada treinador."""
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.dependencies import require_auth
from app.services.legacy_core import normalize_text
from app.services.service_orders import list_training_appointments
from app.templating import render

router = APIRouter()


@router.get("/agenda", response_class=HTMLResponse)
async def agenda_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    kind = normalize_text(request.session.get("org_person_kind"))
    name = normalize_text(request.session.get("org_person_name")) if kind == "treinador" else ""
    items = list_training_appointments(name)
    today = date.today().isoformat()
    upcoming = [item for item in items if (item.get("scheduled_date") or "") >= today]
    past = [item for item in items if (item.get("scheduled_date") or "") < today]
    return render(
        request,
        "agenda/index.html",
        {
            "active_page": "agenda",
            "own_agenda": kind == "treinador",
            "trainer_name": name,
            "upcoming": upcoming,
            "past": past,
        },
    )
