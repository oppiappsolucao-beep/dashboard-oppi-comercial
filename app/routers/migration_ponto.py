"""Migração Oppi Ponto → CRM via interface web (admin)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import is_admin, require_auth
from app.services.storage_paths import get_storage_dir
from app.templating import render

router = APIRouter(prefix="/migracao-ponto", tags=["migracao"])

DEFAULT_JSON_NAME = "oppi-ponto-crm-migration-2026-07-28.json"


def _json_candidates() -> list[Path]:
    storage = get_storage_dir()
    names = [
        DEFAULT_JSON_NAME,
        "oppi-ponto-crm-migration.json",
    ]
    found: list[Path] = []
    for name in names:
        path = storage / name
        if path.exists():
            found.append(path)
    # qualquer export recente na pasta storage
    if storage.exists():
        for path in sorted(storage.glob("oppi-ponto-crm-migration*.json"), reverse=True):
            if path not in found:
                found.append(path)
    return found


def _load_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("", response_class=HTMLResponse)
def migration_page(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        return RedirectResponse("/visao-geral", status_code=303)

    files = _json_candidates()
    return render(
        request,
        "migration_ponto.html",
        {
            "active_page": "settings",
            "files": [{"name": p.name, "path": str(p), "size": p.stat().st_size} for p in files],
            "default_file": files[0].name if files else DEFAULT_JSON_NAME,
            "result": None,
            "error": None,
        },
    )


@router.post("/executar", response_class=HTMLResponse)
def migration_run(
    request: Request,
    filename: str = Form(...),
    apply: str = Form("0"),
):
    redirect = require_auth(request)
    if redirect:
        return redirect
    if not is_admin(request):
        return RedirectResponse("/visao-geral", status_code=303)

    files = _json_candidates()
    storage = get_storage_dir()
    target = storage / Path(filename).name
    error = None
    result = None

    try:
        if not target.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {target.name}")
        payload = _load_payload(target)
        from app.services.ponto_migration import migrate_companies

        result = migrate_companies(payload, apply=(str(apply) == "1"), local_only=True)
    except Exception as exc:
        message = str(exc)
        if "429" in message or "Quota exceeded" in message:
            error = (
                "A cota da Google Sheets estourou (muitas leituras por minuto). "
                "Aguarde 2 minutos e clique em Aplicar de novo — a migração agora grava local "
                "e não depende da planilha na hora."
            )
        else:
            error = message

    return render(
        request,
        "migration_ponto.html",
        {
            "active_page": "settings",
            "files": [{"name": p.name, "path": str(p), "size": p.stat().st_size} for p in files],
            "default_file": target.name if target.exists() else (files[0].name if files else DEFAULT_JSON_NAME),
            "result": result,
            "error": error,
        },
    )
