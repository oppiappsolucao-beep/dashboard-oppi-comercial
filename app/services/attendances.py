"""Domínio de Atendimentos (lista, enviar, assumir, finalizar)."""
from __future__ import annotations

import logging
import threading

from app.config import settings
from app.services import attendance_ai, attendance_crm, attendances_storage as store
from app.services import evolution_client
from app.services.evolution_client import EvolutionClientError
from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)

# Setores/perfis operacionais: nunca excluem conversa da inbox
_DELETE_BLOCKED_DEPARTMENTS = frozenset({"atendimento", "cadastro"})


def can_delete_attendance_conversation(
    session_user: dict | None,
    *,
    request=None,
) -> bool:
    """Só Administrador (ou login master APP_USERNAME). Atendimento/Cadastro não podem."""
    try:
        if request is not None:
            uname = normalize_text(getattr(request, "session", {}).get("username") or "")
            if uname and uname.lower() == settings.app_username.lower():
                return True
            role_sess = normalize_text(getattr(request, "session", {}).get("user_role") or "")
            if role_sess == "Administrador":
                dept = normalize_text((session_user or {}).get("department_name") or "").lower()
                if dept not in _DELETE_BLOCKED_DEPARTMENTS:
                    return True
    except Exception:
        pass
    if not session_user:
        return False
    role = normalize_text(session_user.get("role") or "")
    if role != "Administrador":
        return False
    dept = normalize_text(session_user.get("department_name") or "").lower()
    if dept in _DELETE_BLOCKED_DEPARTMENTS:
        return False
    return True


def _resolve_sector_filter(session_user: dict | None, sector_filter: str) -> tuple[int | str | None, dict]:
    from app.services.sectors import attendance_scope_for_user

    scope = attendance_scope_for_user(session_user)
    if scope.get("locked") and scope.get("sector_id"):
        return scope["sector_id"], scope

    raw = normalize_text(sector_filter).lower()
    if raw in ("", "todos", "all"):
        return None, scope
    try:
        return int(sector_filter), scope
    except (TypeError, ValueError):
        return None, scope


def build_whatsapp_line_options(*, refresh_owners: bool = True) -> list[dict]:
    """Linhas WhatsApp configuradas (rótulo = número conectado ou nome da instância)."""
    lines: list[dict] = []
    for name in settings.evolution_instances:
        owner = ""
        try:
            owner = evolution_client.get_instance_owner_phone(
                name, allow_network=refresh_owners
            )
        except Exception:
            owner = ""
        label = owner or name
        if owner and len(owner) >= 12:
            # 5511942157917 → +55 11 94215-7917
            label = f"+{owner[:2]} {owner[2:4]} {owner[4:9]}-{owner[9:]}"
        elif owner and len(owner) >= 10:
            label = owner
        unread = 0
        try:
            unread = store.count_unread(evolution_instance=name)
        except Exception:
            unread = 0
        lines.append({
            "id": name,
            "label": label,
            "phone": owner,
            "unread": unread,
        })
    return lines


def _resolve_line_filter(line_filter: str, lines: list[dict] | None = None) -> str:
    configured = list(settings.evolution_instances or [])
    if not configured:
        return settings.evolution_primary_instance
    wanted = normalize_text(line_filter)
    if wanted:
        for name in configured:
            if normalize_text(name).lower() == wanted.lower():
                return name
        if lines:
            for line in lines:
                if normalize_text(line.get("id")).lower() == wanted.lower():
                    return line["id"]
    if lines:
        return lines[0]["id"]
    return configured[0]


