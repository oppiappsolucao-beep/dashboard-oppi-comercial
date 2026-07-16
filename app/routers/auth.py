from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import check_credentials
from app.services.legacy_core import get_logo_data_uri
from app.templating import render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/visao-geral", status_code=303)
    return render(
        request,
        "login.html",
        {
            "logo_uri": get_logo_data_uri(),
            "error": request.session.pop("auth_error", ""),
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if check_credentials(username, password):
        request.session["authenticated"] = True
        request.session["username"] = username.strip()
        request.session["auth_error"] = ""
        return RedirectResponse(url="/visao-geral", status_code=303)

    request.session["auth_error"] = "Usuário ou senha inválidos."
    return RedirectResponse(url="/login", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
