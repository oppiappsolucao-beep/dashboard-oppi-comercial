"""Config/users/metas/atividades/propostas — Postgres SoT + espelho Sheets/JSON."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from database.connection import SessionLocal
from database.models import (
    CrmAccountUser,
    CrmActivity,
    CrmAppSetting,
    CrmMonthlyGoal,
    CrmProposal,
)

from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)
_TZ = ZoneInfo("America/Sao_Paulo")


def _now_iso() -> str:
    return datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def _json_loads(raw: str | None, default):
    try:
        data = json.loads(raw or "")
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def _json_dumps(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


# ----- App settings -----

def load_settings_pg() -> dict:
    db = SessionLocal()
    try:
        rows = db.query(CrmAppSetting).all()
        result = {}
        for row in rows:
            key = normalize_text(row.key)
            if key == "commercial_services":
                result[key] = _json_loads(row.value, [])
            else:
                result[key] = row.value or ""
        return result
    finally:
        db.close()


def save_settings_pg(values: dict) -> None:
    db = SessionLocal()
    try:
        for key, value in (values or {}).items():
            raw = value
            if key == "commercial_services" or not isinstance(raw, str):
                raw = json.dumps(value, ensure_ascii=False, default=str)
            existing = db.get(CrmAppSetting, str(key))
            if existing is None:
                existing = CrmAppSetting(key=str(key))
                db.add(existing)
            existing.value = str(raw)
            existing.updated_at = _now_iso()
        db.commit()
    finally:
        db.close()
    _schedule_mirror_settings(values)


def _schedule_mirror_settings(values: dict) -> None:
    def _run() -> None:
        try:
            from app.services import app_settings as mod

            current = load_settings_pg()
            current.update(values or {})
            path = mod._settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            mod._save_to_sheet(current)
        except Exception:
            logger.exception("Falha espelho Configuracoes")

    threading.Thread(target=_run, daemon=True).start()


# ----- Account users -----

def load_users_pg() -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.query(CrmAccountUser).order_by(CrmAccountUser.name.asc()).all()
        result = []
        for row in rows:
            result.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "email": row.email,
                    "username": row.username,
                    "password_hash": row.password_hash,
                    "role": row.role,
                    "active": bool(row.active),
                    "department_id": row.department_id or "",
                    "department_name": row.department_name or "",
                    "last_access": row.last_access or "",
                    "created_at": row.created_at or "",
                    "updated_at": row.updated_at or "",
                }
            )
        return result
    finally:
        db.close()


def persist_users_pg(users: list[dict]) -> None:
    db = SessionLocal()
    try:
        existing_ids = {u.id for u in db.query(CrmAccountUser).all()}
        keep_ids = set()
        for user in users:
            user_id = normalize_text(user.get("id"))
            if not user_id:
                continue
            keep_ids.add(user_id)
            row = db.get(CrmAccountUser, user_id)
            if row is None:
                row = CrmAccountUser(id=user_id)
                db.add(row)
            row.name = normalize_text(user.get("name"))
            row.email = normalize_text(user.get("email"))
            row.username = normalize_text(user.get("username")).lower()
            row.password_hash = normalize_text(user.get("password_hash"))
            row.role = normalize_text(user.get("role")) or "Vendedor"
            row.active = bool(user.get("active", True))
            row.department_id = normalize_text(user.get("department_id"))
            row.department_name = normalize_text(user.get("department_name"))
            row.last_access = normalize_text(user.get("last_access"))
            row.created_at = normalize_text(user.get("created_at")) or _now_iso()
            row.updated_at = normalize_text(user.get("updated_at")) or _now_iso()
        for stale in existing_ids - keep_ids:
            obj = db.get(CrmAccountUser, stale)
            if obj:
                db.delete(obj)
        db.commit()
    finally:
        db.close()
    _schedule_mirror_users(users)


def _schedule_mirror_users(users: list[dict]) -> None:
    def _run() -> None:
        try:
            from app.services.account_users import _save_to_file, _save_to_sheet

            _save_to_file(users)
            _save_to_sheet(users)
        except Exception:
            logger.exception("Falha espelho Usuarios")

    threading.Thread(target=_run, daemon=True).start()


# ----- Monthly goals -----

def load_goals_pg() -> dict[str, dict]:
    db = SessionLocal()
    try:
        rows = db.query(CrmMonthlyGoal).all()
        result: dict[str, dict] = {}
        for row in rows:
            key = f"{int(row.reference_year)}-{int(row.reference_month):02d}|{row.seller}"
            result[key] = {
                "amount": float(row.amount or 0),
                "commission_rate": float(row.commission_rate or 8.0),
            }
        return result
    finally:
        db.close()


def persist_goals_pg(goals: dict[str, dict]) -> None:
    db = SessionLocal()
    try:
        existing = {
            (r.reference_year, r.reference_month, r.seller): r
            for r in db.query(CrmMonthlyGoal).all()
        }
        keep = set()
        for key, value in (goals or {}).items():
            try:
                period, seller = key.split("|", 1)
                year_text, month_text = period.split("-", 1)
                year, month = int(year_text), int(month_text)
            except Exception:
                continue
            amount = float((value or {}).get("amount") or 0) if isinstance(value, dict) else float(value or 0)
            rate = float((value or {}).get("commission_rate") or 8.0) if isinstance(value, dict) else 8.0
            tuple_key = (year, month, seller)
            keep.add(tuple_key)
            row = existing.get(tuple_key)
            if row is None:
                row = CrmMonthlyGoal(
                    reference_year=year,
                    reference_month=month,
                    seller=seller,
                )
                db.add(row)
            row.amount = amount
            row.commission_rate = rate
            row.updated_at = _now_iso()
        for stale, row in existing.items():
            if stale not in keep:
                db.delete(row)
        db.commit()
    finally:
        db.close()
    _schedule_mirror_goals(goals)


def _schedule_mirror_goals(goals: dict) -> None:
    def _run() -> None:
        try:
            from app.services.monthly_goals import _save_to_file, _save_to_sheet

            _save_to_file(goals)
            _save_to_sheet(goals)
        except Exception:
            logger.exception("Falha espelho Metas")

    threading.Thread(target=_run, daemon=True).start()


# ----- Activities -----

def list_activities_pg(
    tenant_id: str | None = None,
    *,
    include_deleted: bool = False,
) -> list[dict]:
    tenant = normalize_text(tenant_id) or "default"
    db = SessionLocal()
    try:
        q = db.query(CrmActivity).filter(CrmActivity.tenant_id == tenant)
        if not include_deleted:
            q = q.filter(CrmActivity.deleted.is_(False))
        rows = q.all()
        result = []
        for row in rows:
            payload = _json_loads(row.payload_json, {})
            payload["id"] = row.id
            payload["tenant_id"] = row.tenant_id
            payload["sheet_row"] = row.sheet_row or 0
            if getattr(row, "registration_id", None):
                payload["registration_id"] = int(row.registration_id)
            payload["deleted"] = bool(row.deleted)
            result.append(payload)
        return result
    finally:
        db.close()


def get_activity_pg(tenant_id: str | None, activity_id: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(CrmActivity, normalize_text(activity_id))
        if not row:
            return None
        tenant = normalize_text(tenant_id) or "default"
        if row.tenant_id != tenant:
            return None
        payload = _json_loads(row.payload_json, {})
        payload["id"] = row.id
        payload["deleted"] = bool(row.deleted)
        return payload
    finally:
        db.close()


def save_activity_pg(tenant_id: str | None, activity_id: str | None, payload: dict) -> dict:
    import uuid

    tenant = normalize_text(tenant_id) or "default"
    aid = normalize_text(activity_id) or f"act_{uuid.uuid4().hex[:12]}"
    record = dict(payload or {})
    record["id"] = aid
    record["tenant_id"] = tenant
    record["updated_at"] = _now_iso()
    record.setdefault("created_at", _now_iso())
    sheet_row = int(record.get("sheet_row") or 0) or None
    registration_id = record.get("registration_id")
    try:
        registration_id = int(registration_id) if registration_id not in (None, "") else None
    except (TypeError, ValueError):
        registration_id = None
    if registration_id is None and sheet_row:
        try:
            from app.services.crm_registrations_storage import get_registration_by_sheet_row

            reg = get_registration_by_sheet_row(int(sheet_row), tenant_id=tenant)
            if reg:
                registration_id = int(reg.id)
                record["registration_id"] = registration_id
        except Exception:
            registration_id = None
    db = SessionLocal()
    try:
        row = db.get(CrmActivity, aid)
        if row is None:
            row = CrmActivity(id=aid, created_at=record["created_at"])
            db.add(row)
        row.tenant_id = tenant
        row.sheet_row = sheet_row
        row.registration_id = registration_id
        row.payload_json = _json_dumps(record)
        row.deleted = bool(record.get("deleted"))
        row.updated_at = record["updated_at"]
        db.commit()
    finally:
        db.close()
    _schedule_mirror_activities(tenant)
    return record


def _schedule_mirror_activities(tenant_id: str) -> None:
    def _run() -> None:
        try:
            from app.services.activities_storage import _persist_store, _empty_store

            items = list_activities_pg(tenant_id, include_deleted=True)
            store = _empty_store()
            bucket = {"activities": {item["id"]: item for item in items}}
            store[tenant_id] = bucket
            _persist_store(store)
        except Exception:
            logger.exception("Falha espelho Atividades")

    threading.Thread(target=_run, daemon=True).start()


# ----- Proposals history -----

def load_proposals_pg(company: str | None = None, limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.query(CrmProposal).order_by(CrmProposal.created_at.desc()).all()
        result = []
        key = normalize_text(company).lower() if company else ""
        for row in rows:
            payload = _json_loads(row.payload_json, {})
            payload.setdefault("id", row.id)
            payload.setdefault("cliente", row.cliente)
            payload.setdefault("cnpj_cpf", row.cnpj_cpf)
            payload.setdefault("created_at", row.created_at)
            if key and normalize_text(payload.get("cliente")).lower() != key:
                continue
            result.append(payload)
            if len(result) >= max(1, int(limit)):
                break
        return result
    finally:
        db.close()


def save_proposal_pg(entry: dict) -> dict:
    import uuid

    record = dict(entry or {})
    entry_id = normalize_text(record.get("id")) or str(uuid.uuid4())
    record["id"] = entry_id
    record.setdefault("created_at", _now_iso())
    db = SessionLocal()
    try:
        row = db.get(CrmProposal, entry_id)
        if row is None:
            row = CrmProposal(id=entry_id)
            db.add(row)
        row.cliente = normalize_text(record.get("cliente"))
        row.cnpj_cpf = normalize_text(record.get("cnpj_cpf"))
        row.payload_json = _json_dumps(record)
        row.created_at = normalize_text(record.get("created_at")) or _now_iso()
        db.commit()
    finally:
        db.close()
    _schedule_mirror_proposals()
    return record


def delete_proposal_pg(entry_id: str) -> bool:
    entry_id = normalize_text(entry_id)
    if not entry_id:
        return False
    db = SessionLocal()
    try:
        row = db.get(CrmProposal, entry_id)
        if not row:
            return False
        db.delete(row)
        db.commit()
    finally:
        db.close()
    _schedule_mirror_proposals()
    return True


def get_proposal_pg(entry_id: str) -> dict | None:
    entry_id = normalize_text(entry_id)
    if not entry_id:
        return None
    db = SessionLocal()
    try:
        row = db.get(CrmProposal, entry_id)
        if not row:
            return None
        payload = _json_loads(row.payload_json, {})
        payload.setdefault("id", row.id)
        return payload
    finally:
        db.close()


def _schedule_mirror_proposals() -> None:
    def _run() -> None:
        try:
            from app.services.proposal_history import _history_path

            rows = load_proposals_pg(limit=5000)
            path = _history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Falha espelho proposal_history.json")

    threading.Thread(target=_run, daemon=True).start()