def page_context(
    *,
    search: str = "",
    status: str = "",
    sector_filter: str = "",
    line_filter: str = "",
    selected_id: str = "",
    session_user: dict | None = None,
    flash: str = "",
    error: str = "",
    light: bool = False,
    soft: bool = False,
    request=None,
) -> dict:
    # Manutenção pesada só na carga completa — e em background (não trava troca de aba)
    if not light:
        def _maintenance() -> None:
            try:
                store.purge_group_conversations()
                store.delete_conversations_by_contact_names()
            except Exception:
                logger.exception("Falha ao limpar conversas indesejadas da inbox")
            try:
                schedule_sync_inbox_from_evolution(force=False)
            except Exception:
                logger.exception("Falha ao agendar sync inbox Evolution")

        threading.Thread(target=_maintenance, daemon=True, name="att-page-maint").start()

    effective_sector, scope = _resolve_sector_filter(session_user, sector_filter)
    # Rótulos das linhas: só cache (HTTP Evolution fora do hot path)
    whatsapp_lines = build_whatsapp_line_options(refresh_owners=False)
    if not light:
        def _warm_owners() -> None:
            try:
                build_whatsapp_line_options(refresh_owners=True)
            except Exception:
                logger.exception("Falha ao aquecer rótulos das linhas WhatsApp")

        threading.Thread(target=_warm_owners, daemon=True, name="att-warm-owners").start()
    active_line = _resolve_line_filter(line_filter, whatsapp_lines)
    conversations = store.list_conversations(
        search=search,
        status=status,
        sector_id=effective_sector,
        evolution_instance=active_line,
    )
    selected = None
    messages: list[dict] = []
    crm = attendance_crm.build_crm_panel(None)
    sector_options: list[dict] = []
    responsible_options: list[str] = []
    tag_options: list[str] = []
    quick_replies: list[dict] = []
    try:
        from app.services.sectors import list_sectors, responsible_options_for_sector

        sector_options = list_sectors(active_only=True)
        if scope.get("locked") and scope.get("sector_id"):
            sector_options = [s for s in sector_options if s.get("id") == scope["sector_id"]]
    except Exception:
        sector_options = []

    try:
        from app.services.attendance_tags import list_attendance_tag_options

        tag_options = list_attendance_tag_options()
    except Exception:
        tag_options = []

    try:
        from app.services.attendance_quick_replies import list_quick_reply_options

        quick_replies = list_quick_reply_options()
    except Exception:
        quick_replies = []

    if selected_id:
        selected = store.get_conversation(selected_id)
        if selected:
            from app.services.evolution_client import is_whatsapp_group_jid

            name_key = store._normalize_contact_key(selected.get("contact_name") or "")
            if (
                is_whatsapp_group_jid(selected.get("remote_jid") or "")
                or is_whatsapp_group_jid(selected.get("phone_e164") or "")
                or name_key in store.UNWANTED_INBOX_CONTACT_KEYS
            ):
                store.delete_conversation(selected_id)
                selected = None
                selected_id = ""
            elif (
                scope.get("locked")
                and effective_sector is not None
                and selected.get("sector_id") not in (None, effective_sector)
                and int(selected.get("sector_id") or 0) != int(effective_sector)
            ):
                # Usuário preso a um setor — não abre conversa de outro
                selected = None
                selected_id = ""
            else:
                # Clique explícito: alinha a linha à conversa (não devolve 404 silencioso)
                conv_line = normalize_text(selected.get("evolution_instance") or "")
                if conv_line:
                    active_line = _resolve_line_filter(conv_line, whatsapp_lines)

                store.mark_conversation_read(selected_id)
                selected = store.get_conversation(selected_id)
                messages = store.list_messages(selected_id)
                # CRM / mídia nunca no request — travava o worker e o chat “não abria”
                if not soft:
                    try:
                        schedule_open_enrichment(selected_id)
                    except Exception:
                        logger.exception("Falha ao agendar enriquecimento da conversa")
                try:
                    crm = attendance_crm.build_crm_panel(
                        selected.get("sheet_row") if selected else None,
                        fallback_name=(selected or {}).get("contact_name") or "",
                        fallback_phone=(selected or {}).get("phone_e164") or "",
                    )
                except Exception:
                    crm = attendance_crm.build_crm_panel(None)
                try:
                    from app.services.sectors import responsible_options_for_sector

                    responsible_options = responsible_options_for_sector(
                        selected.get("sector_id") if selected else None
                    )
                except Exception:
                    responsible_options = []

    if not responsible_options:
        try:
            from app.services.sectors import responsible_options_for_sector

            responsible_options = responsible_options_for_sector(None)
        except Exception:
            responsible_options = []

    ui_sector_filter = str(scope["sector_id"]) if scope.get("locked") and scope.get("sector_id") else (
        str(effective_sector) if effective_sector is not None else (normalize_text(sector_filter) or "todos")
    )
    if ui_sector_filter in ("", "None"):
        ui_sector_filter = "todos"

    return {
        "active_page": "attendances",
        "conversations": conversations,
        "selected": selected,
        "messages": messages,
        "crm": crm,
        "search": search,
        "status_filter": status or "abertos",
        "status_options": [
            ("abertos", "Em aberto"),
            (store.STATUS_NOVO_LEAD, "Novo Lead"),
            (store.STATUS_EM_ATENDIMENTO, "Em Atendimento"),
            (store.STATUS_FINALIZADO, "Finalizado"),
        ],
        "sector_filter": ui_sector_filter,
        "sector_filter_locked": bool(scope.get("locked")),
        "user_sector_name": scope.get("sector_name") or "",
        "line_filter": active_line,
        "whatsapp_lines": whatsapp_lines,
        "evolution_configured": settings.evolution_configured,
        "unread_total": store.count_unread(evolution_instance=active_line) if active_line else store.count_unread(),
        "flash": flash,
        "error": error,
        "ai_mode_on": store.AI_MODE_ON,
        "ai_mode_paused": store.AI_MODE_PAUSED,
        "sector_options": sector_options,
        "responsible_options": responsible_options,
        "tag_options": tag_options,
        "quick_replies": quick_replies,
        "is_admin": (session_user or {}).get("role") == "Administrador",
        "can_delete_conversation": can_delete_attendance_conversation(
            session_user, request=request
        ),
    }


