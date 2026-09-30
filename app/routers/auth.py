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
            "notice": request.session.pop("auth_notice", ""),
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

        from app.services.account_users import (
            get_account_user_by_username,
            touch_account_user_last_access,
        )

        managed_user = get_account_user_by_username(username.strip())
        if managed_user:
            request.session["user_id"] = managed_user["id"]
            request.session["user_role"] = managed_user["role"]
            touch_account_user_last_access(managed_user["id"])
        else:
            request.session["user_id"] = ""
            request.session["user_role"] = "Administrador"

        return RedirectResponse(url="/visao-geral", status_code=303)

    request.session["auth_error"] = "Usuário ou senha inválidos."
    return RedirectResponse(url="/login", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _recovery_context(request: Request, **extra):
    from app.services.legacy_core import get_logo_data_uri

    context = {
        "logo_uri": get_logo_data_uri(),
        "error": request.session.pop("recovery_error", ""),
        "info": request.session.pop("recovery_info", ""),
        "username": request.session.get("recovery_username", ""),
    }
    context.update(extra)
    return context


@router.get("/recuperar-senha", response_class=HTMLResponse)
async def recover_request_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/visao-geral", status_code=303)
    return render(request, "recover_password.html", _recovery_context(request, step="request"))


@router.post("/recuperar-senha")
async def recover_request_submit(request: Request, username: str = Form(...)):
    from app.services.password_recovery import request_reset_code

    clean = username.strip()
    request.session["recovery_username"] = clean
    request_reset_code(clean)
    request.session["recovery_info"] = (
        "Se o usuário existir, o código foi registrado no log do serviço comercial "
        "e vale por 20 minutos."
    )
    return RedirectResponse(url="/recuperar-senha/codigo", status_code=303)


@router.get("/recuperar-senha/codigo", response_class=HTMLResponse)
async def recover_code_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/visao-geral", status_code=303)
    return render(request, "recover_password.html", _recovery_context(request, step="code"))


@router.post("/recuperar-senha/codigo")
async def recover_code_submit(
    request: Request,
    username: str = Form(...),
    code: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    from app.services.password_recovery import complete_password_reset

    request.session["recovery_username"] = username.strip()
    if password != password_confirm:
        request.session["recovery_error"] = "As senhas não conferem."
        return RedirectResponse(url="/recuperar-senha/codigo", status_code=303)

    error = complete_password_reset(username, code, password)
    if error:
        request.session["recovery_error"] = error
        return RedirectResponse(url="/recuperar-senha/codigo", status_code=303)

    request.session.pop("recovery_username", None)
    request.session["auth_error"] = ""
    request.session["recovery_info"] = ""
    request.session["auth_notice"] = "Senha alterada. Entre com a nova senha."
    return RedirectResponse(url="/login", status_code=303)
