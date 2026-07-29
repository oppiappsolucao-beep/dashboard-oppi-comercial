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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from app.dependencies import get_session_user, require_auth
from app.services import attendances as attendances_service
from app.services import attendances_storage as store
from app.services.legacy_core import normalize_text
from app.services.storage_paths import get_storage_dir
from app.templating import render

router = APIRouter(tags=["attendances"])

_MEDIA_MAX_BYTES = 15 * 1024 * 1024


def _username(request: Request) -> str:
    return normalize_text(request.session.get("username", "")) or "Atendente"


def _seller_label(request: Request) -> str:
    """Nome comercial do responsável (preferência: nome do usuário, senão login)."""
    user = get_session_user(request)
    if user:
        name = normalize_text(user.get("name") or "")
        if name:
            return name
        username = normalize_text(user.get("username") or "")
        if username:
            return username
    return _username(request)


def _filters(request: Request, form: dict | None = None) -> tuple[str, str, str, str, str]:
    data = form or {}
    search = normalize_text(data.get("search") or request.query_params.get("search", ""))
    # Padrão: só abertos (finalizados saem da fila)
    status = normalize_text(data.get("status") or request.query_params.get("status", "abertos")) or "abertos"
    if status.lower() in ("todos", "all"):
        status = "abertos"
    selected = normalize_text(
        data.get("conversation_id") or request.query_params.get("c", "")
    )
    sector = normalize_text(data.get("sector") or request.query_params.get("sector", ""))
    line = normalize_text(
        data.get("line")
        or data.get("wa")
        or request.query_params.get("line", "")
        or request.query_params.get("wa", "")
    )
    return search, status, selected, sector, line


def _page_ctx(
    request: Request,
    *,
    form: dict | None = None,
    selected_id: str | None = None,
    flash: str = "",
    error: str = "",
    light: bool = False,
    soft: bool = False,
) -> dict:
    search, status, selected, sector, line = _filters(request, form)
    return attendances_service.page_context(
        search=search,
        status=status,
        sector_filter=sector,
        line_filter=line,
        selected_id=selected if selected_id is None else selected_id,
        session_user=get_session_user(request),
        flash=flash,
        error=error,
        light=light,
        soft=soft,
        request=request,
    )


def _media_dir() -> Path:
    path = get_storage_dir() / "attendance_media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _media_mime_for_filename(filename: str, fallback: str = "") -> str:
    """MIME confiável para <audio>/<img> — evita application/octet-stream (player fica 0:00)."""
    name = (filename or "").lower()
    explicit = {
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".mpeg": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".aac": "audio/aac",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
    }
    for ext, mime in explicit.items():
        if name.endswith(ext):
            # .mp4/.webm podem ser vídeo; se o fallback já disser video/, respeita
            if fallback.startswith("video/") and ext in (".mp4", ".webm"):
                return fallback
            return mime
    guessed = mimetypes.guess_type(filename or "")[0] or ""
    if guessed and guessed != "application/octet-stream":
        return guessed
    return fallback or "application/octet-stream"


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


@router.post("/atendimentos/novo", response_class=HTMLResponse)
async def attendances_start_call(
    request: Request,
    phone: str = Form(""),
    contact_name: str = Form(""),
    first_message: str = Form(""),
    line: str = Form(""),
):
    require_auth(request)
    conversation, error = attendances_service.start_whatsapp_call(
        phone=phone,
        contact_name=contact_name,
        first_message=first_message,
        assignee=_seller_label(request),
        evolution_instance=line,
    )
    selected_id = (conversation or {}).get("id") or ""
    flash = ""
    if conversation and not error:
        flash = "Chamado aberto. Se for número novo, o lead já foi cadastrado."
    ctx = _page_ctx(
        request,
        selected_id=selected_id or None,
        flash=flash,
        error=error,
    )
    # Atualiza a página completa para listar a nova conversa e abrir o chat
    return render(request, "attendances/index.html", ctx)


@router.get("/atendimentos", response_class=HTMLResponse)
def attendances_page(request: Request):
    require_auth(request)
    flash = ""
    error = ""
    light = False
    if request.query_params.get("deleted") == "1":
        flash = "Conversa excluída do atendimento. O cadastro no CRM foi mantido."
        light = True  # sem sync Evolution — evita demora e ressurreição imediata
    err = normalize_text(request.query_params.get("error") or "")
    if err == "sem_permissao":
        error = "Apenas o administrador pode excluir conversas."
    elif err == "nao_encontrada":
        error = "Conversa não encontrada ou já excluída."
    return render(
        request,
        "attendances/index.html",
        _page_ctx(
            request,
            selected_id="" if light else None,
            flash=flash,
            error=error,
            light=light,
        ),
    )


