from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.dependencies import get_prepared_data, require_auth
from app.templating import render
from app.services.legacy_core import DuplicateRegistrationError, STATUS_OPTIONS, normalize_text
from app.services.registration import get_seller_options, save_new_company

router = APIRouter()


@router.get("/cadastro/novo", response_class=HTMLResponse)
async def new_registration_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    df, columns = get_prepared_data()
    return render(
        request,
        "registration/new.html",
        {
            "active_page": "registration_new",
            "seller_options": get_seller_options(df),
            "status_options": STATUS_OPTIONS,
            "today": date.today().isoformat(),
            "error": request.session.pop("registration_error", ""),
        },
    )


@router.post("/cadastro/novo")
async def new_registration_submit(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect

    form = await request.form()
    form_dict = dict(form)

    try:
        save_new_company(form_dict)
        empresa = normalize_text(form_dict.get("empresa"))
        status = normalize_text(form_dict.get("status"))
        request.session["company_registration_success"] = (
            f'Empresa "{empresa}" cadastrada com sucesso na planilha com o status "{status}".'
        )
        return RedirectResponse(url="/visao-geral", status_code=303)
    except DuplicateRegistrationError as error:
        request.session["registration_error"] = str(error)
    except ValueError as error:
        request.session["registration_error"] = str(error)
    except Exception as error:
        request.session["registration_error"] = f"Não consegui cadastrar a empresa: {error}"

    return RedirectResponse(url="/cadastro/novo", status_code=303)
