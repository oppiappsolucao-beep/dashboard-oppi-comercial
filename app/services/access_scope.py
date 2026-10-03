"""O que cada funcionário pode abrir, conforme os acessos do setor."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

FULL_ACCESS = (
    "gestao",
    "atendimentos",
    "kanban",
    "empresas",
    "novo_cadastro",
    "financeiro",
    "propostas",
    "cadastro",
)

HOME_BY_ACCESS = (
    ("kanban", "/atividades"),
    ("empresas", "/leads-e-empresas"),
    ("novo_cadastro", "/cadastro/novo"),
    ("atendimentos", "/atendimentos"),
    ("gestao", "/gestao"),
    ("financeiro", "/financeiro"),
    ("propostas", "/propostas"),
    ("cadastro", "/cadastros"),
)


def is_employee(request: Request) -> bool:
    return bool(request.session.get("org_person_id"))


def allowed_accesses(request: Request) -> set[str]:
    if not is_employee(request):
        return set(FULL_ACCESS)
    raw = request.session.get("org_accesses") or []
    if isinstance(raw, str):
        raw = [item for item in raw.split(",") if item]
    return {str(item) for item in raw if item}


def nav_permissions(request: Request) -> dict[str, bool]:
    allowed = allowed_accesses(request)
    return {key: key in allowed for key in FULL_ACCESS}


def home_url(request: Request) -> str:
    allowed = allowed_accesses(request)
    for key, url in HOME_BY_ACCESS:
        if key in allowed:
            return url
    return "/sem-acesso"


def _required_access(path: str) -> str | None:
    if path.startswith("/cadastros"):
        return "cadastro"
    if path == "/cadastro/novo" or path.startswith("/cadastro/novo/"):
        return "novo_cadastro"
    rules = (
        ("/gestao", "gestao"),
        ("/visao-geral", "gestao"),
        ("/metas-e-relatorios", "gestao"),
        ("/atendimentos", "atendimentos"),
        ("/atividades", "kanban"),
        ("/propostas", "propostas"),
        ("/financeiro", "financeiro"),
        ("/leads-e-empresas", "empresas"),
        ("/cadastro", "empresas"),
        ("/funil-de-vendas", "empresas"),
    )
    for prefix, key in rules:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None


def enforce_access(request: Request):
    """Devolve redirect quando o funcionário abre uma área fora do setor."""
    if not is_employee(request):
        return None
    path = request.url.path
    if path.startswith("/configuracoes"):
        return RedirectResponse(url=home_url(request), status_code=303)
    needed = _required_access(path)
    if needed and needed not in allowed_accesses(request):
        return RedirectResponse(url=home_url(request), status_code=303)
    return None
