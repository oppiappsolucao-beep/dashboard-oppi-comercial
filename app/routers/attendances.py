"""Router UI — Atendimentos (inbox WhatsApp)."""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import queue
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from app.dependencies import require_auth
from app.services import attendances as attendances_service
from app.services import attendances_storage as store
from app.services.legacy_core import normalize_text
from app.services.storage_paths import get_storage_dir
from app.templating import render

router = APIRouter(tags=["attendances"])

_MEDIA_MAX_BYTES = 15 * 1024 * 1024


def _username(request: Request) -> str:
    return normalize_text(request.session.get("username", "")) or "Atendente"


def _filters(request: Request, form: dict | None = None) -> tuple[str, str, str]:
    data = form or {}
    search = normalize_text(data.get("search") or request.query_params.get("search", ""))
    status = normalize_text(data.get("status") or request.query_params.get("status", "todos")) or "todos"
    selected = normalize_text(
        data.get("conversation_id") or request.query_params.get("c", "")
    )
    return search, status, selected


def _media_dir() -> Path:
    path = get_storage_dir() / "attendance_media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _guess_media_type(mime: str, filename: str) -> str:
    mime = (mime or "").lower()
    name = (filename or "").lower()
    if mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    if mime.startswith("audio/") or name.endswith((".ogg", ".mp3", ".wav", ".m4a", ".opus")):
        return "audio"
    if mime.startswith("video/") or name.endswith((".mp4", ".webm", ".mov")):
        return "video"
    return "document"


@router.get("/atendimentos", response_class=HTMLResponse)
def attendances_page(request: Request):
    require_auth(request)
    search, status, selected = _filters(request)
    ctx = attendances_service.page_context(search=search, status=status, selected_id=selected)
    return render(request, "attendances/index.html", ctx)


@router.post("/atendimentos/filtros", response_class=HTMLResponse)
async def attendances_filters(request: Request):
    require_auth(request)
    form = dict(await request.form())
    search, status, selected = _filters(request, form)
    ctx = attendances_service.page_context(search=search, status=status, selected_id=selected)
    return render(request, "partials/attendances_list.html", ctx)


@router.get("/atendimentos/conversa/{conversation_id}", response_class=HTMLResponse)
def attendances_conversation(request: Request, conversation_id: str):
    require_auth(request)
    search, status, _ = _filters(request)
    ctx = attendances_service.page_context(
        search=search, status=status, selected_id=conversation_id
    )
    if not ctx.get("selected"):
        return HTMLResponse("<div class='att-empty'>Conversa não encontrada.</div>", status_code=404)
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/enviar", response_class=HTMLResponse)
async def attendances_send(request: Request, conversation_id: str, text: str = Form("")):
    require_auth(request)
    _, error = attendances_service.send_text_message(
        conversation_id,
        text,
        sender="agent",
        assignee=_username(request),
    )
    search, status, _ = _filters(request)
    ctx = attendances_service.page_context(
        search=search, status=status, selected_id=conversation_id, error=error
    )
    return render(request, "partials/attendances_send_response.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/midia", response_class=HTMLResponse)
async def attendances_send_media(
    request: Request,
    conversation_id: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    require_auth(request)
    raw = await file.read()
    search, status, _ = _filters(request)
    if not raw:
        ctx = attendances_service.page_context(
            search=search, status=status, selected_id=conversation_id, error="Arquivo vazio."
        )
        return render(request, "partials/attendances_send_response.html", ctx)
    if len(raw) > _MEDIA_MAX_BYTES:
        ctx = attendances_service.page_context(
            search=search,
            status=status,
            selected_id=conversation_id,
            error="Arquivo maior que 15 MB.",
        )
        return render(request, "partials/attendances_send_response.html", ctx)

    filename = normalize_text(file.filename) or "arquivo"
    safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
    dest = _media_dir() / safe_name
    dest.write_bytes(raw)

    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    media_type = _guess_media_type(mime, filename)
    # Evolution costuma aceitar base64 puro (sem data URI)
    media_payload = base64.b64encode(raw).decode("ascii")
    local_url = f"/atendimentos/media/{safe_name}"

    _, error = attendances_service.send_media_message(
        conversation_id,
        media_url=media_payload,
        media_type=media_type,
        caption=caption,
        filename=filename,
        mimetype=mime,
        sender="agent",
        store_media_url=local_url,
    )

    ctx = attendances_service.page_context(
        search=search, status=status, selected_id=conversation_id, error=error
    )
    return render(request, "partials/attendances_send_response.html", ctx)


@router.get("/atendimentos/media/{filename}")
def attendances_media_file(request: Request, filename: str):
    require_auth(request)
    safe = Path(filename).name
    path = _media_dir() / safe
    if not path.is_file():
        return JSONResponse({"error": "not_found"}, status_code=404)
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=mime, filename=safe)


