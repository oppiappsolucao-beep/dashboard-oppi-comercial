from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import APP_BUILD, settings
from app.services.legacy_core import get_logo_data_uri

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
templates.env.globals["logo_uri"] = get_logo_data_uri()
templates.env.globals["app_username"] = settings.app_username
templates.env.globals["support_whatsapp_url"] = settings.support_whatsapp_url
templates.env.globals["support_whatsapp_label"] = settings.support_whatsapp_label
templates.env.globals["static_version"] = APP_BUILD

PAGE_BACK_FALLBACKS = {
    "overview": "/visao-geral",
    "funnel": "/funil-de-vendas",
    "leads": "/leads-e-empresas",
    "activities": "/atividades",
    "attendances": "/atendimentos",
    "proposals": "/propostas",
    "goals": "/metas-e-relatorios",
    "registration_new": "/leads-e-empresas",
    "contracts": "/leads-e-empresas",
    "settings": "/visao-geral",
}


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
):
    ctx = dict(context or {})
    if "back_fallback" not in ctx:
        active_page = ctx.get("active_page")
        if active_page:
            ctx["back_fallback"] = PAGE_BACK_FALLBACKS.get(active_page, "/visao-geral")
    if "display_username" not in ctx or "display_role" not in ctx:
        try:
            from app.dependencies import get_session_user

            user = get_session_user(request)
        except Exception:
            user = None
        if user:
            ctx.setdefault(
                "display_username",
                user.get("name") or user.get("username") or settings.app_username,
            )
            ctx.setdefault("display_role", user.get("role") or "")
        else:
            ctx.setdefault("display_username", settings.app_username)
            ctx.setdefault("display_role", "")
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=ctx,
        **kwargs,
    )
