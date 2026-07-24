from pathlib import Path
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import APP_BUILD, settings
from app.routers import auth, activities, attendances, contracts, evolution_webhook, funnel, goals_reports, leads, overview, proposals, registration
from app.routers import settings as settings_router
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
app.include_router(attendances.router)
app.include_router(evolution_webhook.router)
app.include_router(proposals.router)
app.include_router(goals_reports.router)
app.include_router(leads.router)
app.include_router(registration.router)
app.include_router(contracts.router)
app.include_router(settings_router.router)

# Atendimentos (registro explícito para garantir rota no deploy)
from app.routers.attendances import (  # noqa: E402
    attendances_evolution_diag,
    attendances_filters,
    attendances_page,
    attendances_test_send,
)

app.add_api_route("/atendimentos", attendances_page, methods=["GET"], tags=["attendances"])
app.add_api_route("/atendimentos/filtros", attendances_filters, methods=["POST"], tags=["attendances"])
app.add_api_route("/atendimentos/diagnostico-evolution", attendances_evolution_diag, methods=["GET"], tags=["attendances"])
app.add_api_route(
    "/atendimentos/conversa/{conversation_id}/teste-envio",
    attendances_test_send,
    methods=["POST"],
    tags=["attendances"],
)

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

from app.routers.settings import (  # noqa: E402
    settings_add_service,
    settings_filters,
    settings_page,
    settings_permissions_toggle,
    settings_refresh,
    settings_remove_service,
)

app.add_api_route("/configuracoes", settings_page, methods=["GET"], tags=["settings"])
app.add_api_route("/configuracoes/filtros", settings_filters, methods=["POST"], tags=["settings"])
app.add_api_route("/configuracoes/permissoes", settings_permissions_toggle, methods=["POST"], tags=["settings"])
app.add_api_route("/configuracoes/atualizar", settings_refresh, methods=["POST"], tags=["settings"])
app.add_api_route("/configuracoes/servicos/adicionar", settings_add_service, methods=["POST"], tags=["settings"])
app.add_api_route("/configuracoes/servicos/remover", settings_remove_service, methods=["POST"], tags=["settings"])


@app.on_event("startup")
async def startup_maintenance() -> None:
    import logging
    import time

    log = logging.getLogger(__name__)

    # SQLite local rápido — não depende da API Google
    try:
        from app.services.crm_local_db import init_crm_local_db
        from app.services.legacy_core import hydrate_sheet_cache_from_disk

        init_crm_local_db()
        hydrate_sheet_cache_from_disk()
    except Exception as error:
        log.error("Startup SQLite/cache local: %s", error)

    def _run_background() -> None:
        # Banco + nichos (pode demorar se o Postgres estiver lento)
        try:
            from database.connection import Base, engine
            from database import models  # noqa: F401
            from app.services.attendance_db_migrate import (
                ensure_attendance_schema_columns,
                migrate_attendance_from_sqlite_if_needed,
            )
            from app.services.niches import ensure_default_niches
            from app.services.sectors import ensure_default_sectors
            from app.services.attendance_tags import ensure_default_attendance_tags
            from app.services.account_users import ensure_default_account_users

            Base.metadata.create_all(bind=engine)
            ensure_attendance_schema_columns()
            ensure_default_niches()
            ensure_default_sectors()
            ensure_default_attendance_tags()
            ensure_default_account_users()
            migrate_info = migrate_attendance_from_sqlite_if_needed()
            log.info("Attendance DB migrate: %s", migrate_info)
            try:
                from app.services.attendances_storage import purge_group_conversations

                purged = purge_group_conversations()
                log.info("Attendance group purge: %s", purged)
                from app.services.attendances_storage import delete_conversations_by_contact_names

                named = delete_conversations_by_contact_names()
                log.info("Attendance named delete: %s", named)
            except Exception as purge_error:
                log.error("Attendance group purge: %s", purge_error)
        except Exception as error:
            log.error("Startup DATABASE_URL / attendance migrate: %s", error)

        # Evita rajada de leituras na Sheets API no boot (erro 429/OOM).
        time.sleep(2)

        # Só hidrata cache local — NÃO chama Google Sheets no boot (429/OOM).
        steps = [
            ("activities", lambda: __import__("app.services.activities_storage", fromlist=["reload_activities_store"]).reload_activities_store(force_refresh=False)),
            ("lead_actions", lambda: __import__("app.services.lead_actions_storage", fromlist=["reload_lead_actions_store"]).reload_lead_actions_store(force_refresh=False)),
            ("crm_tabs", lambda: __import__("app.services.sheet_crm_storage", fromlist=["ensure_crm_storage_tabs"]).ensure_crm_storage_tabs()),
            ("account_users", lambda: __import__("app.services.account_users", fromlist=["load_account_users"]).load_account_users()),
            ("app_settings", lambda: __import__("app.services.app_settings", fromlist=["load_app_settings"]).load_app_settings()),
            ("monthly_goals", lambda: __import__("app.services.monthly_goals", fromlist=["load_monthly_goals"]).load_monthly_goals()),
            (
                "pending_recover",
                lambda: __import__(
                    "app.services.pending_companies",
                    fromlist=["recover_pending_from_sheet_tab"],
                ).recover_pending_from_sheet_tab(),
            ),
            (
                "pending_sync",
                lambda: __import__(
                    "app.services.pending_companies",
                    fromlist=["sync_pending_companies_to_sheet"],
                ).sync_pending_companies_to_sheet(),
            ),
            (
                "proposal_cleanup",
                lambda: __import__(
                    "app.services.proposal_pdf",
                    fromlist=["cleanup_service_account_proposal_files"],
                ).cleanup_service_account_proposal_files(),
            ),
            (
                "fake_company",
                lambda: __import__(
                    "app.services.seed_fake_company",
                    fromlist=["ensure_fake_test_company_on_startup"],
                ).ensure_fake_test_company_on_startup(),
            ),
        ]
        for name, step in steps:
            try:
                step()
            except Exception as error:
                message = str(error)
                if "429" in message or "Quota exceeded" in message:
                    log.warning("Startup background (%s): cota Google Sheets — usando cache local.", name)
                else:
                    log.error("Startup background (%s): %s", name, error)
                time.sleep(1)

    threading.Thread(target=_run_background, daemon=True).start()


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "build": APP_BUILD})


@app.get("/health/pdf-engine")
async def health_pdf_engine():
    from app.services.proposal_pdf import check_pdf_engine_status

    payload = {"status": "ok", **check_pdf_engine_status()}
    if not payload.get("libreoffice_installed"):
        payload["status"] = "missing_libreoffice"
    return JSONResponse(payload)


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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return render(
        request,
        "error.html",
        {"message": f"Erro interno ao carregar a página: {exc}"},
        status_code=500,
    )