@router.post("/atendimentos/filtros", response_class=HTMLResponse)
async def attendances_filters(request: Request):
    require_auth(request)
    form = dict(await request.form())
    return render(
        request,
        "partials/attendances_list.html",
        _page_ctx(request, form=form, light=True),
    )


@router.get("/atendimentos/conversa/{conversation_id}", response_class=HTMLResponse)
def attendances_conversation(request: Request, conversation_id: str, soft: int = 0):
    require_auth(request)
    is_soft = bool(soft)
    # Soft = refresh do poll: não dispara Evolution de novo (quebrava o worker único).
    if not is_soft:
        try:
            attendances_service.schedule_sync_messages_from_evolution(
                conversation_id, limit=40, force=False
            )
        except Exception:
            pass
    ctx = _page_ctx(
        request,
        selected_id=conversation_id,
        light=True,
        soft=is_soft,
    )
    # Sempre 200: HTMX ignora 404 e o clique parece “não abrir”
    if not ctx.get("selected"):
        return render(request, "partials/attendances_thread.html", ctx)
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/enviar", response_class=HTMLResponse)
async def attendances_send(request: Request, conversation_id: str, text: str = Form("")):
    require_auth(request)
    message, notice = attendances_service.send_text_message(
        conversation_id,
        text,
        sender="agent",
        assignee=_username(request),
    )
    error = ""
    flash = ""
    if not message and notice:
        error = notice
    elif notice:
        flash = notice
    ctx = _page_ctx(request, selected_id=conversation_id, error=error, flash=flash)
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
    if not raw:
        ctx = _page_ctx(request, selected_id=conversation_id, error="Arquivo vazio.")
        return render(request, "partials/attendances_send_response.html", ctx)
    if len(raw) > _MEDIA_MAX_BYTES:
        ctx = _page_ctx(request, selected_id=conversation_id, error="Arquivo maior que 15 MB.")
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

    ctx = _page_ctx(request, selected_id=conversation_id, error=error)
    return render(request, "partials/attendances_send_response.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/voz", response_class=HTMLResponse)
async def attendances_send_voice(
    request: Request,
    conversation_id: str,
    file: UploadFile = File(...),
):
    require_auth(request)
    raw = await file.read()
    if not raw:
        ctx = _page_ctx(request, selected_id=conversation_id, error="Áudio vazio.")
        return render(request, "partials/attendances_send_response.html", ctx)
    if len(raw) > _MEDIA_MAX_BYTES:
        ctx = _page_ctx(request, selected_id=conversation_id, error="Áudio maior que 15 MB.")
        return render(request, "partials/attendances_send_response.html", ctx)

    filename = normalize_text(file.filename) or "audio.ogg"
    safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
    dest = _media_dir() / safe_name
    dest.write_bytes(raw)

    mime = file.content_type or mimetypes.guess_type(filename)[0] or "audio/ogg"
    if not mime.startswith("audio/"):
        mime = _media_mime_for_filename(filename, "audio/ogg")
    else:
        mime = mime.split(";")[0].strip() or "audio/ogg"
    media_payload = base64.b64encode(raw).decode("ascii")
    local_url = f"/atendimentos/media/{safe_name}"

    _, error = attendances_service.send_voice_message(
        conversation_id,
        audio_base64=media_payload,
        mimetype=mime,
        filename=filename,
        sender="agent",
        store_media_url=local_url,
    )

    ctx = _page_ctx(request, selected_id=conversation_id, error=error)
    return render(request, "partials/attendances_send_response.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/atalho", response_class=HTMLResponse)
async def attendances_send_quick_reply(
    request: Request,
    conversation_id: str,
    shortcut: str = Form(""),
):
    require_auth(request)
    message, notice = attendances_service.send_quick_reply(
        conversation_id,
        shortcut,
        sender="agent",
        assignee=_username(request),
    )
    # Sucesso: sem banner no topo (aviso técnico PENDING só atrapalha no atalho)
    error = notice if (not message and notice) else ""
    ctx = _page_ctx(request, selected_id=conversation_id, error=error)
    return render(request, "partials/attendances_send_response.html", ctx)


