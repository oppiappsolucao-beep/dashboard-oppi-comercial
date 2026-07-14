from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)


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
