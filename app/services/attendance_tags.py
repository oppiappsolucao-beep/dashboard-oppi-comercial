"""Tags de atendimento persistidas em DATABASE_URL."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.services.legacy_core import normalize_text
from database.connection import SessionLocal, engine
from database.models import AttendanceConversation, CrmAttendanceTag

logger = logging.getLogger(__name__)

DEFAULT_TAGS = [
    "Novo contato",
    "Parceiros",
    "Atendimento interno",
]

SYSTEM_TAG_NAMES = {name.strip().lower() for name in DEFAULT_TAGS}

# 6 cores pré-cadastradas (escolha na Configuração)
PRESET_COLORS: list[dict[str, str]] = [
    {"id": "verde", "bg": "#ECFDF5", "fg": "#047857", "label": "Verde"},
    {"id": "azul", "bg": "#EFF6FF", "fg": "#1D4ED8", "label": "Azul"},
    {"id": "laranja", "bg": "#FFF7ED", "fg": "#C2410C", "label": "Laranja"},
    {"id": "rosa", "bg": "#FDF2F8", "fg": "#BE185D", "label": "Rosa"},
    {"id": "roxo", "bg": "#F5F3FF", "fg": "#6D28D9", "label": "Roxo"},
    {"id": "ciano", "bg": "#ECFEFF", "fg": "#0E7490", "label": "Ciano"},
]

_PRESET_BY_ID = {item["id"]: item for item in PRESET_COLORS}
_DEFAULT_COLOR_IDS = ("laranja", "verde", "azul")


def normalize_color_id(value: str | None) -> str:
    key = normalize_text(value).lower()
    if key in _PRESET_BY_ID:
        return key
    # aceita hex antigo / lixo → verde
    return "verde"


def color_pair_for_id(color_id: str | None) -> tuple[str, str]:
    preset = _PRESET_BY_ID.get(normalize_color_id(color_id), PRESET_COLORS[0])
    return preset["bg"], preset["fg"]


def tag_color_pair(name: str, color_id: str | None = None) -> tuple[str, str]:
    if color_id:
        return color_pair_for_id(color_id)
    key = normalize_text(name).lower() or "x"
    idx = sum(ord(c) for c in key) % len(PRESET_COLORS)
    preset = PRESET_COLORS[idx]
    return preset["bg"], preset["fg"]


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_attendance_tag_schema() -> None:
    """Garante tabela + coluna color."""
    try:
        CrmAttendanceTag.__table__.create(bind=engine, checkfirst=True)
    except Exception:
        logger.exception("Falha ao criar crm_attendance_tags")
    try:
        insp = inspect(engine)
        if "crm_attendance_tags" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("crm_attendance_tags")}
        if "color" in cols:
            return
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE crm_attendance_tags "
                    "ADD COLUMN color VARCHAR(40) DEFAULT 'verde'"
                )
            )
        logger.info("Schema crm_attendance_tags: adicionada color")
    except Exception:
        logger.exception("Falha ao adicionar coluna color em crm_attendance_tags")


def _row_to_dict(row: CrmAttendanceTag) -> dict:
    name = row.name or ""
    color_id = normalize_color_id(getattr(row, "color", None) or "")
    bg, fg = color_pair_for_id(color_id)
    return {
        "id": row.id,
        "name": name,
        "color": color_id,
        "is_system": bool(row.is_system) or normalize_text(name).lower() in SYSTEM_TAG_NAMES,
        "active": bool(row.active),
        "sort_order": int(row.sort_order or 0),
        "status_label": "Ativo" if row.active else "Inativo",
        "status_class": "active" if row.active else "inactive",
        "preview_bg": bg,
        "preview_fg": fg,
    }


def ensure_default_attendance_tags() -> None:
    ensure_attendance_tag_schema()
    db = SessionLocal()
    try:
        rows = db.query(CrmAttendanceTag).all()
        existing = {(row.name or "").strip().lower(): row for row in rows}
        created = False
        for index, name in enumerate(DEFAULT_TAGS):
            key = name.lower()
            if key in existing:
                row = existing[key]
                if not normalize_text(getattr(row, "color", "") or ""):
                    row.color = _DEFAULT_COLOR_IDS[index % len(_DEFAULT_COLOR_IDS)]
                    created = True
                continue
            db.add(
                CrmAttendanceTag(
                    name=name,
                    is_system=True,
                    active=True,
                    sort_order=index,
                    color=_DEFAULT_COLOR_IDS[index % len(_DEFAULT_COLOR_IDS)],
                    created_at=_now_iso(),
                )
            )
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


def tag_style_map(names: list[str] | None = None) -> dict[str, dict[str, str]]:
    """Mapa nome → {bg, fg, color} a partir do cadastro de tags."""
    known = {row["name"]: row for row in list_attendance_tags(active_only=False)}
    out: dict[str, dict[str, str]] = {}
    wanted = names if names is not None else list(known.keys())
    for name in wanted:
        clean = normalize_text(name)
        if not clean:
            continue
        row = known.get(clean)
        if row:
            out[clean] = {
                "bg": row["preview_bg"],
                "fg": row["preview_fg"],
                "color": row["color"],
            }
        else:
            bg, fg = tag_color_pair(clean)
            out[clean] = {"bg": bg, "fg": fg, "color": "verde"}
    return out


def add_attendance_tag(
    name: str,
    *,
    color: str = "verde",
    is_system: bool = False,
) -> str:
    clean = normalize_text(name)
    if not clean:
        raise ValueError("Informe o nome da tag.")
    color_id = normalize_color_id(color)
    db = SessionLocal()
    try:
        existing = db.query(CrmAttendanceTag).filter(CrmAttendanceTag.name.ilike(clean)).first()
        if existing:
            existing.active = True
            existing.color = color_id
            db.commit()
            return existing.name
        max_order = db.query(CrmAttendanceTag).count()
        row = CrmAttendanceTag(
            name=clean,
            is_system=bool(is_system),
            active=True,
            sort_order=int(max_order),
            color=color_id,
            created_at=_now_iso(),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            again = db.query(CrmAttendanceTag).filter(CrmAttendanceTag.name.ilike(clean)).first()
            if again:
                again.active = True
                again.color = color_id
                db.commit()
                return again.name
            raise
        return clean
    finally:
        db.close()


def update_attendance_tag(
    tag_name: str,
    *,
    new_name: str | None = None,
    color: str | None = None,
    active: bool | None = None,
) -> dict:
    clean = normalize_text(tag_name)
    if not clean:
        raise ValueError("Tag não encontrada.")
    db = SessionLocal()
    try:
        row = db.query(CrmAttendanceTag).filter(CrmAttendanceTag.name.ilike(clean)).first()
        if not row:
            raise ValueError("Tag não encontrada.")
        renamed_from = row.name
        if new_name is not None:
            next_name = normalize_text(new_name)
            if not next_name:
                raise ValueError("Informe o nome da tag.")
            clash = (
                db.query(CrmAttendanceTag)
                .filter(
                    CrmAttendanceTag.name.ilike(next_name),
                    CrmAttendanceTag.id != row.id,
                )
                .first()
            )
            if clash:
                raise ValueError("Já existe uma tag com esse nome.")
            row.name = next_name
        if color is not None:
            row.color = normalize_color_id(color)
        if active is not None:
            row.active = bool(active)
        db.commit()
        db.refresh(row)
        result = _row_to_dict(row)
        if normalize_text(renamed_from) != result["name"]:
            try:
                _rename_tag_everywhere(renamed_from, result["name"])
            except Exception:
                logger.exception("Falha ao renomear tag nas conversas/cadastros")
        return result
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


def _rename_tag_everywhere(old_name: str, new_name: str) -> None:
    old = normalize_text(old_name)
    new = normalize_text(new_name)
    if not old or not new or old == new:
        return
    db = SessionLocal()
    try:
        rows = db.query(AttendanceConversation).all()
        changed = False
        for row in rows:
            try:
                tags = json.loads(row.tags_json or "[]")
            except Exception:
                tags = []
            if not isinstance(tags, list):
                continue
            next_tags = []
            touched = False
            for tag in tags:
                text_tag = normalize_text(tag)
                if text_tag.lower() == old.lower():
                    next_tags.append(new)
                    touched = True
                elif text_tag:
                    next_tags.append(text_tag)
            if touched:
                row.tags_json = json.dumps(next_tags, ensure_ascii=False)
                changed = True
        if changed:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()

    try:
        from app.services.crm_registrations_storage import rename_attendance_tag_in_registrations

        rename_attendance_tag_in_registrations(old, new)
    except Exception:
        logger.exception("rename tag in registrations falhou")