def ensure_crm_link(
    conversation: dict,
    *,
    contact_name: str = "",
    vendedor: str = "",
) -> dict:
    """Garante vínculo CRM pelo WhatsApp da conversa (corrige vínculo errado)."""
    if not conversation or not conversation.get("id"):
        return conversation
    phone = conversation.get("phone_e164", "")
    current_row = conversation.get("sheet_row")
    if current_row and attendance_crm.sheet_row_matches_phone(current_row, phone):
        if not conversation.get("registration_id"):
            try:
                from app.services.crm_registrations_storage import get_registration_by_sheet_row

                reg = get_registration_by_sheet_row(int(current_row))
                if reg:
                    return (
                        store.update_conversation(
                            conversation["id"],
                            registration_id=int(reg.id),
                        )
                        or conversation
                    )
            except Exception:
                pass
        return conversation

    # Vínculo ausente ou de outro contato — limpa e resolve de novo pelo telefone
    if current_row:
        store.update_conversation(
            conversation["id"],
            sheet_row=None,
            registration_id=None,
        )
        conversation = {**conversation, "sheet_row": None, "registration_id": None}

    sheet_row = attendance_crm.resolve_or_create_lead(
        phone=phone,
        contact_name=contact_name or conversation.get("contact_name", ""),
        vendedor=vendedor or conversation.get("assignee", ""),
    )
    if sheet_row:
        registration_id = None
        try:
            from app.services.crm_registrations_storage import get_registration_by_sheet_row

            reg = get_registration_by_sheet_row(int(sheet_row))
            if reg:
                registration_id = int(reg.id)
        except Exception:
            pass
        return (
            store.update_conversation(
                conversation["id"],
                sheet_row=int(sheet_row),
                registration_id=registration_id,
            )
            or conversation
        )
    return conversation


