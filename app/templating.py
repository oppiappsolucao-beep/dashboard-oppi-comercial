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


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context or {},
        **kwargs,
    )
