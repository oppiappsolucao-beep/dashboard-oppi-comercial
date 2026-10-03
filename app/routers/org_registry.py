from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_auth
from app.services.legacy_core import normalize_text
from app.services.org_registry import (
    ACCESS_OPTIONS,
    BRAZIL_UFS,
    list_people,
    list_sectors,
    remove_person,
    remove_sector,
    save_person,
    save_sector,
)
from app.templating import render

router = APIRouter()

TABS = {"funcionarios", "setores", "representantes"}


def _tab(value: str) -> str:
    tab = normalize_text(value).lower()
    return tab if tab in TABS else "funcionarios"


def _page(request: Request, tab: str):
    return render(
        request,
        "org/index.html",
        {
            "active_page": "org_registry",
            "active_tab": tab,
            "sectors": list_sectors(),
            "funcionarios": list_people("funcionario"),
            "representantes": list_people("representante"),
            "access_options": ACCESS_OPTIONS,
            "ufs": BRAZIL_UFS,
            "success": request.session.pop("org_success", ""),
            "error": request.session.pop("org_error", ""),
        },
    )


@router.get("/cadastros", response_class=HTMLResponse)
async def org_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    return _page(request, _tab(request.query_params.get("tipo", "")))


@router.post("/cadastros/setores")
async def org_save_sector(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        saved = save_sector(
            sector_id=normalize_text(form.get("sector_id")) or "novo",
            name=form.get("sector_name", ""),
            accesses=form.getlist("access"),
        )
    except ValueError as error:
        request.session["org_error"] = str(error)
    else:
        request.session["org_success"] = f"Setor {saved['name']} salvo com os acessos permitidos."
    return RedirectResponse(url="/cadastros?tipo=setores", status_code=303)


@router.post("/cadastros/setores/{sector_id}/remover")
async def org_remove_sector(request: Request, sector_id: str):
    redirect = require_auth(request)
    if redirect:
        return redirect
    remove_sector(sector_id)
    request.session["org_success"] = "Setor removido."
    return RedirectResponse(url="/cadastros?tipo=setores", status_code=303)


@router.post("/cadastros/pessoas")
async def org_save_person(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    form = await request.form()
    kind = normalize_text(form.get("kind")).lower()
    tab = "representantes" if kind == "representante" else "funcionarios"
    try:
        person = save_person(
            kind=kind,
            name=form.get("name", ""),
            sector_id=form.get("sector_id", ""),
            email=form.get("email", ""),
            phone=form.get("phone", ""),
            region=form.get("region", ""),
            username=form.get("username", ""),
            password=form.get("password", ""),
            state_name=form.get("state_name", ""),
            city=form.get("city", ""),
        )
    except ValueError as error:
        request.session["org_error"] = str(error)
    else:
        label = "Representante" if kind == "representante" else "Funcionário"
        if person.get("sector_name"):
            request.session["org_success"] = f"{label} {person['name']} cadastrado no setor {person['sector_name']}."
        else:
            request.session["org_success"] = f"{label} {person['name']} cadastrado."
    return RedirectResponse(url=f"/cadastros?tipo={tab}", status_code=303)


@router.post("/cadastros/pessoas/{person_id}/remover")
async def org_remove_person(request: Request, person_id: str, kind: str = ""):
    redirect = require_auth(request)
    if redirect:
        return redirect
    form = await request.form()
    kind = normalize_text(form.get("kind") or kind).lower()
    remove_person(person_id)
    tab = "representantes" if kind == "representante" else "funcionarios"
    request.session["org_success"] = "Cadastro removido."
    return RedirectResponse(url=f"/cadastros?tipo={tab}", status_code=303)
