"""Limpeza pontual: leads recentes + conversas WhatsApp (mantém empresas)."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from database.connection import SessionLocal
from database.models import (
    AttendanceConversation,
    AttendanceMessage,
    AttendanceSuppressedChat,
    CrmActivity,
    CrmRegistration,
)

from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)
_TZ = ZoneInfo("America/Sao_Paulo")


def _parse_any_date(value: str) -> date | None:
    text = normalize_text(value)
    if not text:
        return None
    # ISO: 2026-08-05T17:59:00 or 2026-08-05
    try:
        return datetime.fromisoformat(text.replace("Z", "")[:19]).date()
    except ValueError:
        pass
    # BR: 05/08/2026
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _registration_entered_on(row: CrmRegistration) -> date | None:
    for raw in (row.created_at, row.data_chamado, row.updated_at):
        parsed = _parse_any_date(raw or "")
        if parsed:
            return parsed
    return None


def purge_recent_leads_and_all_conversations(*, days: int = 30) -> dict:
    """Apaga leads dos últimos N dias + todas as conversas WhatsApp.

    NÃO apaga cadastros com cadastro_tipo=empresa.
    """
    result = {
        "leads_deleted": 0,
        "lead_sheet_rows": [],
        "activities_deleted": 0,
        "conversations_deleted": 0,
        "messages_deleted": 0,
        "suppressed_cleared": 0,
    }
    cutoff = datetime.now(_TZ).replace(tzinfo=None).date() - timedelta(days=max(1, int(days)))
    lead_rows: list[int] = []

    db = SessionLocal()
    try:
        regs = (
            db.query(CrmRegistration)
            .filter(CrmRegistration.cadastro_tipo == "lead")
            .all()
        )
        to_delete: list[CrmRegistration] = []
        for row in regs:
            entered = _registration_entered_on(row)
            if entered is None:
                continue
            if entered >= cutoff:
                to_delete.append(row)
                if row.sheet_row:
                    lead_rows.append(int(row.sheet_row))

        for row in to_delete:
            db.delete(row)
        result["leads_deleted"] = len(to_delete)
        result["lead_sheet_rows"] = sorted(set(lead_rows))

        if lead_rows:
            act_q = db.query(CrmActivity).filter(CrmActivity.sheet_row.in_(lead_rows))
            result["activities_deleted"] = int(act_q.delete(synchronize_session=False) or 0)

        # Todas as conversas de atendimento (começar do zero)
        result["messages_deleted"] = int(
            db.query(AttendanceMessage).delete(synchronize_session=False) or 0
        )
        result["conversations_deleted"] = int(
            db.query(AttendanceConversation).delete(synchronize_session=False) or 0
        )
        result["suppressed_cleared"] = int(
            db.query(AttendanceSuppressedChat).delete(synchronize_session=False) or 0
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Falha na limpeza de leads/conversas")
        raise
    finally:
        db.close()

    try:
        from app.services.crm_registrations_storage import (
            invalidate_crm_postgres_ready_cache,
            invalidate_registrations_cache,
        )

        invalidate_registrations_cache()
        invalidate_crm_postgres_ready_cache()
    except Exception:
        pass
    try:
        from app.services.activities_storage import invalidate_activities_cache

        invalidate_activities_cache()
    except Exception:
        pass
    try:
        from app.dependencies import invalidate_merged_prepared_cache

        invalidate_merged_prepared_cache()
    except Exception:
        pass

    return result
