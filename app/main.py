from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import auth, activities, contracts, funnel, goals_reports, leads, overview, pricing, proposals, registration, settings
from app.templating import render

app = FastAPI(title="Dashboard Oppi Comercial")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "defina-app-password-no-easypanel",
    session_cookie="oppi_session",
    max_age=60 * 60 * 24 * 7,
)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth.router)
app.include_router(overview.router)
app.include_router(funnel.router)
app.include_router(activities.router)
app.include_router(proposals.router)
app.include_router(goals_reports.router)
app.include_router(leads.router)
app.include_router(registration.router)
app.include_router(contracts.router)
app.include_router(pricing.router)
app.include_router(settings.router)

# Leads e Empresas (registro explícito para garantir rota no deploy)
from app.routers.leads import leads_filters, leads_page, leads_refresh  # noqa: E402

app.add_api_route("/leads-e-empresas", leads_page, methods=["GET"], tags=["leads"])
app.add_api_route("/leads-e-empresas/filtros", leads_filters, methods=["POST"], tags=["leads"])
app.add_api_route("/leads-e-empresas/atualizar", leads_refresh, methods=["POST"], tags=["leads"])

from app.routers.activities import activities_filters, activities_page, activities_refresh  # noqa: E402

app.add_api_route("/atividades", activities_page, methods=["GET"], tags=["activities"])
app.add_api_route("/atividades/filtros", activities_filters, methods=["POST"], tags=["activities"])
app.add_api_route("/atividades/atualizar", activities_refresh, methods=["POST"], tags=["activities"])

from app.routers.proposals import proposals_chat, proposals_chat_reset, proposals_filters, proposals_page, proposals_refresh  # noqa: E402

app.add_api_route("/propostas", proposals_page, methods=["GET"], tags=["proposals"])
app.add_api_route("/propostas/filtros", proposals_filters, methods=["POST"], tags=["proposals"])
app.add_api_route("/propostas/chat", proposals_chat, methods=["POST"], tags=["proposals"])
app.add_api_route("/propostas/atualizar", proposals_refresh, methods=["POST"], tags=["proposals"])
app.add_api_route("/propostas/chat/reset", proposals_chat_reset, methods=["POST"], tags=["proposals"])

from app.routers.goals_reports import goals_filters, goals_page, goals_refresh  # noqa: E402

app.add_api_route("/metas-e-relatorios", goals_page, methods=["GET"], tags=["goals"])
app.add_api_route("/metas-e-relatorios/filtros", goals_filters, methods=["POST"], tags=["goals"])
app.add_api_route("/metas-e-relatorios/atualizar", goals_refresh, methods=["POST"], tags=["goals"])

from app.routers.settings import settings_filters, settings_page, settings_permissions_toggle, settings_refresh  # noqa: E402

app.add_api_route("/configuracoes", settings_page, methods=["GET"], tags=["settings"])
app.add_api_route("/configuracoes/filtros", settings_filters, methods=["POST"], tags=["settings"])
app.add_api_route("/configuracoes/permissoes", settings_permissions_toggle, methods=["POST"], tags=["settings"])
app.add_api_route("/configuracoes/atualizar", settings_refresh, methods=["POST"], tags=["settings"])


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.get("/")
async def root():
    return RedirectResponse(url="/visao-geral", status_code=303)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 503:
        return render(
            request,
            "error.html",
            {"message": exc.detail},
            status_code=503,
        )
    raise exc