def send_text_message(
    conversation_id: str,
    text: str,
    *,
    sender: str = "agent",
    assignee: str = "",
) -> tuple[dict | None, str]:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return None, "Conversa não encontrada."
    body = str(text or "").strip()
    if not body:
        return None, "Digite uma mensagem."
    if not settings.evolution_configured:
        return None, "Evolution API não configurada. Defina as variáveis no Easypanel."

    # Anti-duplicata: mesmo texto enviado há menos de 4s (Enter + botão / corretor)
    try:
        recent = store.list_messages(conversation_id, limit=12)
        last_out = next(
            (item for item in reversed(recent or []) if item.get("direction") == "out"),
            None,
        )
        if last_out and normalize_text(last_out.get("body") or "") == body:
            created = normalize_text(last_out.get("created_at") or "")
            if created:
                from datetime import datetime, timezone

                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    now = datetime.now(tz=ts.tzinfo or timezone.utc)
                    if abs((now - ts).total_seconds()) < 2:
                        return last_out, ""
                except Exception:
                    pass
    except Exception:
        pass

    if evolution_client.is_self_chat(
        conversation.get("phone_e164") or "",
        conversation.get("remote_jid") or "",
        instance=conversation.get("evolution_instance") or "",
    ):
        owner = evolution_client.get_instance_owner_phone(
            conversation.get("evolution_instance") or ""
        )
        return None, (
            "Este chat é o mesmo número conectado na Evolution"
            + (f" ({owner})" if owner else "")
            + ". WhatsApp não entrega mensagem para si mesmo. "
            "Abra um chamado para outro celular e teste de novo."
        )

    try:
        response = evolution_client.send_text(
            conversation["phone_e164"],
            body,
            jid=conversation.get("remote_jid") or "",
            instance=conversation.get("evolution_instance") or "",
        )
    except EvolutionClientError as error:
        return None, str(error)

    evo_id = evolution_client.extract_message_id(response)
    message = store.add_message(
        conversation_id,
        direction="out",
        body=body,
        msg_type="text",
        evolution_id=evo_id,
        sender=sender,
        bump_unread=False,
    )
    updates: dict = {}
    if conversation.get("status") == store.STATUS_NOVO_LEAD:
        updates["status"] = store.STATUS_EM_ATENDIMENTO
    if assignee and not conversation.get("assignee"):
        updates["assignee"] = assignee
    # Atendente humano enviou → pausa a IA (evita confusão / resposta automática)
    if sender == "agent":
        updates["ai_mode"] = store.AI_MODE_PAUSED
    used_number = normalize_text(response.get("_oppi_send_number") or "")
    resolved_lid = normalize_text(response.get("_oppi_resolved_lid") or "")
    if resolved_lid and "@lid" in resolved_lid.lower():
        updates["remote_jid"] = resolved_lid
    elif used_number and "@lid" in used_number.lower() and used_number != normalize_text(
        conversation.get("remote_jid") or ""
    ):
        updates["remote_jid"] = used_number
    if updates:
        store.update_conversation(conversation_id, **updates)

    warning = ""
    status = normalize_text(response.get("_oppi_send_status") or "") or "UNKNOWN"
    used = used_number or (
        conversation.get("remote_jid") or conversation.get("phone_e164") or ""
    )
    if response.get("_oppi_delivery_pending"):
        has_lid = "@lid" in used.lower() or "@lid" in (resolved_lid or "").lower()
        if has_lid:
            warning = (
                f"⚠ Entrega PENDING mesmo com @lid · destino {used}. "
                "Provável Baileys desatualizado no servidor Evolution — "
                "atualize baileys@7.0.0-rc13 no Easypanel e reconecte o QR."
            )
        else:
            warning = (
                f"⚠ Entrega PENDING · destino {used} (sem @lid). "
                "Peça uma mensagem nova do cliente no WhatsApp e clique "
                "em CRM → Atualizar @lid, depois envie de novo."
            )
    else:
        warning = f"Enviado à Evolution · status {status} · destino {used}"
    return message, warning


