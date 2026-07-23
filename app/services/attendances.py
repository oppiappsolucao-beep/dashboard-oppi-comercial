"""Domínio de Atendimentos (lista, enviar, assumir, finalizar)."""
from __future__ import annotations

import logging

from app.config import settings
from app.services import attendance_ai, attendance_crm, attendances_storage as store
from app.services import evolution_client
from app.services.evolution_client import EvolutionClientError
from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)


def page_context(
    *,
    search: str = "",
    status: str = "",
    selected_id: str = "",
    flash: str = "",
    error: str = "",
) -> dict:
    conversations = store.list_conversations(search=search, status=status)
    selected = None
    messages: list[dict] = []
    crm = attendance_crm.build_crm_panel(None)
    if selected_id:
        selected = store.get_conversation(selected_id)
        if selected:
            store.mark_conversation_read(selected_id)
            selected = store.get_conversation(selected_id)
            messages = store.list_messages(selected_id)
            crm = attendance_crm.build_crm_panel(selected.get("sheet_row"))
    elif conversations:
        # sem seleção explícita: não auto-abre no MVP (lista só)
        pass

    return {
        "active_page": "attendances",
        "conversations": conversations,
        "selected": selected,
        "messages": messages,
        "crm": crm,
        "search": search,
        "status_filter": status or "todos",
        "status_options": [("todos", "Todos")] + store.STATUS_OPTIONS,
        "evolution_configured": settings.evolution_configured,
        "unread_total": store.count_unread(),
        "flash": flash,
        "error": error,
        "ai_mode_on": store.AI_MODE_ON,
        "ai_mode_paused": store.AI_MODE_PAUSED,
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
        response = evolution_client.send_text(conversation["phone_e164"], body)
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


def assume_conversation(conversation_id: str, assignee: str) -> dict | None:
    return store.update_conversation(
        conversation_id,
        ai_mode=store.AI_MODE_PAUSED,
        assignee=normalize_text(assignee) or "Atendente",
        status=store.STATUS_EM_ATENDIMENTO,
    )


def return_to_ai(conversation_id: str) -> dict | None:
    return store.update_conversation(conversation_id, ai_mode=store.AI_MODE_ON)


def finalize_conversation(conversation_id: str) -> dict | None:
    return store.update_conversation(
        conversation_id,
        status=store.STATUS_FINALIZADO,
        ai_mode=store.AI_MODE_OFF,
    )


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
