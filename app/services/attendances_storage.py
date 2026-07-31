"""Persistência de conversas/mensagens de Atendimentos em DATABASE_URL."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.services.legacy_core import normalize_digits, normalize_text
from database.connection import SessionLocal
from database.models import (
    AttendanceConversation,
    AttendanceMessage,
    AttendanceSuppressedChat,
)

logger = logging.getLogger(__name__)


def _primary_evolution_instance() -> str:
    try:
        from app.config import settings

        return normalize_text(settings.evolution_primary_instance)
    except Exception:
        return ""


def resolve_evolution_instance(value: str | None = None) -> str:
    """Nome canônico da linha; vazio no banco = primary."""
    name = normalize_text(value or "")
    primary = _primary_evolution_instance()
    if not name:
        return primary
    try:
        from app.config import settings

        for configured in settings.evolution_instances:
            if configured.lower() == name.lower():
                return configured
    except Exception:
        pass
    return name


def _sql_instance_match(wanted: str):
    """Filtro SQL: conversas da linha (legado vazio = primary)."""
    primary = _primary_evolution_instance()
    wanted_n = normalize_text(wanted) or primary
    wanted_l = wanted_n.lower()
    primary_l = primary.lower()
    col = AttendanceConversation.evolution_instance
    if wanted_l == primary_l or not primary_l:
        return or_(
            func.lower(col) == wanted_l,
            col == "",
            col.is_(None),
        )
    return func.lower(col) == wanted_l


STATUS_NOVO_LEAD = "novo_lead"
STATUS_EM_ATENDIMENTO = "em_atendimento"
STATUS_FINALIZADO = "finalizado"
STATUS_EXCLUIDO = "excluido"
STATUS_OPTIONS = [
    (STATUS_NOVO_LEAD, "Novo Lead"),
    (STATUS_EM_ATENDIMENTO, "Em Atendimento"),
    (STATUS_FINALIZADO, "Finalizado"),
]
STATUS_LABELS = dict(STATUS_OPTIONS)
STATUS_LABELS[STATUS_EXCLUIDO] = "Excluído"

AI_MODE_ON = "on"
AI_MODE_PAUSED = "paused"
AI_MODE_OFF = "off"

_lock = threading.Lock()
_event_seq = 0
_event_listeners: list = []


def _now() -> datetime:
    try:
        return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


@contextmanager
def _session(*, commit: bool = True):
    db = SessionLocal()
    try:
        yield db
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _notify(event: dict) -> None:
    global _event_seq
    with _lock:
        _event_seq += 1
        payload = {**event, "seq": _event_seq, "at": _now_iso()}
        listeners = list(_event_listeners)
    for queue in listeners:
        try:
            queue.put_nowait(payload)
        except Exception:
            pass


def subscribe_events():
    import queue

    q: queue.Queue = queue.Queue(maxsize=100)
    with _lock:
        _event_listeners.append(q)
    return q


def unsubscribe_events(q) -> None:
    with _lock:
        if q in _event_listeners:
            _event_listeners.remove(q)


def _initials(name: str) -> str:
    parts = [p for p in normalize_text(name).split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _conversation_to_dict(row: AttendanceConversation | None) -> dict:
    if row is None:
        return {}
    try:
        tags = json.loads(row.tags_json or "[]")
    except json.JSONDecodeError:
        tags = []
    if not isinstance(tags, list):
        tags = []
    status = normalize_text(row.status) or STATUS_NOVO_LEAD
    return {
        "id": row.id,
        "phone_e164": row.phone_e164,
        "remote_jid": row.remote_jid or "",
        "contact_name": row.contact_name or "",
        "profile_pic_url": row.profile_pic_url or "",
        "sheet_row": int(row.sheet_row) if row.sheet_row else None,
        "registration_id": int(row.registration_id)
        if getattr(row, "registration_id", None)
        else None,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "assignee": row.assignee or "",
        "ai_mode": row.ai_mode or AI_MODE_ON,
        "tags": [normalize_text(t) for t in tags if normalize_text(t)],
        "notes": row.notes or "",
        "last_message_at": row.last_message_at or "",
        "last_message_preview": row.last_message_preview or "",
        "unread_count": int(row.unread_count or 0),
        "typing": bool(row.typing),
        "sector_id": int(row.sector_id) if getattr(row, "sector_id", None) else None,
        "sector_name": getattr(row, "sector_name", None) or "",
        "evolution_instance": resolve_evolution_instance(
            getattr(row, "evolution_instance", None) or ""
        ),
        "created_at": row.created_at or "",
        "updated_at": row.updated_at or "",
        "initials": _initials(row.contact_name or row.phone_e164),
    }


def _message_to_dict(row: AttendanceMessage | None) -> dict:
    if row is None:
        return {}
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "direction": row.direction,
        "type": row.msg_type or "text",
        "body": row.body or "",
        "media_url": row.media_url or "",
        "media_mime": row.media_mime or "",
        "media_filename": row.media_filename or "",
        "evolution_id": row.evolution_id or "",
        "sender": row.sender or "contact",
        "created_at": row.created_at or "",
    }


def get_conversation(conversation_id: str) -> dict | None:
    with _session(commit=False) as db:
        row = db.get(AttendanceConversation, conversation_id)
        if not row:
            return None
        if (row.status or "") == STATUS_EXCLUIDO:
            return None
        return _conversation_to_dict(row)


def get_conversation_by_phone(
    phone_e164: str,
    *,
    evolution_instance: str | None = None,
) -> dict | None:
    from app.services.evolution_client import phone_match_variants

    phone = normalize_text(phone_e164)
    if not phone:
        return None
    variants = phone_match_variants(phone) or [phone]
    with _session(commit=False) as db:
        q = db.query(AttendanceConversation).filter(
            AttendanceConversation.phone_e164.in_(variants),
            AttendanceConversation.status != STATUS_EXCLUIDO,
        )
        if evolution_instance is not None:
            instance = resolve_evolution_instance(evolution_instance)
            if instance:
                q = q.filter(_sql_instance_match(instance))
        row = q.order_by(AttendanceConversation.updated_at.desc()).first()
        return _conversation_to_dict(row) if row else None


def get_conversation_by_remote_jid(
    remote_jid: str,
    *,
    evolution_instance: str | None = None,
) -> dict | None:
    """Localiza conversa pelo JID (@lid ou @s.whatsapp.net) — útil quando o webhook não traz telefone."""
    jid = normalize_text(remote_jid)
    if not jid:
        return None
    with _session(commit=False) as db:
        q = db.query(AttendanceConversation).filter(
            AttendanceConversation.remote_jid == jid,
            AttendanceConversation.status != STATUS_EXCLUIDO,
        )
        if evolution_instance is not None:
            instance = resolve_evolution_instance(evolution_instance)
            if instance:
                q = q.filter(_sql_instance_match(instance))
        row = q.order_by(AttendanceConversation.updated_at.desc()).first()
        return _conversation_to_dict(row) if row else None


def upsert_conversation_by_remote_jid(
    remote_jid: str,
    *,
    contact_name: str = "",
    phone_e164: str = "",
    evolution_instance: str = "",
    ignore_suppression: bool = False,
) -> dict:
    """Cria/atualiza conversa a partir do JID — cobre contato novo que só chega como @lid."""
    from app.services.evolution_client import (
        is_placeholder_whatsapp_phone,
        is_usable_whatsapp_identity,
        is_whatsapp_group_jid,
        normalize_phone_from_jid,
    )

    remote_jid = normalize_text(remote_jid)
    contact_name = normalize_text(contact_name)
    phone = normalize_text(phone_e164)
    instance = resolve_evolution_instance(evolution_instance)
    if is_placeholder_whatsapp_phone(phone):
        phone = ""
    # Nunca derive telefone a partir de @lid (vira número inventado enorme)
    if not phone and remote_jid and "@lid" not in remote_jid.lower():
        phone = normalize_phone_from_jid(remote_jid)
    if not remote_jid or is_whatsapp_group_jid(remote_jid):
        return {}
    # Sem JID real (@…) e sem telefone válido → não cria "wa:490…" fantasma
    if "@" not in remote_jid and not (
        phone and len(normalize_digits(phone)) >= 10 and len(normalize_digits(phone)) <= 15
    ):
        return {}
    if not is_usable_whatsapp_identity(phone=phone, remote_jid=remote_jid):
        return {}
    if phone and is_whatsapp_group_jid(phone):
        phone = ""
    if _is_unwanted_inbox_contact(contact_name):
        return {}
    if not ignore_suppression and is_chat_suppressed(
        phone_e164=phone, remote_jid=remote_jid, evolution_instance=instance
    ):
        return {}

    if phone and len(normalize_digits(phone)) >= 10 and len(normalize_digits(phone)) <= 15:
        return upsert_conversation_by_phone(
            phone,
            contact_name=contact_name,
            remote_jid=remote_jid,
            evolution_instance=instance,
            ignore_suppression=ignore_suppression,
        )

    existing = get_conversation_by_remote_jid(
        remote_jid, evolution_instance=instance or ""
    )
    if existing:
        if (existing.get("status") or "") == STATUS_EXCLUIDO and not ignore_suppression:
            return {}
        if (existing.get("status") or "") == STATUS_EXCLUIDO and ignore_suppression:
            return (
                update_conversation(
                    existing["id"],
                    status=STATUS_NOVO_LEAD,
                    contact_name=contact_name or existing.get("contact_name") or "",
                )
                or existing
            )
        updates: dict = {}
        if contact_name and not normalize_text(existing.get("contact_name") or ""):
            updates["contact_name"] = contact_name
        if instance and not normalize_text(existing.get("evolution_instance") or ""):
            updates["evolution_instance"] = instance
        if is_placeholder_whatsapp_phone(existing.get("phone_e164") or ""):
            updates["phone_e164"] = ""
        if updates:
            return update_conversation(existing["id"], **updates) or existing
        return existing

    # phone vazio + remote_jid @lid — envio usa o JID, nunca inventa wa:ID
    now = _now_iso()
    with _lock, _session() as db:
        conversation_id = _new_id("c_")
        row = AttendanceConversation(
            id=conversation_id,
            phone_e164="",
            contact_name=contact_name or "WhatsApp",
            profile_pic_url="",
            sheet_row=None,
            status=STATUS_NOVO_LEAD,
            assignee="",
            ai_mode=AI_MODE_ON,
            tags_json="[]",
            notes="",
            last_message_at="",
            last_message_preview="",
            unread_count=0,
            typing=False,
            remote_jid=remote_jid,
            evolution_instance=instance,
            sector_id=None,
            sector_name="",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        result = _conversation_to_dict(row)
    _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
    return result


def purge_group_conversations() -> dict:
    """Apaga do banco conversas de grupo e placeholders inválidos (wa:…)."""
    from app.services.evolution_client import (
        is_placeholder_whatsapp_phone,
        is_usable_whatsapp_identity,
        is_whatsapp_group_jid,
    )

    removed_ids: list[str] = []
    removed_names: list[str] = []
    with _lock, _session() as db:
        rows = db.query(AttendanceConversation).all()
        for row in rows:
            phone = row.phone_e164 or ""
            jid = row.remote_jid or ""
            bad_group = is_whatsapp_group_jid(jid) or is_whatsapp_group_jid(phone)
            bad_placeholder = is_placeholder_whatsapp_phone(phone)
            bad_identity = not is_usable_whatsapp_identity(phone=phone, remote_jid=jid)
            if not (bad_group or bad_placeholder or bad_identity):
                continue
            # Não apaga lead com telefone BR válido (DDD real) só porque jid está vazio
            from app.services.evolution_client import is_valid_br_whatsapp_phone

            if (
                not bad_group
                and not bad_placeholder
                and is_valid_br_whatsapp_phone(phone)
            ):
                continue
            removed_ids.append(row.id)
            removed_names.append(row.contact_name or row.phone_e164 or row.id)
            db.query(AttendanceMessage).filter(
                AttendanceMessage.conversation_id == row.id
            ).delete(synchronize_session=False)
            db.delete(row)
    if removed_ids:
        logger.info(
            "Removidas %s conversas inválidas/grupo: %s (%s)",
            len(removed_ids),
            removed_ids,
            removed_names,
        )
        _notify({"type": "groups_purged", "count": len(removed_ids), "ids": removed_ids})
    return {"removed": len(removed_ids), "ids": removed_ids, "names": removed_names}


def _normalize_contact_key(value: str) -> str:
    import re
    import unicodedata

    text = normalize_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


# Conversas pedidas para remoção explícita (não são filtro genérico de grupo).
UNWANTED_INBOX_CONTACT_KEYS = {
    _normalize_contact_key("Luiz Carlos Zanini"),
    _normalize_contact_key("Skoob Pet Indaiatuba"),
    _normalize_contact_key("SkoobPet Indaiatuba"),
    _normalize_contact_key("Skoob filhotes disponíveis"),
    _normalize_contact_key("Skoob filhotes disponiveis"),
}


def _is_unwanted_inbox_contact(name: str) -> bool:
    key = _normalize_contact_key(name)
    if not key:
        return False
    if key in UNWANTED_INBOX_CONTACT_KEYS:
        return True
    # Qualquer variação "Skoob …" que vaze de grupo/lista
    return key.startswith("skoob")



def delete_conversations_by_contact_names(names: list[str] | None = None) -> dict:
    """Apaga conversas cujo nome bate com a lista (ex.: Luiz / Skoob)."""
    if names:
        keys = {_normalize_contact_key(n) for n in names if _normalize_contact_key(n)}
        def _match(name: str) -> bool:
            return _normalize_contact_key(name) in keys
    else:
        def _match(name: str) -> bool:
            return _is_unwanted_inbox_contact(name)

    removed_ids: list[str] = []
    removed_names: list[str] = []
    with _lock, _session() as db:
        rows = db.query(AttendanceConversation).all()
        for row in rows:
            if not _match(row.contact_name or ""):
                continue
            removed_ids.append(row.id)
            removed_names.append(row.contact_name or row.phone_e164 or row.id)
            db.query(AttendanceMessage).filter(
                AttendanceMessage.conversation_id == row.id
            ).delete(synchronize_session=False)
            db.delete(row)
    if removed_ids:
        logger.info(
            "Removidas %s conversas pedidas: %s (%s)",
            len(removed_ids),
            removed_ids,
            removed_names,
        )
        _notify({"type": "named_conversations_deleted", "count": len(removed_ids), "ids": removed_ids})
    return {"removed": len(removed_ids), "ids": removed_ids, "names": removed_names}


def is_chat_suppressed(
    *,
    phone_e164: str = "",
    remote_jid: str = "",
    evolution_instance: str = "",
) -> bool:
    """True se este contato foi excluído pelo admin (telefone/jid; qualquer linha)."""
    phone = normalize_text(phone_e164)
    jid = normalize_text(remote_jid)
    if not phone and not jid:
        return False

    phone_variants: list[str] = []
    if phone:
        try:
            from app.services.evolution_client import phone_match_variants

            phone_variants = [v for v in (phone_match_variants(phone) or [phone]) if v]
        except Exception:
            phone_variants = [phone]

    # Soft-delete: não depende de instância — evita sync recriar o mesmo WhatsApp
    try:
        with _session(commit=False) as db:
            q = db.query(AttendanceConversation.id).filter(
                AttendanceConversation.status == STATUS_EXCLUIDO
            )
            if phone_variants and q.filter(
                AttendanceConversation.phone_e164.in_(phone_variants)
            ).first():
                return True
            if jid and q.filter(AttendanceConversation.remote_jid == jid).first():
                return True
    except Exception:
        logger.exception("Falha ao checar soft-delete de conversa")

    # Tabela auxiliar
    try:
        with _session(commit=False) as db:
            q = db.query(AttendanceSuppressedChat.id)
            if phone_variants and q.filter(
                AttendanceSuppressedChat.phone_e164.in_(phone_variants)
            ).first():
                return True
            if jid and q.filter(AttendanceSuppressedChat.remote_jid == jid).first():
                return True
            return False
    except Exception:
        return False


def suppress_chat(
    *,
    phone_e164: str = "",
    remote_jid: str = "",
    evolution_instance: str = "",
) -> None:
    phone = normalize_text(phone_e164)
    jid = normalize_text(remote_jid)
    instance = resolve_evolution_instance(evolution_instance)
    if not phone and not jid:
        return
    try:
        with _lock, _session() as db:
            q = db.query(AttendanceSuppressedChat)
            if phone:
                q = q.filter(AttendanceSuppressedChat.phone_e164 == phone)
            elif jid:
                q = q.filter(AttendanceSuppressedChat.remote_jid == jid)
            if instance:
                q = q.filter(
                    or_(
                        func.lower(AttendanceSuppressedChat.evolution_instance)
                        == instance.lower(),
                        AttendanceSuppressedChat.evolution_instance == "",
                        AttendanceSuppressedChat.evolution_instance.is_(None),
                    )
                )
            if q.first():
                return
            db.add(
                AttendanceSuppressedChat(
                    phone_e164=phone,
                    remote_jid=jid,
                    evolution_instance=instance,
                    suppressed_at=_now_iso(),
                )
            )
    except Exception:
        logger.exception("suppress_chat falhou (soft-delete já aplicado)")


def clear_all_chat_suppressions(*, reopen_excluded: bool = True) -> dict:
    """Emergência: limpa bloqueios de exclusão para a inbox voltar a receber."""
    removed_rows = 0
    reopened = 0
    # Sem _lock global — evita deadlock com webhook/sync segurando a mesma lock.
    try:
        with _session() as db:
            removed_rows = db.query(AttendanceSuppressedChat).delete()
            if reopen_excluded:
                rows = (
                    db.query(AttendanceConversation)
                    .filter(AttendanceConversation.status == STATUS_EXCLUIDO)
                    .all()
                )
                for row in rows:
                    row.status = STATUS_NOVO_LEAD
                    row.updated_at = _now_iso()
                    reopened += 1
    except Exception:
        logger.exception("clear_all_chat_suppressions falhou")
        return {"ok": False, "removed": removed_rows, "reopened": reopened}
    return {"ok": True, "removed": int(removed_rows or 0), "reopened": int(reopened or 0)}


def clear_chat_suppression(
    *,
    phone_e164: str = "",
    remote_jid: str = "",
    evolution_instance: str = "",
) -> int:
    """Remove bloqueio (ex.: lead mandou mensagem nova após exclusão).

    Limpa por telefone/jid em QUALQUER linha — suppress é global; filtrar por
    instância deixava o chat bloqueado quando o webhook vinha com outro nome.
    """
    phone = normalize_text(phone_e164)
    jid = normalize_text(remote_jid)
    if not phone and not jid:
        return 0
    removed = 0
    # Reabre soft-delete
    try:
        with _lock, _session() as db:
            q = db.query(AttendanceConversation).filter(
                AttendanceConversation.status == STATUS_EXCLUIDO
            )
            if phone:
                try:
                    from app.services.evolution_client import phone_match_variants

                    variants = [v for v in (phone_match_variants(phone) or [phone]) if v]
                except Exception:
                    variants = [phone]
                if variants:
                    for row in q.filter(
                        AttendanceConversation.phone_e164.in_(variants)
                    ).all():
                        row.status = STATUS_NOVO_LEAD
                        row.updated_at = _now_iso()
                        removed += 1
            elif jid:
                for row in q.filter(AttendanceConversation.remote_jid == jid).all():
                    row.status = STATUS_NOVO_LEAD
                    row.updated_at = _now_iso()
                    removed += 1
    except Exception:
        logger.exception("Falha ao reabrir soft-delete")

    try:
        with _lock, _session() as db:
            q = db.query(AttendanceSuppressedChat)
            if phone:
                try:
                    from app.services.evolution_client import phone_match_variants

                    variants = [v for v in (phone_match_variants(phone) or [phone]) if v]
                except Exception:
                    variants = [phone]
                rows = (
                    q.filter(AttendanceSuppressedChat.phone_e164.in_(variants)).all()
                    if variants
                    else []
                )
            elif jid:
                rows = q.filter(AttendanceSuppressedChat.remote_jid == jid).all()
            else:
                rows = []
            for row in rows:
                db.delete(row)
                removed += 1
    except Exception:
        pass
    return removed


def delete_conversation(conversation_id: str) -> bool:
    """Remove da inbox (soft-delete rápido). Sync da Evolution não recria."""
    conversation_id = normalize_text(conversation_id)
    if not conversation_id:
        return False
    phone = ""
    jid = ""
    instance = ""
    # Sem _lock global: exclusão não pode esperar sync Evolution / upserts.
    db = SessionLocal()
    try:
        row = db.get(AttendanceConversation, conversation_id)
        if not row:
            return False
        if (row.status or "") == STATUS_EXCLUIDO:
            return True
        phone = normalize_text(row.phone_e164 or "")
        jid = normalize_text(row.remote_jid or "")
        instance = resolve_evolution_instance(getattr(row, "evolution_instance", "") or "")
        row.status = STATUS_EXCLUIDO
        row.unread_count = 0
        row.typing = False
        row.last_message_preview = ""
        row.last_message_at = ""
        row.updated_at = _now_iso()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha no soft-delete %s", conversation_id)
        return False
    finally:
        db.close()

    def _after_delete() -> None:
        try:
            suppress_chat(phone_e164=phone, remote_jid=jid, evolution_instance=instance)
        except Exception:
            logger.exception("Falha ao registrar conversa suprimida %s", conversation_id)
        try:
            # Limpa mensagens em background (não bloqueia o clique Excluir)
            with _session() as db2:
                db2.query(AttendanceMessage).filter(
                    AttendanceMessage.conversation_id == conversation_id
                ).delete(synchronize_session=False)
        except Exception:
            logger.exception("Falha ao limpar mensagens da conversa excluída %s", conversation_id)

    threading.Thread(
        target=_after_delete,
        daemon=True,
        name=f"att-del-{conversation_id[:10]}",
    ).start()
    try:
        _notify({"type": "conversation_deleted", "conversation_id": conversation_id})
    except Exception:
        pass
    return True


def list_conversations(
    *,
    search: str = "",
    status: str = "",
    sector_id: int | str | None = None,
    evolution_instance: str = "",
    limit: int = 100,
) -> list[dict]:
    from app.services.evolution_client import is_whatsapp_group_jid

    instance = resolve_evolution_instance(evolution_instance)
    with _session(commit=False) as db:
        q = db.query(AttendanceConversation)
        # Nunca listar conversas excluídas pelo admin
        q = q.filter(AttendanceConversation.status != STATUS_EXCLUIDO)
        if status and status != "todos":
            if status == "abertos":
                q = q.filter(AttendanceConversation.status != STATUS_FINALIZADO)
            else:
                q = q.filter(AttendanceConversation.status == status)
        else:
            # Padrão / "todos": só fila ativa (Novo Lead + Em Atendimento).
            # Finalizados só aparecem quando o filtro "Finalizado" é selecionado.
            q = q.filter(AttendanceConversation.status != STATUS_FINALIZADO)
        if instance:
            q = q.filter(_sql_instance_match(instance))
        if sector_id not in (None, "", "todos", "all"):
            try:
                sid = int(sector_id)
            except (TypeError, ValueError):
                sid = None
            if sid is not None:
                # Inclui sem setor (chegou pelo WhatsApp e ainda não foi direcionado)
                q = q.filter(
                    or_(
                        AttendanceConversation.sector_id == sid,
                        AttendanceConversation.sector_id.is_(None),
                    )
                )
        search_norm = normalize_text(search).lower()
        if search_norm:
            like = f"%{search_norm}%"
            q = q.filter(
                or_(
                    func.lower(AttendanceConversation.contact_name).like(like),
                    AttendanceConversation.phone_e164.like(f"%{search_norm}%"),
                    func.lower(AttendanceConversation.last_message_preview).like(like),
                )
            )
        # Ordena por last_message_at quando preenchido; senão updated_at
        rows = (
            q.order_by(
                func.coalesce(
                    func.nullif(AttendanceConversation.last_message_at, ""),
                    AttendanceConversation.updated_at,
                ).desc(),
                AttendanceConversation.unread_count.desc(),
                AttendanceConversation.updated_at.desc(),
            )
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        conversations = []
        from app.services.evolution_client import is_placeholder_whatsapp_phone

        for row in rows:
            if is_whatsapp_group_jid(row.remote_jid or "") or is_whatsapp_group_jid(row.phone_e164 or ""):
                continue
            if is_placeholder_whatsapp_phone(row.phone_e164 or ""):
                continue
            if _is_unwanted_inbox_contact(row.contact_name or ""):
                continue
            conversations.append(_conversation_to_dict(row))
        return conversations


def upsert_conversation_by_phone(
    phone_e164: str,
    *,
    contact_name: str = "",
    profile_pic_url: str = "",
    sheet_row: int | None = None,
    status: str | None = None,
    remote_jid: str = "",
    evolution_instance: str = "",
    ignore_suppression: bool = False,
) -> dict:
    from app.services.evolution_client import (
        is_placeholder_whatsapp_phone,
        is_valid_br_whatsapp_phone,
        is_whatsapp_group_jid,
        phone_match_variants,
    )

    phone = normalize_text(phone_e164)
    remote_jid = normalize_text(remote_jid)
    contact_name = normalize_text(contact_name)
    instance = resolve_evolution_instance(evolution_instance)
    if is_placeholder_whatsapp_phone(phone):
        return {}
    if is_whatsapp_group_jid(phone) or is_whatsapp_group_jid(remote_jid):
        return {}
    if not is_valid_br_whatsapp_phone(phone) and not (
        remote_jid and ("@lid" in remote_jid.lower() or "@s.whatsapp.net" in remote_jid.lower())
    ):
        # Ex.: 5530214500323 (DDD inválido) — grupo/lixo mapeado como telefone
        return {}
    if _is_unwanted_inbox_contact(contact_name):
        return {}
        return {}
    if not phone:
        raise ValueError("Telefone obrigatório")
    if not ignore_suppression and is_chat_suppressed(
        phone_e164=phone, remote_jid=remote_jid, evolution_instance=instance
    ):
        return {}
    now = _now_iso()
    remote_jid = normalize_text(remote_jid)
    phone_variants = phone_match_variants(phone) or [phone]

    with _lock, _session() as db:
        q = db.query(AttendanceConversation).filter(
            AttendanceConversation.phone_e164.in_(phone_variants)
        )
        if instance:
            q = q.filter(_sql_instance_match(instance))
        existing = q.order_by(AttendanceConversation.updated_at.desc()).first()
        # Mesmo contato pode ter sido aberto só com @lid (sem telefone no 1º payload)
        phone_linked_from_jid = False
        if not existing and remote_jid:
            jq = db.query(AttendanceConversation).filter(
                AttendanceConversation.remote_jid == remote_jid
            )
            if instance:
                jq = jq.filter(_sql_instance_match(instance))
            existing = jq.order_by(AttendanceConversation.updated_at.desc()).first()
            if existing and phone and existing.phone_e164 != phone:
                existing.phone_e164 = phone
                phone_linked_from_jid = True
        if existing and (existing.status or "") == STATUS_EXCLUIDO and not ignore_suppression:
            return {}
        if existing and (existing.status or "") == STATUS_EXCLUIDO and ignore_suppression:
            existing.status = status or STATUS_NOVO_LEAD
            existing.updated_at = now
            conversation_id = existing.id
            result = _conversation_to_dict(existing)
            _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
            return result
        # Mesmo WhatsApp excluído em outra linha / instância vazia — não recria
        if not existing and not ignore_suppression:
            excluido = (
                db.query(AttendanceConversation)
                .filter(
                    AttendanceConversation.phone_e164.in_(phone_variants),
                    AttendanceConversation.status == STATUS_EXCLUIDO,
                )
                .first()
            )
            if excluido:
                return {}
        if existing:
            changed = phone_linked_from_jid
            if contact_name and not (existing.contact_name or "").strip():
                existing.contact_name = normalize_text(contact_name)
                changed = True
            if profile_pic_url:
                existing.profile_pic_url = normalize_text(profile_pic_url)
                changed = True
            if sheet_row and not existing.sheet_row:
                existing.sheet_row = int(sheet_row)
                changed = True
            if status:
                existing.status = status
                changed = True
            if instance and normalize_text(getattr(existing, "evolution_instance", "") or "") != instance:
                # só preenche se vazio (legado) — não troca de linha
                if not normalize_text(getattr(existing, "evolution_instance", "") or ""):
                    existing.evolution_instance = instance
                    changed = True
            if remote_jid and remote_jid != (existing.remote_jid or ""):
                current = normalize_text(existing.remote_jid or "")
                # Nunca trocar @lid por número/@s.whatsapp.net — PN costuma ficar PENDING no Baileys.
                if "@lid" in current.lower() and "@lid" not in remote_jid.lower():
                    pass
                else:
                    existing.remote_jid = remote_jid
                    changed = True
            if changed:
                existing.updated_at = now
            conversation_id = existing.id
            result = _conversation_to_dict(existing)
            if changed:
                _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
            return result

        conversation_id = _new_id("c_")
        row = AttendanceConversation(
            id=conversation_id,
            phone_e164=phone,
            contact_name=normalize_text(contact_name),
            profile_pic_url=normalize_text(profile_pic_url),
            sheet_row=int(sheet_row) if sheet_row else None,
            status=status or STATUS_NOVO_LEAD,
            assignee="",
            ai_mode=AI_MODE_ON,
            tags_json="[]",
            notes="",
            last_message_at="",
            last_message_preview="",
            unread_count=0,
            typing=False,
            remote_jid=remote_jid,
            evolution_instance=instance,
            sector_id=None,
            sector_name="",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        result = _conversation_to_dict(row)

    _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
    return result or {}


def _update_conversation(conversation_id: str, fields: dict) -> None:
    if not fields:
        return
    allowed = {
        "contact_name",
        "profile_pic_url",
        "sheet_row",
        "registration_id",
        "status",
        "assignee",
        "ai_mode",
        "tags_json",
        "notes",
        "last_message_at",
        "last_message_preview",
        "unread_count",
        "typing",
        "updated_at",
        "remote_jid",
        "phone_e164",
        "sector_id",
        "sector_name",
        "evolution_instance",
    }
    with _lock, _session() as db:
        row = db.get(AttendanceConversation, conversation_id)
        if not row:
            return
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "typing":
                setattr(row, key, bool(value))
            elif key in {"sector_id", "sheet_row", "registration_id"}:
                try:
                    setattr(row, key, int(value) if value not in (None, "") else None)
                except (TypeError, ValueError):
                    setattr(row, key, None)
            else:
                setattr(row, key, value)


def update_conversation(conversation_id: str, **fields) -> dict | None:
    payload = dict(fields)
    if "tags" in payload:
        tags = payload.pop("tags") or []
        payload["tags_json"] = json.dumps(
            [normalize_text(t) for t in tags if normalize_text(t)],
            ensure_ascii=False,
        )
    payload["updated_at"] = _now_iso()
    _update_conversation(conversation_id, payload)
    conversation = get_conversation(conversation_id)
    if conversation:
        _notify({"type": "conversation_upsert", "conversation_id": conversation_id})
    return conversation


def set_typing(conversation_id: str, typing: bool) -> None:
    _update_conversation(
        conversation_id, {"typing": bool(typing), "updated_at": _now_iso()}
    )
    _notify({"type": "typing", "conversation_id": conversation_id, "typing": bool(typing)})


def add_message(
    conversation_id: str,
    *,
    direction: str,
    body: str = "",
    msg_type: str = "text",
    media_url: str = "",
    media_mime: str = "",
    media_filename: str = "",
    evolution_id: str = "",
    sender: str = "contact",
    created_at: str | None = None,
    bump_unread: bool = False,
) -> dict | None:
    evolution_id = normalize_text(evolution_id)
    if evolution_id:
        with _session(commit=False) as db:
            existing = (
                db.query(AttendanceMessage)
                .filter(AttendanceMessage.evolution_id == evolution_id)
                .first()
            )
            if existing:
                return _message_to_dict(existing)

    message_id = _new_id("m_")
    created = created_at or _now_iso()
    preview = normalize_text(body)
    if not preview and msg_type != "text":
        preview = f"[{msg_type}]"
    preview = preview[:180]

    with _lock, _session() as db:
        if evolution_id:
            dup = (
                db.query(AttendanceMessage)
                .filter(AttendanceMessage.evolution_id == evolution_id)
                .first()
            )
            if dup:
                return _message_to_dict(dup)

        msg = AttendanceMessage(
            id=message_id,
            conversation_id=conversation_id,
            direction=direction,
            msg_type=msg_type or "text",
            body=body or "",
            media_url=media_url or "",
            media_mime=media_mime or "",
            media_filename=media_filename or "",
            evolution_id=evolution_id,
            sender=sender,
            created_at=created,
        )
        db.add(msg)

        conv = db.get(AttendanceConversation, conversation_id)
        if conv:
            conv.last_message_at = created
            conv.last_message_preview = preview
            conv.updated_at = created
            conv.typing = False
            if bump_unread:
                conv.unread_count = int(conv.unread_count or 0) + 1

        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            # Corrida: outro worker inseriu o mesmo evolution_id
            if evolution_id:
                existing = (
                    db.query(AttendanceMessage)
                    .filter(AttendanceMessage.evolution_id == evolution_id)
                    .first()
                )
                if existing:
                    return _message_to_dict(existing)
            raise

        result = _message_to_dict(msg)

    _notify(
        {
            "type": "message",
            "conversation_id": conversation_id,
            "message_id": message_id,
            "direction": direction,
        }
    )
    return result


def get_message(message_id: str) -> dict | None:
    with _session(commit=False) as db:
        row = db.get(AttendanceMessage, message_id)
        return _message_to_dict(row) if row else None


def update_message_media(
    message_id: str,
    *,
    media_url: str = "",
    media_mime: str = "",
    media_filename: str = "",
) -> dict | None:
    """Atualiza URL/MIME local da mídia (todas as conversas)."""
    message_id = normalize_text(message_id)
    if not message_id:
        return None
    with _lock, _session() as db:
        row = db.get(AttendanceMessage, message_id)
        if not row:
            return None
        if media_url:
            row.media_url = media_url
        if media_mime:
            row.media_mime = media_mime
        if media_filename:
            row.media_filename = media_filename
        db.flush()
        return _message_to_dict(row)


def list_messages_needing_media(*, limit: int = 200) -> list[dict]:
    """Áudios/imagens/vídeos com URL externa ou vazia (todas as conversas)."""
    with _session(commit=False) as db:
        rows = (
            db.query(AttendanceMessage)
            .filter(AttendanceMessage.msg_type.in_(("audio", "image", "video", "document")))
            .order_by(AttendanceMessage.created_at.desc())
            .limit(max(1, min(int(limit or 200), 1000)))
            .all()
        )
        out = []
        for row in rows:
            url = (row.media_url or "").strip()
            if url.startswith("/atendimentos/media/"):
                continue
            if not (row.evolution_id or "").strip():
                continue
            out.append(_message_to_dict(row))
        return out


def list_messages(conversation_id: str, *, limit: int = 200) -> list[dict]:
    with _session(commit=False) as db:
        rows = (
            db.query(AttendanceMessage)
            .filter(AttendanceMessage.conversation_id == conversation_id)
            .order_by(AttendanceMessage.created_at.asc(), AttendanceMessage.id.asc())
            .limit(max(1, min(int(limit or 200), 1000)))
            .all()
        )
        return [_message_to_dict(row) for row in rows]


def mark_conversation_read(conversation_id: str) -> None:
    """Zera unread sem reescrever updated_at se já estava lido (evita loop do poll)."""
    conversation_id = normalize_text(conversation_id)
    if not conversation_id:
        return
    with _session() as db:
        row = db.get(AttendanceConversation, conversation_id)
        if not row:
            return
        if int(row.unread_count or 0) <= 0:
            return
        row.unread_count = 0
        # Não mexe em updated_at: o poll usa max(updated_at) no inbox_token.
    _notify({"type": "conversation_read", "conversation_id": conversation_id})


def count_unread(*, evolution_instance: str = "") -> int:
    instance = resolve_evolution_instance(evolution_instance) if evolution_instance else ""
    with _session(commit=False) as db:
        q = db.query(func.coalesce(func.sum(AttendanceConversation.unread_count), 0)).filter(
            AttendanceConversation.status != STATUS_EXCLUIDO
        )
        if instance:
            q = q.filter(_sql_instance_match(instance))
        total = q.scalar()
        return int(total or 0)


def get_sync_snapshot(conversation_id: str = "") -> dict:
    """Snapshot do inbox — usado pelo poll da UI."""
    conversation_id = normalize_text(conversation_id)
    with _session(commit=False) as db:
        unread = int(
            db.query(func.coalesce(func.sum(AttendanceConversation.unread_count), 0)).scalar()
            or 0
        )
        last_msg = (
            db.query(func.max(AttendanceConversation.last_message_at)).scalar() or ""
        )
        last_upd = db.query(func.max(AttendanceConversation.updated_at)).scalar() or ""
        msg_count = int(db.query(func.count(AttendanceMessage.id)).scalar() or 0)
        msg_max_id = db.query(func.max(AttendanceMessage.id)).scalar() or ""

        conv_token = ""
        if conversation_id:
            crow = (
                db.query(
                    func.count(AttendanceMessage.id),
                    func.max(AttendanceMessage.created_at),
                    func.max(AttendanceMessage.id),
                )
                .filter(AttendanceMessage.conversation_id == conversation_id)
                .one()
            )
            typing_row = db.get(AttendanceConversation, conversation_id)
            typing = 1 if typing_row and typing_row.typing else 0
            conv_token = f"{crow[0]}|{crow[1] or ''}|{crow[2] or ''}|{typing}"

    inbox_token = f"{unread}|{last_msg}|{last_upd}|{msg_count}|{msg_max_id}"
    return {
        "unread": unread,
        "inbox_token": inbox_token,
        "conversation_id": conversation_id or None,
        "conversation_token": conv_token or None,
    }
