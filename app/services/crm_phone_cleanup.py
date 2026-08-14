"""Corrige 9º dígito de WhatsApp (Evolution) e remove cadastros duplicados, sem apagar filial."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import AttendanceConversation, AttendanceMessage, CrmRegistration

from app.services.legacy_core import (
    canonicalize_br_mobile_national,
    format_br_whatsapp_display,
    invalidate_sheet_cache,
    normalize_cnpj_for_duplicate,
    normalize_digits,
    normalize_phone_for_duplicate,
    normalize_text,
)
from app.services.crm_registrations_storage import DEFAULT_TENANT_ID, invalidate_registrations_cache

logger = logging.getLogger(__name__)

_PHONE_FIELDS = (
    "telefone_b2b",
    "telefone_socio_1",
    "telefone_alternativo",
)


def needs_mobile_ninth_digit(value: str) -> bool:
    digits = normalize_phone_for_duplicate(value)
    canon = canonicalize_br_mobile_national(value)
    return bool(digits) and bool(canon) and digits != canon and len(canon) == 11


def is_evolution_origin(row: dict[str, Any]) -> bool:
    if row.get("from_attendance"):
        return True
    obs = normalize_text(row.get("observacoes")).lower()
    if "atendimento whatsapp" in obs:
        return True
    name = normalize_text(row.get("empresa")).lower()
    return name.startswith("lead whatsapp") or name.startswith("whatsapp ")


def is_protected_filial(row: dict[str, Any]) -> bool:
    if row.get("is_filial"):
        return True
    try:
        if int(row.get("empresa_matriz_sheet_row") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    empresa = normalize_text(row.get("empresa")).lower()
    return "filial" in empresa


def completeness_score(row: dict[str, Any]) -> float:
    """Quanto maior, mais vale manter. Filial não entra na disputa de exclusão."""
    score = 0.0
    tipo = normalize_text(row.get("cadastro_tipo")).lower()
    if tipo == "empresa":
        score += 1000
    if normalize_cnpj_for_duplicate(row.get("cnpj") or ""):
        score += 250
    if normalize_text(row.get("endereco")):
        score += 80
    if normalize_text(row.get("email_empresa")):
        score += 40
    if normalize_text(row.get("municipio")):
        score += 20
    empresa = normalize_text(row.get("empresa"))
    if empresa and not empresa.lower().startswith("lead whatsapp"):
        score += 90
    if normalize_text(row.get("nome_contato")):
        score += 40
    if row.get("cadastro_ativo", True):
        score += 15
    filled = sum(
        1
        for key in ("socio_1", "cep", "bairro", "site", "observacoes")
        if normalize_text(row.get(key))
    )
    score += filled * 8
    try:
        sheet_row = int(row.get("sheet_row") or 0)
    except (TypeError, ValueError):
        sheet_row = 0
    score -= sheet_row * 0.001
    return score


def connected_duplicate_groups(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Agrupa cadastros que compartilham telefone (já com 9) ou CNPJ, inclusive em cadeia."""
    if len(rows) < 2:
        return []
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    by_phone: dict[str, list[int]] = defaultdict(list)
    by_cnpj: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        phone = canonicalize_br_mobile_national(row.get("telefone_b2b") or "")
        if len(phone) >= 10:
            by_phone[phone].append(index)
        cnpj = normalize_cnpj_for_duplicate(row.get("cnpj") or "")
        if cnpj:
            by_cnpj[cnpj].append(index)

    for indexes in by_phone.values():
        for extra in indexes[1:]:
            union(indexes[0], extra)
    for indexes in by_cnpj.values():
        for extra in indexes[1:]:
            union(indexes[0], extra)

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[find(index)].append(row)
    return [members for members in grouped.values() if len(members) >= 2]


