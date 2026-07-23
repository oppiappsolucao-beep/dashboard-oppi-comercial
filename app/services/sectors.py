"""Setores comerciais com responsáveis (usuários) vinculados."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.services.account_users import load_account_users
from app.services.legacy_core import normalize_text
from database.connection import SessionLocal
from database.models import CrmSector


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None).isoformat(timespec="seconds")


def _parse_user_ids(raw) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        text = normalize_text(raw)
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        values = parsed if isinstance(parsed, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        uid = normalize_text(item)
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def _users_by_id() -> dict[str, dict]:
    return {normalize_text(u.get("id")): u for u in load_account_users() if u.get("id")}


def _sector_to_dict(row: CrmSector, users_map: dict[str, dict] | None = None) -> dict:
    users_map = users_map or _users_by_id()
    user_ids = _parse_user_ids(row.user_ids_json)
    users = []
    for uid in user_ids:
        user = users_map.get(uid)
        if not user:
            continue
        users.append(
            {
                "id": uid,
                "name": user.get("name") or user.get("username") or uid,
                "username": user.get("username") or "",
                "active": bool(user.get("active", True)),
            }
        )
    return {
        "id": row.id,
        "name": row.name,
        "active": bool(row.active),
        "user_ids": user_ids,
        "users": users,
        "users_label": ", ".join(u["name"] for u in users) or "—",
        "users_names_joined": "||".join(u["name"] for u in users if u.get("name")),
        "status_label": "Ativo" if row.active else "Inativo",
        "status_class": "active" if row.active else "inactive",
    }


def list_sectors(*, active_only: bool = True) -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(CrmSector)
        if active_only:
            q = q.filter(CrmSector.active.is_(True))
        rows = q.order_by(CrmSector.name.asc()).all()
        users_map = _users_by_id()
        return [_sector_to_dict(row, users_map) for row in rows]
    finally:
        db.close()


def get_sector(sector_id: int | str | None) -> dict | None:
    try:
        sid = int(sector_id)
    except (TypeError, ValueError):
        return None
    db = SessionLocal()
    try:
        row = db.get(CrmSector, sid)
        if not row:
            return None
        return _sector_to_dict(row)
    finally:
        db.close()


def list_sector_options() -> list[dict]:
    return [{"id": s["id"], "name": s["name"]} for s in list_sectors(active_only=True)]


def users_for_sector(sector_id: int | str | None) -> list[dict]:
    sector = get_sector(sector_id)
    if not sector:
        return []
    return [u for u in sector.get("users") or [] if u.get("active", True)]


def responsible_options_for_sector(sector_id: int | str | None = None) -> list[str]:
    """Nomes de responsáveis (display) para selects."""
    if sector_id:
        users = users_for_sector(sector_id)
        names = [normalize_text(u.get("name")) for u in users if normalize_text(u.get("name"))]
        if names:
            return names
    # fallback: todos usuários ativos
    names = []
    for user in load_account_users():
        if not user.get("active", True):
            continue
        name = normalize_text(user.get("name") or user.get("username"))
        if name:
            names.append(name)
    return sorted(set(names), key=str.lower)


def add_sector(name: str, user_ids: list[str] | None = None) -> dict:
    clean = normalize_text(name)
    if not clean:
        raise ValueError("Informe o nome do setor.")
    ids = _parse_user_ids(user_ids or [])
    now = _now_iso()
    db = SessionLocal()
    try:
        row = CrmSector(
            name=clean,
            active=True,
            user_ids_json=json.dumps(ids, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise ValueError("Já existe um setor com este nome.") from error
        db.refresh(row)
        return _sector_to_dict(row)
    finally:
        db.close()


def update_sector(
    sector_id: int | str,
    *,
    name: str | None = None,
    user_ids: list[str] | None = None,
    active: bool | None = None,
) -> dict:
    try:
        sid = int(sector_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Setor inválido.") from error

    db = SessionLocal()
    try:
        row = db.get(CrmSector, sid)
        if not row:
            raise ValueError("Setor não encontrado.")
        if name is not None:
            clean = normalize_text(name)
            if not clean:
                raise ValueError("Informe o nome do setor.")
            row.name = clean
        if user_ids is not None:
            row.user_ids_json = json.dumps(_parse_user_ids(user_ids), ensure_ascii=False)
        if active is not None:
            row.active = bool(active)
        row.updated_at = _now_iso()
        try:
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise ValueError("Já existe um setor com este nome.") from error
        db.refresh(row)
        return _sector_to_dict(row)
    finally:
        db.close()


def delete_sector(sector_id: int | str) -> None:
    try:
        sid = int(sector_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Setor inválido.") from error
    db = SessionLocal()
    try:
        row = db.get(CrmSector, sid)
        if not row:
            raise ValueError("Setor não encontrado.")
        db.delete(row)
        db.commit()
    finally:
        db.close()