def send_media_message(
    conversation_id: str,
    *,
    media_url: str,
    media_type: str = "image",
    caption: str = "",
    filename: str = "",
    mimetype: str = "",
    sender: str = "agent",
    store_media_url: str = "",
) -> tuple[dict | None, str]:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return None, "Conversa não encontrada."
    if not settings.evolution_configured:
        return None, "Evolution API não configurada."
    try:
        response = evolution_client.send_media(
            conversation["phone_e164"],
            media_url=media_url,
            media_type=media_type,
            caption=caption,
            filename=filename,
            mimetype=mimetype,
            jid=conversation.get("remote_jid") or "",
            instance=conversation.get("evolution_instance") or "",
        )
    except EvolutionClientError as error:
        return None, str(error)

    evo_id = evolution_client.extract_message_id(response)
    message = store.add_message(
        conversation_id,
        direction="out",
        body=caption,
        msg_type=media_type if media_type in ("image", "document", "audio", "video") else "document",
        media_url=store_media_url or media_url,
        media_mime=mimetype,
        media_filename=filename,
        evolution_id=evo_id,
        sender=sender,
    )
    return message, ""


def send_voice_message(
    conversation_id: str,
    *,
    audio_base64: str,
    mimetype: str = "audio/ogg",
    filename: str = "audio.ogg",
    sender: str = "agent",
    store_media_url: str = "",
) -> tuple[dict | None, str]:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return None, "Conversa não encontrada."
    if not settings.evolution_configured:
        return None, "Evolution API não configurada."
    try:
        response = evolution_client.send_whatsapp_audio(
            conversation["phone_e164"],
            audio_base64=audio_base64,
            jid=conversation.get("remote_jid") or "",
            mimetype=mimetype or "audio/ogg",
            instance=conversation.get("evolution_instance") or "",
        )
    except EvolutionClientError as error:
        return None, str(error)

    evo_id = evolution_client.extract_message_id(response)
    message = store.add_message(
        conversation_id,
        direction="out",
        body="",
        msg_type="audio",
        media_url=store_media_url,
        media_mime=mimetype,
        media_filename=filename,
        evolution_id=evo_id,
        sender=sender,
    )
    return message, ""


def send_quick_reply(
    conversation_id: str,
    shortcut: str,
    *,
    sender: str = "agent",
    assignee: str = "",
) -> tuple[dict | None, str]:
    """Dispara mensagem rápida cadastrada (texto/imagem/áudio/vídeo)."""
    from app.services import attendance_quick_replies as quick_replies
    import base64
    import shutil
    import uuid
    from pathlib import Path

    from app.services.storage_paths import get_storage_dir

    item = quick_replies.get_by_shortcut(shortcut)
    if not item:
        return None, "Atalho não encontrado. Cadastre em Configurações → Atendimentos."

    kind = item.get("media_type") or "text"
    body = str(item.get("body") or "").strip()

    if kind == "text":
        return send_text_message(
            conversation_id,
            body,
            sender=sender,
            assignee=assignee,
        )

    path = quick_replies.media_abs_path(item)
    if not path:
        return None, "Arquivo do atalho não encontrado. Cadastre novamente."

    raw = path.read_bytes()
    if not raw:
        return None, "Arquivo do atalho está vazio."

    mime = item.get("media_mime") or ""
    filename = item.get("media_filename") or path.name
    media_dir = get_storage_dir() / "attendance_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
    dest = media_dir / safe_name
    shutil.copyfile(path, dest)
    local_url = f"/atendimentos/media/{safe_name}"
    media_payload = base64.b64encode(raw).decode("ascii")

    if kind == "audio":
        return send_voice_message(
            conversation_id,
            audio_base64=media_payload,
            mimetype=mime or "audio/ogg",
            filename=filename,
            sender=sender,
            store_media_url=local_url,
        )

    return send_media_message(
        conversation_id,
        media_url=media_payload,
        media_type=kind if kind in ("image", "video", "audio", "document") else "document",
        caption=body,
        filename=filename,
        mimetype=mime,
        sender=sender,
        store_media_url=local_url,
    )


def assume_conversation(
    conversation_id: str,
    assignee: str,
    *,
    sector_id: str | int | None = None,
) -> dict | None:
    return assign_conversation(
        conversation_id,
        assignee=assignee,
        sector_id=sector_id,
        pause_ai=True,
        set_in_progress=True,
    )