@router.post("/atendimentos/conversa/{conversation_id}/assumir", response_class=HTMLResponse)
def attendances_assume(request: Request, conversation_id: str):
    require_auth(request)
    attendances_service.assume_conversation(conversation_id, _username(request))
    search, status, _ = _filters(request)
    ctx = attendances_service.page_context(
        search=search, status=status, selected_id=conversation_id, flash="Atendimento assumido. IA pausada."
    )
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/devolver-ia", response_class=HTMLResponse)
def attendances_return_ai(request: Request, conversation_id: str):
    require_auth(request)
    attendances_service.return_to_ai(conversation_id)
    search, status, _ = _filters(request)
    ctx = attendances_service.page_context(
        search=search, status=status, selected_id=conversation_id, flash="Conversação devolvida à IA."
    )
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/finalizar", response_class=HTMLResponse)
def attendances_finalize(request: Request, conversation_id: str):
    require_auth(request)
    attendances_service.finalize_conversation(conversation_id)
    search, status, _ = _filters(request)
    ctx = attendances_service.page_context(
        search=search, status=status, selected_id=conversation_id, flash="Atendimento finalizado."
    )
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/notas", response_class=HTMLResponse)
async def attendances_notes(request: Request, conversation_id: str):
    require_auth(request)
    form = dict(await request.form())
    notes = normalize_text(form.get("notes", ""))
    tags_raw = normalize_text(form.get("tags", ""))
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    attendances_service.update_notes_tags(conversation_id, notes=notes, tags=tags)
    search, status, _ = _filters(request)
    ctx = attendances_service.page_context(
        search=search, status=status, selected_id=conversation_id, flash="Observações salvas."
    )
    return render(request, "partials/attendances_crm_panel.html", ctx)


@router.get("/atendimentos/unread")
def attendances_unread(request: Request):
    require_auth(request)
    return JSONResponse({"unread": store.count_unread()})


@router.get("/atendimentos/sync")
def attendances_sync(request: Request, conversation_id: str = ""):
    """Poll leve baseado no SQLite — mensagens novas aparecem sem F5."""
    require_auth(request)
    try:
        return JSONResponse(store.get_sync_snapshot(conversation_id))
    except Exception:
        return JSONResponse(
            {
                "unread": 0,
                "inbox_token": "",
                "conversation_id": conversation_id or None,
                "conversation_token": None,
            },
            status_code=200,
        )


@router.get("/atendimentos/diagnostico-evolution")
def attendances_evolution_diag(request: Request, conversation_id: str = ""):
    """Diagnóstico rápido da integração Evolution (sem expor a API key)."""
    require_auth(request)
    from app.config import settings
    from app.services import evolution_client

    key = settings.evolution_api_key or ""
    masked = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("*" * len(key))
    state = ""
    state_error = ""
    try:
        state = evolution_client.get_connection_state()
    except Exception as error:
        state_error = str(error)

    conversation = store.get_conversation(conversation_id) if conversation_id else None
    return JSONResponse(
        {
            "configured": settings.evolution_configured,
            "api_url": settings.evolution_api_url,
            "instance": settings.evolution_instance,
            "api_key_masked": masked,
            "connection_state": state or None,
            "connection_error": state_error or None,
            "conversation": {
                "id": (conversation or {}).get("id"),
                "phone_e164": (conversation or {}).get("phone_e164"),
                "remote_jid": (conversation or {}).get("remote_jid"),
                "contact_name": (conversation or {}).get("contact_name"),
            }
            if conversation
            else None,
            "hint": (
                "Peça ao cliente para enviar uma mensagem nova depois do deploy; "
                "isso grava o remote_jid correto. Depois responda de novo."
            ),
        }
    )


@router.get("/atendimentos/stream")
async def attendances_stream(request: Request):
    require_auth(request)
    q = store.subscribe_events()

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'unread': store.count_unread()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(q.get, True, 15.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'ping', 'unread': store.count_unread()})}\n\n"
        finally:
            store.unsubscribe_events(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