@router.get("/atendimentos/media/{filename}")
def attendances_media_file(request: Request, filename: str):
    require_auth(request)
    safe = Path(filename).name
    path = _media_dir() / safe
    if not path.is_file():
        return JSONResponse({"error": "not_found"}, status_code=404)
    mime = _media_mime_for_filename(safe)
    # inline: <audio src> quebra com Content-Disposition: attachment (player fica 0:00)
    return FileResponse(
        path,
        media_type=mime,
        filename=safe,
        content_disposition_type="inline",
    )


@router.post("/atendimentos/conversa/{conversation_id}/assumir", response_class=HTMLResponse)
async def attendances_assume(request: Request, conversation_id: str):
    require_auth(request)
    form = await request.form()
    assignee = normalize_text(form.get("assignee")) or _username(request)
    attendances_service.assume_conversation(
        conversation_id,
        assignee,
        sector_id=form.get("sector_id"),
    )
    ctx = _page_ctx(request, selected_id=conversation_id, flash="Atendimento assumido. IA pausada.")
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/direcionar", response_class=HTMLResponse)
async def attendances_assign(request: Request, conversation_id: str):
    require_auth(request)
    form = await request.form()
    action = normalize_text(form.get("action")) or "direcionar"
    assignee = normalize_text(form.get("assignee"))
    if action == "assumir" and not assignee:
        assignee = _username(request)
    if not assignee and action != "assumir":
        ctx = _page_ctx(
            request,
            selected_id=conversation_id,
            error="Selecione o responsável para direcionar.",
        )
        return render(request, "partials/attendances_thread.html", ctx)

    attendances_service.assign_conversation(
        conversation_id,
        assignee=assignee or _username(request),
        sector_id=form.get("sector_id"),
        pause_ai=True,
        set_in_progress=True,
    )
    flash = "Atendimento assumido. IA pausada." if action == "assumir" else "Atendimento direcionado."
    ctx = _page_ctx(request, selected_id=conversation_id, flash=flash)
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/devolver-ia", response_class=HTMLResponse)
def attendances_return_ai(request: Request, conversation_id: str):
    require_auth(request)
    attendances_service.return_to_ai(conversation_id)
    ctx = _page_ctx(request, selected_id=conversation_id, flash="Conversação devolvida à IA.")
    return render(request, "partials/attendances_thread.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/atualizar-lid", response_class=HTMLResponse)
def attendances_refresh_lid(request: Request, conversation_id: str):
    """Busca @lid na Evolution e grava na conversa (necessário para sair de PENDING)."""
    require_auth(request)
    from app.services import evolution_client

    conversation = store.get_conversation(conversation_id)
    if not conversation:
        ctx = _page_ctx(request, selected_id=conversation_id, error="Conversa não encontrada.")
        return render(request, "partials/attendances_send_response.html", ctx)

    lid = evolution_client.discover_lid_for_phone(
        conversation.get("phone_e164") or "",
        conversation.get("remote_jid") or "",
    )
    if lid and "@lid" in lid.lower():
        store.update_conversation(conversation_id, remote_jid=lid)
        # força sync de mensagens para casar histórico
        try:
            attendances_service.schedule_sync_messages_from_evolution(
                conversation_id, limit=40, force=True
            )
        except Exception:
            pass
        ctx = _page_ctx(
            request,
            selected_id=conversation_id,
            flash=f"@lid atualizado: {lid}. Pode enviar de novo.",
        )
    else:
        ctx = _page_ctx(
            request,
            selected_id=conversation_id,
            error=(
                "Não achei @lid na Evolution para este número. "
                "Peça uma mensagem nova do cliente no WhatsApp e tente de novo."
            ),
        )
    return render(request, "partials/attendances_send_response.html", ctx)


@router.post("/atendimentos/conversa/{conversation_id}/finalizar", response_class=HTMLResponse)
def attendances_finalize(request: Request, conversation_id: str):
    require_auth(request)
    attendances_service.finalize_conversation(conversation_id)
    ctx = _page_ctx(request, selected_id=conversation_id, flash="Atendimento finalizado.")
    return render(request, "partials/attendances_thread.html", ctx)


