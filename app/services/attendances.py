"""Domínio de Atendimentos (lista, enviar, assumir, finalizar)."""
from __future__ import annotations

import logging

from app.config import settings
from app.services import attendance_ai, attendance_crm, attendances_storage as store
from app.services import evolution_client
from app.services.evolution_client import EvolutionClientError
from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)


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


def page_context(
    *,
    search: str = "",
    status: str = "",
    sector_filter: str = "",
    selected_id: str = "",
    session_user: dict | None = None,
    flash: str = "",
    error: str = "",
) -> dict:
    # Remove grupos reais (@g.us) e as conversas pedidas (Luiz / Skoob)
    try:
        store.purge_group_conversations()
        store.delete_conversations_by_contact_names()
    except Exception:
        logger.exception("Falha ao limpar conversas indesejadas da inbox")

    effective_sector, scope = _resolve_sector_filter(session_user, sector_filter)
    conversations = store.list_conversations(
        search=search,
        status=status,
        sector_id=effective_sector,
    )
    selected = None
    messages: list[dict] = []
    crm = attendance_crm.build_crm_panel(None)
    sector_options: list[dict] = []
    responsible_options: list[str] = []
    tag_options: list[str] = []
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
                effective_sector is not None
                and selected.get("sector_id") not in (None, effective_sector)
                and int(selected.get("sector_id") or 0) != int(effective_sector)
            ):
                # Fora do escopo do usuário/filtro — não abre a conversa
                selected = None
                selected_id = ""
            else:
                store.mark_conversation_read(selected_id)
                selected = store.get_conversation(selected_id)
                messages = store.list_messages(selected_id)
                crm = attendance_crm.build_crm_panel(selected.get("sheet_row") if selected else None)
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
            ("todos", "Todos"),
        ] + store.STATUS_OPTIONS,
        "sector_filter": ui_sector_filter,
        "sector_filter_locked": bool(scope.get("locked")),
        "user_sector_name": scope.get("sector_name") or "",
        "evolution_configured": settings.evolution_configured,
        "unread_total": store.count_unread(),
        "flash": flash,
        "error": error,
        "ai_mode_on": store.AI_MODE_ON,
        "ai_mode_paused": store.AI_MODE_PAUSED,
        "sector_options": sector_options,
        "responsible_options": responsible_options,
        "tag_options": tag_options,
    }


def ensure_crm_link(conversation: dict, *, contact_name: str = "") -> dict:
    if conversation.get("sheet_row"):
        return conversation
    sheet_row = attendance_crm.resolve_or_create_lead(
        phone=conversation.get("phone_e164", ""),
        contact_name=contact_name or conversation.get("contact_name", ""),
    )
    if sheet_row:
        return store.update_conversation(conversation["id"], sheet_row=int(sheet_row)) or conversation
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

    try:
        response = evolution_client.send_text(
            conversation["phone_e164"],
            body,
            jid=conversation.get("remote_jid") or "",
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
    if updates:
        store.update_conversation(conversation_id, **updates)
    return message, ""


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
