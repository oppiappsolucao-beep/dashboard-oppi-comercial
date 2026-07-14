from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.dependencies import get_prepared_data, get_pricing_store, require_auth
from app.services.legacy_core import (
    OPPI_PRICING_STEPS,
    _diagnostic_add_answer,
    _diagnostic_ensure_thread,
    _diagnostic_get_progress,
    _diagnostic_initials,
    _diagnostic_render_messages,
    _diagnostic_reset,
    _pricing_generate_pdf,
    _pricing_pdf_safe_filename,
    _pricing_question_text,
    normalize_search_text,
    normalize_text,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _company_list(df, search: str = "") -> list[dict]:
    companies = sorted({
        normalize_text(c) for c in df["_empresa"].tolist() if normalize_text(c)
    })
    term = normalize_search_text(search)
    if term:
        companies = [c for c in companies if term in normalize_search_text(c)]

    return [
        {
            "name": company,
            "initials": _diagnostic_initials(company),
            "snippet": _company_snippet(company),
        }
        for company in companies
    ]


def _company_snippet(company: str) -> str:
    messages = _diagnostic_ensure_thread(company)
    last = normalize_text(messages[-1].get("content")) if messages else ""
    return last[:48] + ("..." if len(last) > 48 else "")


def _pricing_progress(company: str) -> dict:
    progress = _diagnostic_get_progress()
    current = int(progress.get(company, 0))
    total = len(OPPI_PRICING_STEPS)
    percent = min(100, round((current / total) * 100)) if total else 0
    return {"current": current, "total": total, "percent": percent}


@router.get("/pesos-medidas", response_class=HTMLResponse)
async def pricing_page(request: Request, empresa: str = "", search: str = ""):
    redirect = require_auth(request)
    if redirect:
        return redirect

    get_pricing_store(request)
    df, columns = get_prepared_data()
    companies = _company_list(df, search)

    if not companies:
        return templates.TemplateResponse(
            "pricing/index.html",
            {
                "request": request,
                "active_page": "pricing",
                "companies": [],
                "selected_company": "",
                "messages_html": "",
                "progress": {"current": 0, "total": len(OPPI_PRICING_STEPS), "percent": 0},
                "search": search,
            },
        )

    selected = empresa or companies[0]["name"]
    if selected not in [c["name"] for c in companies]:
        selected = companies[0]["name"]

    messages = _diagnostic_ensure_thread(selected)

    return templates.TemplateResponse(
        "pricing/index.html",
        {
            "request": request,
            "active_page": "pricing",
            "companies": companies,
            "selected_company": selected,
            "messages_html": _diagnostic_render_messages(messages),
            "progress": _pricing_progress(selected),
            "search": search,
            "company_encoded": quote(selected),
        },
    )


@router.post("/pesos-medidas/{empresa:path}/mensagem", response_class=HTMLResponse)
async def pricing_message(request: Request, empresa: str, message: str = Form(...)):
    redirect = require_auth(request)
    if redirect:
        return redirect

    get_pricing_store(request)
    _diagnostic_add_answer(empresa, message)
    messages = _diagnostic_ensure_thread(empresa)

    return templates.TemplateResponse(
        "partials/pricing_chat.html",
        {
            "request": request,
            "selected_company": empresa,
            "messages_html": _diagnostic_render_messages(messages),
            "progress": _pricing_progress(empresa),
            "company_encoded": quote(empresa),
        },
    )


@router.post("/pesos-medidas/{empresa:path}/reset")
async def pricing_reset(request: Request, empresa: str):
    redirect = require_auth(request)
    if redirect:
        return redirect

    get_pricing_store(request)
    _diagnostic_reset(empresa)
    return RedirectResponse(url=f"/pesos-medidas?empresa={quote(empresa)}", status_code=303)


@router.get("/pesos-medidas/{empresa:path}/pdf")
async def pricing_pdf(request: Request, empresa: str):
    redirect = require_auth(request)
    if redirect:
        return redirect

    get_pricing_store(request)
    df, columns = get_prepared_data()

    try:
        pdf_bytes = _pricing_generate_pdf(empresa, df, columns)
    except Exception as error:
        return HTMLResponse(f"<p>Erro ao gerar PDF: {error}</p>", status_code=500)

    filename = f"diagnostico_oppi_{_pricing_pdf_safe_filename(empresa)}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