def assign_conversation(
    conversation_id: str,
    *,
    assignee: str = "",
    sector_id: str | int | None = None,
    pause_ai: bool = True,
    set_in_progress: bool = True,
) -> dict | None:
    fields: dict = {}
    if set_in_progress:
        fields["status"] = store.STATUS_EM_ATENDIMENTO
    if pause_ai:
        fields["ai_mode"] = store.AI_MODE_PAUSED

    name = normalize_text(assignee)
    if name:
        fields["assignee"] = name

    try:
        from app.services.sectors import get_sector

        sector = get_sector(sector_id) if sector_id not in (None, "") else None
    except Exception:
        sector = None
    if sector:
        fields["sector_id"] = sector["id"]
        fields["sector_name"] = sector["name"]
    elif sector_id in (None, "", 0, "0"):
        # limpa só se explicitamente vazio e veio no form de direcionar
        pass

    if not fields:
        return store.get_conversation(conversation_id)
    return store.update_conversation(conversation_id, **fields)


def return_to_ai(conversation_id: str) -> dict | None:
    return store.update_conversation(conversation_id, ai_mode=store.AI_MODE_ON)


def finalize_conversation(conversation_id: str) -> dict | None:
    return store.update_conversation(
        conversation_id,
        status=store.STATUS_FINALIZADO,
        ai_mode=store.AI_MODE_OFF,
    )


def delete_conversation(conversation_id: str) -> bool:
    return store.delete_conversation(conversation_id)


def start_whatsapp_call(
    *,
    phone: str,
    contact_name: str = "",
    first_message: str = "",
    assignee: str = "",
    evolution_instance: str = "",
) -> tuple[dict | None, str]:
    """Abre chamado WhatsApp: cria/vincula lead e conversa na inbox."""
    from app.services.evolution_client import normalize_phone_from_jid

    phone_e164 = normalize_phone_from_jid(phone)
    if not phone_e164 or len(phone_e164) < 12:
        return None, "Informe um WhatsApp válido com DDD (ex.: 11999998888)."

    sheet_row = attendance_crm.resolve_or_create_lead(
        phone=phone_e164,
        contact_name=contact_name,
        vendedor=assignee,
    )
    name = normalize_text(contact_name)
    if not name and sheet_row:
        crm = attendance_crm.build_crm_panel(sheet_row)
        name = normalize_text(crm.get("contato") or crm.get("empresa"))

    instance = normalize_text(evolution_instance) or settings.evolution_primary_instance
    conversation = store.upsert_conversation_by_phone(
        phone_e164,
        contact_name=name or f"WhatsApp {phone_e164}",
        sheet_row=sheet_row,
        status=store.STATUS_EM_ATENDIMENTO if first_message or assignee else store.STATUS_NOVO_LEAD,
        remote_jid=f"{phone_e164}@s.whatsapp.net",
        evolution_instance=instance,
    )
    if not conversation:
        return None, "Não foi possível abrir a conversa."

    conversation_id = conversation["id"]
    updates: dict = {}
    if assignee:
        updates["assignee"] = normalize_text(assignee)
        updates["ai_mode"] = store.AI_MODE_PAUSED
        updates["status"] = store.STATUS_EM_ATENDIMENTO
    if updates:
        conversation = store.update_conversation(conversation_id, **updates) or conversation

    message_text = normalize_text(first_message)
    if message_text:
        if not settings.evolution_configured:
            return conversation, "Conversa aberta, mas a Evolution API não está configurada para enviar a mensagem."
        _, error = send_text_message(
            conversation_id,
            message_text,
            sender="agent",
            assignee=assignee,
        )
        if error:
            return conversation, f"Conversa aberta, mas a mensagem não foi enviada: {error}"
        conversation = store.get_conversation(conversation_id) or conversation

    return conversation, ""


