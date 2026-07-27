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

    # Compensa webhook perdido: puxa chats recentes sem bloquear a tela
    try:
        schedule_sync_inbox_from_evolution(force=False)
    except Exception:
        logger.exception("Falha ao agendar sync inbox Evolution")

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
            (store.STATUS_NOVO_LEAD, "Novo Lead"),
            (store.STATUS_EM_ATENDIMENTO, "Em Atendimento"),
            (store.STATUS_FINALIZADO, "Finalizado"),
        ],
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


def ensure_crm_link(
    conversation: dict,
    *,
    contact_name: str = "",
    vendedor: str = "",
) -> dict:
    if conversation.get("sheet_row"):
        return conversation
    sheet_row = attendance_crm.resolve_or_create_lead(
        phone=conversation.get("phone_e164", ""),
        contact_name=contact_name or conversation.get("contact_name", ""),
        vendedor=vendedor or conversation.get("assignee", ""),
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


def start_whatsapp_call(
    *,
    phone: str,
    contact_name: str = "",
    first_message: str = "",
    assignee: str = "",
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

    conversation = store.upsert_conversation_by_phone(
        phone_e164,
        contact_name=name or f"WhatsApp {phone_e164}",
        sheet_row=sheet_row,
        status=store.STATUS_EM_ATENDIMENTO if first_message or assignee else store.STATUS_NOVO_LEAD,
        remote_jid=f"{phone_e164}@s.whatsapp.net",
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
_SYNC_MIN_INTERVAL_SEC = 30.0
_SYNC_IN_FLIGHT: set[str] = set()
_SYNC_GUARD = threading.Lock()


_INBOX_SYNC_LAST = 0.0
_INBOX_SYNC_MIN_INTERVAL_SEC = 45.0
_INBOX_SYNC_LOCK = threading.Lock()


def schedule_sync_inbox_from_evolution(*, force: bool = False) -> None:
    """Importa chats recentes da Evolution em background (contatos que o webhook perdeu)."""

    def _run() -> None:
        try:
            sync_inbox_from_evolution(force=force)
        except Exception:
            logger.exception("Sync inbox Evolution falhou")

    threading.Thread(target=_run, daemon=True, name="evo-inbox-sync").start()


def sync_inbox_from_evolution(*, force: bool = False, limit: int = 40) -> int:
    import time

    global _INBOX_SYNC_LAST
    with _INBOX_SYNC_LOCK:
        now = time.monotonic()
        if not force and (now - _INBOX_SYNC_LAST) < _INBOX_SYNC_MIN_INTERVAL_SEC:
            return 0
        _INBOX_SYNC_LAST = now

    if not settings.evolution_configured:
        return 0

    try:
        chats = evolution_client.fetch_recent_chats(limit=limit)
    except Exception:
        logger.exception("fetch_recent_chats falhou")
        return 0

    imported = 0
    for chat in chats:
        remote_jid = normalize_text(chat.get("remote_jid") or "")
        phone = normalize_text(chat.get("phone_e164") or "")
        name = normalize_text(chat.get("contact_name") or "")
        if not remote_jid:
            continue
        try:
            if phone:
                conversation = store.upsert_conversation_by_phone(
                    phone,
                    contact_name=name,
                    remote_jid=remote_jid,
                )
            else:
                conversation = store.upsert_conversation_by_remote_jid(
                    remote_jid,
                    contact_name=name,
                    phone_e164=phone,
                )
            if not conversation:
                continue
            # Puxa mensagens recentes desse chat (já tem throttle por conversa)
            sync_messages_from_evolution(conversation["id"], limit=15, force=False)
            imported += 1
        except Exception:
            logger.exception("Falha ao importar chat %s", remote_jid)
    return imported


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

        imported = 0
        for jid in targets:
            try:
                records = evolution_client.find_messages(jid, limit=limit)
            except Exception:
                logger.exception("findMessages falhou para %s", jid)
                continue
            for item in records:
                try:
                    if ingest_evolution_message_item(
                        item,
                        push_name=conversation.get("contact_name") or "",
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