def pick_duplicate_deletions(group: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Quem manter e quem apagar. Filiais nunca saem. Precisa sobrar pelo menos 1 cadastro."""
    if len(group) < 2:
        return None, []
    protected = [row for row in group if is_protected_filial(row)]
    contest = [row for row in group if not is_protected_filial(row)]
    if len(contest) <= 1:
        return (contest[0] if contest else (protected[0] if protected else None)), []
    ranked = sorted(contest, key=completeness_score, reverse=True)
    return ranked[0], ranked[1:]


def canonical_e164(phone: str) -> str:
    national = canonicalize_br_mobile_national(phone)
    if not national:
        return normalize_digits(phone)
    if national.startswith("55"):
        return national
    return "55" + national


def _row_to_dict(row: CrmRegistration, *, from_attendance: bool = False) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "sheet_row": int(row.sheet_row) if row.sheet_row else None,
        "empresa": normalize_text(row.empresa),
        "cadastro_tipo": normalize_text(row.cadastro_tipo) or "lead",
        "cnpj": normalize_text(row.cnpj),
        "telefone_b2b": normalize_text(row.telefone_b2b),
        "telefone_socio_1": normalize_text(row.telefone_socio_1),
        "telefone_alternativo": normalize_text(row.telefone_alternativo),
        "endereco": normalize_text(row.endereco),
        "email_empresa": normalize_text(row.email_empresa),
        "municipio": normalize_text(row.municipio),
        "nome_contato": normalize_text(getattr(row, "nome_contato", "") or ""),
        "socio_1": normalize_text(row.socio_1),
        "cep": normalize_text(row.cep),
        "bairro": normalize_text(row.bairro),
        "site": normalize_text(row.site),
        "observacoes": normalize_text(row.observacoes),
        "cadastro_ativo": bool(row.cadastro_ativo),
        "is_filial": bool(getattr(row, "is_filial", False)),
        "empresa_matriz_sheet_row": getattr(row, "empresa_matriz_sheet_row", None),
        "from_attendance": from_attendance,
    }


def _fix_phone_fields(row: CrmRegistration) -> list[str]:
    changed: list[str] = []
    for field in _PHONE_FIELDS:
        current = normalize_text(getattr(row, field, "") or "")
        if not needs_mobile_ninth_digit(current):
            continue
        new_value = format_br_whatsapp_display(current)
        if new_value and new_value != current:
            setattr(row, field, new_value)
            changed.append(field)
    return changed


def _evolution_markers(db: Session) -> tuple[set[int], set[int]]:
    sheet_rows: set[int] = set()
    registration_ids: set[int] = set()
    rows = db.query(
        AttendanceConversation.sheet_row,
        AttendanceConversation.registration_id,
    ).all()
    for sheet_row, registration_id in rows:
        try:
            if sheet_row:
                sheet_rows.add(int(sheet_row))
        except (TypeError, ValueError):
            pass
        try:
            if registration_id:
                registration_ids.add(int(registration_id))
        except (TypeError, ValueError):
            pass
    return sheet_rows, registration_ids


def _fix_conversation_phones(db: Session) -> int:
    changed = 0
    conversations = db.query(AttendanceConversation).all()
    for conv in conversations:
        phone = normalize_text(conv.phone_e164 or "")
        if not phone or not needs_mobile_ninth_digit(phone):
            continue
        conv.phone_e164 = canonical_e164(phone)
        changed += 1
    return changed


def _merge_duplicate_conversations(db: Session) -> int:
    """Junta chats do mesmo WhatsApp (com/sem 9) na mesma linha Evolution."""
    grouped: dict[tuple[str, str], list[AttendanceConversation]] = defaultdict(list)
    for conv in db.query(AttendanceConversation).all():
        phone = canonical_e164(conv.phone_e164 or "")
        if len(normalize_digits(phone)) < 12:
            continue
        instance = normalize_text(conv.evolution_instance or "").lower()
        grouped[(instance, phone)].append(conv)
    merged = 0
    from sqlalchemy import func

    counts = {
        conversation_id: int(total)
        for conversation_id, total in (
            db.query(AttendanceMessage.conversation_id, func.count(AttendanceMessage.id))
            .group_by(AttendanceMessage.conversation_id)
            .all()
        )
    }
    for items in grouped.values():
        if len(items) < 2:
            continue
        items.sort(
            key=lambda row: (
                counts.get(row.id, 0),
                normalize_text(row.last_message_at or ""),
            ),
            reverse=True,
        )
        keeper = items[0]
        for loser in items[1:]:
            db.query(AttendanceMessage).filter(
                AttendanceMessage.conversation_id == loser.id
            ).update({"conversation_id": keeper.id}, synchronize_session=False)
            if not keeper.sheet_row and loser.sheet_row:
                keeper.sheet_row = loser.sheet_row
            if not keeper.registration_id and loser.registration_id:
                keeper.registration_id = loser.registration_id
            if not normalize_text(keeper.contact_name) and normalize_text(loser.contact_name):
                keeper.contact_name = loser.contact_name
            db.delete(loser)
            merged += 1
    return merged


def _relink_conversations(db: Session, deleted_sheet_rows: set[int], keeper_by_deleted: dict[int, int]) -> None:
    if not deleted_sheet_rows:
        return
    conversations = (
        db.query(AttendanceConversation)
        .filter(AttendanceConversation.sheet_row.in_(list(deleted_sheet_rows)))
        .all()
    )
    for conv in conversations:
        old = int(conv.sheet_row or 0)
        new_row = keeper_by_deleted.get(old)
        if not new_row:
            continue
        keeper = (
            db.query(CrmRegistration)
            .filter(CrmRegistration.sheet_row == int(new_row))
            .first()
        )
        conv.sheet_row = int(new_row)
        if keeper:
            conv.registration_id = int(keeper.id)


def cleanup_evolution_phones_and_duplicates(*, apply: bool = False) -> dict[str, Any]:
    """Corrige 9º dígito (origem Evolution) e apaga duplicados. Filial nunca é excluída."""
    db = SessionLocal()
    phones_fixed: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    unique_skipped: list[str] = []
    conv_phones = 0
    conv_merged = 0
    try:
        attendance_rows, attendance_ids = _evolution_markers(db)
        registrations = (
            db.query(CrmRegistration)
            .filter(CrmRegistration.tenant_id == DEFAULT_TENANT_ID)
            .all()
        )
        by_id = {int(row.id): row for row in registrations}
        snapshots: list[dict[str, Any]] = []
        for row in registrations:
            from_att = int(row.sheet_row or 0) in attendance_rows or int(row.id) in attendance_ids
            data = _row_to_dict(row, from_attendance=from_att)
            snapshots.append(data)
            if not is_evolution_origin(data):
                continue
            if apply:
                changed_fields = _fix_phone_fields(row)
            else:
                changed_fields = [
                    field
                    for field in _PHONE_FIELDS
                    if needs_mobile_ninth_digit(data.get(field) or "")
                ]
            if changed_fields:
                phones_fixed.append({
                    "sheet_row": data["sheet_row"],
                    "empresa": data["empresa"],
                    "fields": changed_fields,
                    "antes": data["telefone_b2b"],
                    "depois": format_br_whatsapp_display(data["telefone_b2b"]),
                })

        if apply:
            conv_phones = _fix_conversation_phones(db)
            conv_merged = _merge_duplicate_conversations(db)
            db.flush()
            live = (
                db.query(CrmRegistration)
                .filter(CrmRegistration.tenant_id == DEFAULT_TENANT_ID)
                .all()
            )
            live_dicts = [
                _row_to_dict(
                    row,
                    from_attendance=int(row.sheet_row or 0) in attendance_rows
                    or int(row.id) in attendance_ids,
                )
                for row in live
            ]
        else:
            live_dicts = snapshots

        skipped_filial: list[str] = []
        to_delete_ids: dict[int, dict[str, Any]] = {}
        keeper_by_deleted: dict[int, int] = {}
        for members in connected_duplicate_groups(live_dicts):
            for protected in members:
                if is_protected_filial(protected):
                    skipped_filial.append(protected.get("empresa") or str(protected.get("sheet_row")))
            keeper, doomed = pick_duplicate_deletions(members)
            for item in doomed:
                rid = int(item["id"])
                if rid in to_delete_ids:
                    continue
                to_delete_ids[rid] = item
                if keeper and keeper.get("sheet_row") and item.get("sheet_row"):
                    keeper_by_deleted[int(item["sheet_row"])] = int(keeper["sheet_row"])

        unique_skipped = sorted(set(skipped_filial))
        deleted = list(to_delete_ids.values())

        if apply and to_delete_ids:
            deleted_sheet = {int(item["sheet_row"]) for item in deleted if item.get("sheet_row")}
            _relink_conversations(db, deleted_sheet, keeper_by_deleted)
            db.commit()
            from app.services.registration import delete_company_registration

            for item in deleted:
                sheet_row = item.get("sheet_row")
                if not sheet_row:
                    row = by_id.get(int(item["id"]))
                    if row is not None:
                        try:
                            db2 = SessionLocal()
                            try:
                                live_row = db2.query(CrmRegistration).filter(CrmRegistration.id == int(item["id"])).first()
                                if live_row:
                                    db2.delete(live_row)
                                    db2.commit()
                            finally:
                                db2.close()
                        except Exception:
                            logger.exception("Falha ao excluir duplicado id=%s", item["id"])
                    continue
                try:
                    delete_company_registration(DEFAULT_TENANT_ID, int(sheet_row))
                except Exception:
                    logger.exception("Falha ao excluir duplicado sheet_row=%s", sheet_row)
            invalidate_registrations_cache()
            try:
                invalidate_sheet_cache()
            except Exception:
                pass
        elif apply:
            db.commit()
            invalidate_registrations_cache()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if apply:
        try:
            from app.services.crm_registrations_storage import _schedule_mirror_registration

            for item in phones_fixed:
                if item.get("sheet_row"):
                    _schedule_mirror_registration(int(item["sheet_row"]))
        except Exception:
            logger.exception("Falha ao espelhar telefones na Folha1")

    return {
        "apply": apply,
        "phones_fixed": len(phones_fixed),
        "phones": phones_fixed[:80],
        "duplicates_deleted": len(deleted),
        "duplicates": [
            {
                "sheet_row": item.get("sheet_row"),
                "empresa": item.get("empresa"),
                "tipo": item.get("cadastro_tipo"),
                "telefone": item.get("telefone_b2b"),
            }
            for item in deleted[:80]
        ],
        "filiais_preserved": unique_skipped,
        "conversations_phones_fixed": conv_phones,
        "conversations_merged": conv_merged,
    }