@router.get("/atendimentos/conversa/{conversation_id}/excluir")
def attendances_delete_get(request: Request, conversation_id: str):
    """GET acidental (refresh/proxy) — nunca processa exclusão; volta à inbox."""
    auth = require_auth(request)
    if isinstance(auth, RedirectResponse):
        return auth
    line = normalize_text(request.query_params.get("line", ""))
    from urllib.parse import urlencode

    qs = urlencode({"line": line} if line else {})
    return RedirectResponse(url=f"/atendimentos?{qs}" if qs else "/atendimentos", status_code=303)


@router.post("/atendimentos/conversa/{conversation_id}/excluir", response_class=HTMLResponse)
def attendances_delete(
    request: Request,
    conversation_id: str,
    line: str = Form(""),
):
    """Responde 303 na hora; exclusão roda em thread (não derruba o worker)."""
    import logging
    import threading

    log = logging.getLogger(__name__)
    from urllib.parse import urlencode

    try:
        auth = require_auth(request)
        if isinstance(auth, RedirectResponse):
            return auth
    except Exception:
        return RedirectResponse(url="/login", status_code=303)

    line = normalize_text(line or request.query_params.get("line", ""))
    qs_ok = urlencode({"line": line, "deleted": "1"} if line else {"deleted": "1"})
    redirect_ok = RedirectResponse(url=f"/atendimentos?{qs_ok}", status_code=303)

    try:
        session_user = get_session_user(request)
        if not attendances_service.can_delete_attendance_conversation(
            session_user, request=request
        ):
            qs = urlencode({"line": line, "error": "sem_permissao"} if line else {"error": "sem_permissao"})
            return RedirectResponse(url=f"/atendimentos?{qs}", status_code=303)
    except Exception:
        log.exception("can_delete falhou; segue tentativa de exclusão se sessão admin")

    cid = normalize_text(conversation_id)

    def _run_delete() -> None:
        try:
            attendances_service.delete_conversation(cid)
        except Exception:
            log.exception("delete_conversation em background falhou (%s)", cid)

    try:
        threading.Thread(
            target=_run_delete,
            daemon=True,
            name=f"att-del-http-{cid[:10]}",
        ).start()
    except Exception:
        # Último recurso: tenta sincronizado, mas nunca propaga crash
        try:
            attendances_service.delete_conversation(cid)
        except Exception:
            log.exception("delete_conversation sync falhou (%s)", cid)

    return redirect_ok


@router.post("/atendimentos/conversa/{conversation_id}/notas", response_class=HTMLResponse)
async def attendances_notes(request: Request, conversation_id: str):
    require_auth(request)
    form = await request.form()
    notes = normalize_text(form.get("notes", ""))
    raw_tags = form.getlist("tags") if hasattr(form, "getlist") else []
    if not raw_tags:
        tags_raw = normalize_text(form.get("tags", ""))
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        tags = [normalize_text(t) for t in raw_tags if normalize_text(t)]
    attendances_service.update_notes_tags(conversation_id, notes=notes, tags=tags)
    ctx = _page_ctx(request, selected_id=conversation_id, flash="Observações salvas.")
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
        # NÃO chama Evolution aqui: o poll a cada 4s travava o worker e o webhook parava.
        if conversation_id:
            try:
                attendances_service.schedule_sync_messages_from_evolution(
                    conversation_id, limit=20, force=False
                )
            except Exception:
                pass
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