def maybe_ai_reply(conversation_id: str, inbound_text: str) -> None:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return
    if not attendance_ai.should_reply(ai_mode=conversation.get("ai_mode", "")):
        return
    history = store.list_messages(conversation_id, limit=40)
    reply = attendance_ai.generate_reply(
        conversation=conversation,
        inbound_text=inbound_text,
        history=history,
    )
    if not reply:
        return
    message, error = send_text_message(conversation_id, reply, sender="ai")
    if error:
        logger.warning("IA não enviou resposta: %s", error)
    elif message:
        logger.info("IA respondeu na conversa %s", conversation_id)


_SYNC_LAST_AT: dict[str, float] = {}
_SYNC_MIN_INTERVAL_SEC = 10.0
_SYNC_IN_FLIGHT: set[str] = set()
_SYNC_GUARD = threading.Lock()


_INBOX_SYNC_LAST = 0.0
_INBOX_SYNC_MIN_INTERVAL_SEC = 45.0
_INBOX_SYNC_LOCK = threading.Lock()
_INBOX_SYNC_IN_FLIGHT = False


def schedule_open_enrichment(conversation_id: str) -> None:
    """CRM + mídia em background ao abrir o chat (não bloqueia o worker)."""
    conversation_id = normalize_text(conversation_id)
    if not conversation_id:
        return

    def _run() -> None:
        try:
            conv = store.get_conversation(conversation_id)
            if not conv:
                return
            try:
                ensure_crm_link(conv)
            except Exception:
                logger.exception("ensure_crm_link em background falhou (%s)", conversation_id)
            try:
                from app.services.attendance_media import hydrate_messages_media

                messages = store.list_messages(conversation_id)
                hydrate_messages_media(messages, conversation=conv, limit=8)
            except Exception:
                logger.exception("hydrate media em background falhou (%s)", conversation_id)
        except Exception:
            logger.exception("schedule_open_enrichment falhou (%s)", conversation_id)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"att-open-{conversation_id[:10]}",
    ).start()


def schedule_sync_inbox_from_evolution(*, force: bool = False) -> None:
    """Importa chats recentes da Evolution em background (contatos que o webhook perdeu)."""

    def _run() -> None:
        try:
            sync_inbox_from_evolution(force=force)
        except Exception:
            logger.exception("Sync inbox Evolution falhou")

    threading.Thread(target=_run, daemon=True, name="evo-inbox-sync").start()


def sync_inbox_from_evolution(*, force: bool = False, limit: int = 20) -> int:
    """Lista chats e puxa msgs só dos N mais recentes — sem empilhar threads."""
    import time

    global _INBOX_SYNC_LAST, _INBOX_SYNC_IN_FLIGHT
    with _INBOX_SYNC_LOCK:
        now = time.monotonic()
        if _INBOX_SYNC_IN_FLIGHT:
            return 0
        if not force and (now - _INBOX_SYNC_LAST) < _INBOX_SYNC_MIN_INTERVAL_SEC:
            return 0
        _INBOX_SYNC_IN_FLIGHT = True
        _INBOX_SYNC_LAST = now

    try:
        if not settings.evolution_configured:
            return 0

        instances = settings.evolution_instances or [settings.evolution_primary_instance]
        imported = 0
        # findMessages é caro — só nos chats mais recentes por linha
        message_sync_budget = 6
        chat_limit = max(1, min(int(limit or 20), 30))

        for instance in instances:
            try:
                chats = evolution_client.fetch_recent_chats(
                    limit=chat_limit, instance=instance
                )
            except Exception:
                logger.exception("fetch_recent_chats falhou instance=%s", instance)
                continue

            synced_msgs = 0
            for chat in chats:
                remote_jid = normalize_text(chat.get("remote_jid") or "")
                phone = normalize_text(chat.get("phone_e164") or "")
                name = normalize_text(chat.get("contact_name") or "")
                line = normalize_text(chat.get("evolution_instance") or "") or instance
                if not remote_jid:
                    continue
                try:
                    if phone:
                        conversation = store.upsert_conversation_by_phone(
                            phone,
                            contact_name=name,
                            remote_jid=remote_jid,
                            evolution_instance=line,
                            ignore_suppression=True,
                        )
                    else:
                        conversation = store.upsert_conversation_by_remote_jid(
                            remote_jid,
                            contact_name=name,
                            phone_e164=phone,
                            evolution_instance=line,
                            ignore_suppression=True,
                        )
                    if not conversation:
                        continue
                    imported += 1
                    if synced_msgs < message_sync_budget:
                        sync_messages_from_evolution(
                            conversation["id"], limit=12, force=False
                        )
                        synced_msgs += 1
                except Exception:
                    logger.exception("Falha ao importar chat %s", remote_jid)
        return imported
    finally:
        with _INBOX_SYNC_LOCK:
            _INBOX_SYNC_IN_FLIGHT = False


