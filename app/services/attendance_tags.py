"""Tags de atendimento persistidas em DATABASE_URL."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.services.legacy_core import normalize_text
from database.connection import SessionLocal
from database.models import CrmAttendanceTag

DEFAULT_TAGS = [
    "Novo contato",
    "Parceiros",
    "Atendimento interno",
]

SYSTEM_TAG_NAMES = {name.strip().lower() for name in DEFAULT_TAGS}

# Paleta fixa para pills (sem coluna de cor no banco)
_TAG_COLORS: list[tuple[str, str]] = [
    ("#ECFDF5", "#047857"),
    ("#EFF6FF", "#1D4ED8"),
    ("#FFF7ED", "#C2410C"),
    ("#FDF2F8", "#BE185D"),
    ("#F5F3FF", "#6D28D9"),
    ("#ECFEFF", "#0E7490"),
    ("#FEF3C7", "#B45309"),
    ("#F0FDF4", "#15803D"),
]


def tag_color_pair(name: str) -> tuple[str, str]:
    key = normalize_text(name).lower() or "x"
    idx = sum(ord(c) for c in key) % len(_TAG_COLORS)
    return _TAG_COLORS[idx]


def tag_style_map(names: list[str] | None = None) -> dict[str, dict[str, str]]:
    """Mapa nome → {bg, fg} para templates."""
    out: dict[str, dict[str, str]] = {}
    for name in names or list_attendance_tag_options():
        clean = normalize_text(name)
        if not clean:
            continue
        bg, fg = tag_color_pair(clean)
        out[clean] = {"bg": bg, "fg": fg}
    return out


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None).isoformat(timespec="seconds")


def _row_to_dict(row: CrmAttendanceTag) -> dict:
    name = row.name or ""
    bg, fg = tag_color_pair(name)
    return {
        "id": row.id,
        "name": name,
        "is_system": bool(row.is_system) or normalize_text(name).lower() in SYSTEM_TAG_NAMES,
        "active": bool(row.active),
        "sort_order": int(row.sort_order or 0),
        "status_label": "Ativo" if row.active else "Inativo",
        "status_class": "active" if row.active else "inactive",
        "preview_bg": bg,
        "preview_fg": fg,
    }


def ensure_default_attendance_tags() -> None:
    db = SessionLocal()
    try:
        existing = {
            (row.name or "").strip().lower()
            for row in db.query(CrmAttendanceTag).all()
        }
        created = False
        for index, name in enumerate(DEFAULT_TAGS):
            key = name.lower()
            if key in existing:
                continue
            db.add(
                CrmAttendanceTag(
                    name=name,
                    is_system=True,
                    active=True,
                    sort_order=index,
                    created_at=_now_iso(),
                )
            )
            existing.add(key)
            created = True
        if created:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_attendance_tags(*, active_only: bool = True) -> list[dict]:
    ensure_default_attendance_tags()
    db = SessionLocal()
    try:
        q = db.query(CrmAttendanceTag)
        if active_only:
            q = q.filter(CrmAttendanceTag.active.is_(True))
        rows = q.order_by(CrmAttendanceTag.sort_order.asc(), CrmAttendanceTag.name.asc()).all()
        return [_row_to_dict(row) for row in rows]
    finally:
        db.close()


def list_attendance_tag_options() -> list[str]:
    return [row["name"] for row in list_attendance_tags(active_only=True)]


def add_attendance_tag(name: str, *, is_system: bool = False) -> str:
    clean = normalize_text(name)
    if not clean:
        raise ValueError("Informe o nome da tag.")
    db = SessionLocal()
    try:
        existing = db.query(CrmAttendanceTag).filter(CrmAttendanceTag.name.ilike(clean)).first()
        if existing:
            if not existing.active:
                existing.active = True
                db.commit()
            return existing.name
        max_order = db.query(CrmAttendanceTag).count()
        row = CrmAttendanceTag(
            name=clean,
            is_system=bool(is_system),
            active=True,
            sort_order=int(max_order),
            created_at=_now_iso(),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            again = db.query(CrmAttendanceTag).filter(CrmAttendanceTag.name.ilike(clean)).first()
            if again:
                return again.name
            raise
        return clean
    finally:
        db.close()


def remove_attendance_tag(name: str) -> None:
    clean = normalize_text(name)
    db = SessionLocal()
    try:
        row = db.query(CrmAttendanceTag).filter(CrmAttendanceTag.name.ilike(clean)).first()
        if not row:
            raise ValueError("Tag não encontrada.")
        if row.is_system or clean.lower() in SYSTEM_TAG_NAMES:
            row.active = False
        else:
            db.delete(row)
        db.commit()
    finally:
        db.close()