@router.post("/atendimentos/conversa/{conversation_id}/teste-envio")
def attendances_test_send(request: Request, conversation_id: str):
    """Envia um ping real via Evolution e devolve a resposta (JSON ou HTML no HTMX)."""
    require_auth(request)
    from app.services import evolution_client
    from app.services.evolution_client import EvolutionClientError

    conversation = store.get_conversation(conversation_id)
    if not conversation:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<pre class="att-evo-result">Erro: conversa não encontrada.</pre>',
                status_code=404,
            )
        return JSONResponse({"ok": False, "error": "conversa_nao_encontrada"}, status_code=404)

    body = "teste envio CRM Oppi"
    try:
        data = evolution_client.send_text(
            conversation.get("phone_e164") or "",
            body,
            jid=conversation.get("remote_jid") or "",
        )
        msg_id = evolution_client.extract_message_id(data)
        status = evolution_client.extract_message_status(data) or data.get("_oppi_send_status") or "UNKNOWN"
        pending = bool(data.get("_oppi_delivery_pending")) or evolution_client.is_delivery_pending(data)
        used = data.get("_oppi_send_number") or conversation.get("remote_jid") or conversation.get("phone_e164")
        # grava no inbox também, para conferência
        store.add_message(
            conversation_id,
            direction="out",
            body=body,
            msg_type="text",
            evolution_id=msg_id,
            sender="agent",
        )
        payload = {
            "ok": True,
            "pending": pending,
            "status": status or None,
            "message_id": msg_id,
            "phone": conversation.get("phone_e164"),
            "remote_jid": conversation.get("remote_jid"),
            "used_number": used,
            "targets_hint": evolution_client.enrich_targets_from_chats(
                conversation.get("phone_e164") or "",
                conversation.get("remote_jid") or "",
            )[:8],
            "evolution_response": data,
            "check": (
                "Se status=PENDING e a msg não chega no WhatsApp, o problema é "
                "a Evolution/Baileys (não o CRM). Peça uma msg nova do cliente "
                "para gravar @lid, ou atualize Baileys no container Evolution "
                "(baileys@7.0.0-rc13). Confira no Manager Chat o mesmo sintoma."
            ),
        }
        if request.headers.get("HX-Request"):
            flag = "⚠ PENDING — pode não ter chegado" if pending else "✓ Aceito pela Evolution"
            pretty = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            return HTMLResponse(
                '<div id="att-evo-test-result" class="att-evo-result-wrap">'
                f'<pre class="att-evo-result">{flag}\n'
                f"status={status} · destino={used}\n\n{pretty}</pre></div>"
            )
        return JSONResponse(payload)
    except EvolutionClientError as error:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<div id="att-evo-test-result" class="att-evo-result-wrap">'
                f'<pre class="att-evo-result err">Erro no envio:\n{error}</pre></div>',
                status_code=400,
            )
        return JSONResponse(
            {
                "ok": False,
                "error": str(error),
                "phone": conversation.get("phone_e164"),
                "remote_jid": conversation.get("remote_jid"),
            },
            status_code=400,
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
    resolved = ""
    names: list[str] = []
    try:
        names = evolution_client.fetch_instance_names()
        resolved = evolution_client.resolved_instance_name()
        state = evolution_client.get_connection_state()
    except Exception as error:
        state_error = str(error)

    conversation = store.get_conversation(conversation_id) if conversation_id else None
    discovered_lid = ""
    owner_phone = ""
    try:
        owner_phone = evolution_client.get_instance_owner_phone()
    except Exception:
        owner_phone = ""
    if conversation:
        try:
            discovered_lid = evolution_client.discover_lid_for_phone(
                conversation.get("phone_e164") or "",
                conversation.get("remote_jid") or "",
            )
        except Exception:
            discovered_lid = ""
    self_chat = False
    if conversation:
        try:
            self_chat = evolution_client.is_self_chat(
                conversation.get("phone_e164") or "",
                conversation.get("remote_jid") or "",
            )
        except Exception:
            self_chat = False
    return JSONResponse(
        {
            "configured": settings.evolution_configured,
            "api_url": settings.evolution_api_url,
            "instance_configured": settings.evolution_instance,
            "instance_resolved": resolved or None,
            "instance_owner_phone": owner_phone or None,
            "instances_available": names,
            "api_key_masked": masked,
            "connection_state": state or None,
            "connection_error": state_error or None,
            "conversation": {
                "id": (conversation or {}).get("id"),
                "phone_e164": (conversation or {}).get("phone_e164"),
                "remote_jid": (conversation or {}).get("remote_jid"),
                "contact_name": (conversation or {}).get("contact_name"),
                "discovered_lid": discovered_lid or None,
                "is_self_chat": self_chat,
                "has_lid": bool(
                    discovered_lid
                    or (
                        conversation
                        and "@lid" in normalize_text((conversation or {}).get("remote_jid") or "").lower()
                    )
                ),
            }
            if conversation
            else None,
            "hint": (
                "Se is_self_chat=true, troque o teste para OUTRO celular. "
                "Se PENDING em qualquer destino, atualize Baileys no Evolution "
                "(baileys@7.0.0-rc13) e reconecte o QR da oppi-comercial."
            ),
            "evolution_fix": (
                "No Easypanel → serviço Evolution → Command/Entrypoint:\n"
                'sh -c "npm install baileys@7.0.0-rc13 && npm run db:deploy && '
                'npm run db:generate && npm run start:prod"\n'
                "Depois Delete/Reconnect QR da instância oppi-comercial. "
                "Enquanto o Baileys estiver antigo, NENHUM sistema (CRM ou Manager) entrega 1:1."
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
