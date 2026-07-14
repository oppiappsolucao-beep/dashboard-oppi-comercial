from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import auth, contracts, funnel, overview, pricing, registration
from app.templating import render

app = FastAPI(title="Dashboard Oppi Comercial")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="oppi_session",
    max_age=60 * 60 * 24 * 7,
)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth.router)
app.include_router(overview.router)
app.include_router(funnel.router)
app.include_router(registration.router)
app.include_router(contracts.router)
app.include_router(pricing.router)


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