def schedule_sync_messages_from_evolution(
    conversation_id: str,
    *,
    limit: int = 30,
    force: bool = False,
) -> None:
    """Dispara sync em thread daemon — nunca bloqueia o worker do FastAPI/webhook."""
    conversation_id = normalize_text(conversation_id)
    if not conversation_id:
        return

    def _run() -> None:
        try:
            sync_messages_from_evolution(conversation_id, limit=limit, force=force)
        except Exception:
            logger.exception("Sync Evolution em background falhou (%s)", conversation_id)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"evo-sync-{conversation_id[:10]}",
    ).start()


def sync_messages_from_evolution(
    conversation_id: str,
    *,
    limit: int = 30,
    force: bool = False,
) -> int:
    """Puxa mensagens do Evolution para a conversa (compensa webhook perdido/travado)."""
    import time

    conversation_id = normalize_text(conversation_id)
    if not conversation_id:
        return 0

    with _SYNC_GUARD:
        now = time.monotonic()
        last = _SYNC_LAST_AT.get(conversation_id, 0.0)
        if not force and (now - last) < _SYNC_MIN_INTERVAL_SEC:
            return 0
        if conversation_id in _SYNC_IN_FLIGHT:
            return 0
        _SYNC_IN_FLIGHT.add(conversation_id)
        _SYNC_LAST_AT[conversation_id] = now

    try:
        conversation = store.get_conversation(conversation_id)
        if not conversation or not settings.evolution_configured:
            return 0

        targets: list[str] = []
        remote = normalize_text(conversation.get("remote_jid") or "")
        phone = normalize_text(conversation.get("phone_e164") or "")
        if remote:
            targets.append(remote)
        if phone:
            targets.append(f"{phone}@s.whatsapp.net")
            try:
                from app.services.evolution_client import phone_match_variants

                for variant in phone_match_variants(phone):
                    targets.append(f"{variant}@s.whatsapp.net")
            except Exception:
                pass
        targets = list(dict.fromkeys(t for t in targets if t))
        if not targets:
            return 0

        from app.routers.evolution_webhook import ingest_evolution_message_item

        instance = normalize_text(conversation.get("evolution_instance") or "")
        imported = 0
        for jid in targets:
            try:
                records = evolution_client.find_messages(jid, limit=limit, instance=instance)
            except Exception:
                logger.exception("findMessages falhou para %s", jid)
                continue
            for item in records:
                try:
                    if ingest_evolution_message_item(
                        item,
                        push_name=conversation.get("contact_name") or "",
                        evolution_instance=instance,
                        allow_reopen=True,
                    ):
                        imported += 1
                except Exception:
                    logger.exception("Falha ao importar mensagem Evolution")
            if imported:
                break
        return imported
    finally:
        with _SYNC_GUARD:
            _SYNC_IN_FLIGHT.discard(conversation_id)


def update_notes_tags(
    conversation_id: str,
    *,
    notes: str | None = None,
    tags: list[str] | None = None,
) -> dict | None:
    fields: dict = {}
    if notes is not None:
        fields["notes"] = notes
    if tags is not None:
        fields["tags"] = tags
    if not fields:
        return store.get_conversation(conversation_id)
    return store.update_conversation(conversation_id, **fields)
