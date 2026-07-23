"""Nichos comerciais persistidos em DATABASE_URL."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.services.legacy_core import normalize_text
from database.connection import SessionLocal
from database.models import CrmNiche

DEFAULT_NICHES = [
    "Saúde e Bem-estar",
    "Beleza e Estética",
    "Alimentação",
    "Comércio e Varejo",
    "Serviços",
    "Tecnologia",
    "Construção e Imóveis",
    "Automotivo",
    "Educação",
    "Pet",
    "Indústria",
    "Agronegócio",
    "Transporte e Logística",
    "Marketing e Comunicação",
    "Financeiro e Contábil",
    "Jurídico",
    "Turismo e Eventos",
    "Telecomunicações",
    "Outros",
    "Não informado",
]

OUTROS_LABEL = "Outros"


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_default_niches() -> None:
    db = SessionLocal()
    try:
        existing = {
            (row.name or "").strip().lower()
            for row in db.query(CrmNiche).all()
        }
        created = False
        for index, name in enumerate(DEFAULT_NICHES):
            key = name.lower()
            if key in existing:
                continue
            db.add(
                CrmNiche(
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


def list_niche_options(*, include_inactive: bool = False) -> list[str]:
    ensure_default_niches()
    db = SessionLocal()
    try:
        q = db.query(CrmNiche)
        if not include_inactive:
            q = q.filter(CrmNiche.active.is_(True))
        rows = q.order_by(CrmNiche.sort_order.asc(), CrmNiche.name.asc()).all()
        names = [normalize_text(r.name) for r in rows if normalize_text(r.name)]
        # Garante Outros / Não informado no fim se existirem
        ordered: list[str] = []
        tail = {OUTROS_LABEL.lower(), "não informado", "nao informado"}
        for name in names:
            if name.lower() not in tail:
                ordered.append(name)
        for name in names:
            if name.lower() in tail and name not in ordered:
                ordered.append(name)
        return ordered
    finally:
        db.close()


def list_niches_rows() -> list[dict]:
    ensure_default_niches()
    db = SessionLocal()
    try:
        rows = (
            db.query(CrmNiche)
            .order_by(CrmNiche.sort_order.asc(), CrmNiche.name.asc())
            .all()
        )
        return [
            {
                "id": row.id,
                "name": row.name,
                "is_system": bool(row.is_system),
                "active": bool(row.active),
                "status_label": "Ativo" if row.active else "Inativo",
                "status_class": "active" if row.active else "inactive",
            }
            for row in rows
        ]
    finally:
        db.close()


def add_niche(name: str, *, is_system: bool = False) -> str:
    clean = normalize_text(name)
    if not clean:
        raise ValueError("Informe o nome do nicho.")
    if len(clean) < 2:
        raise ValueError("O nome do nicho deve ter pelo menos 2 caracteres.")

    db = SessionLocal()
    try:
        existing = (
            db.query(CrmNiche)
            .filter(CrmNiche.name.ilike(clean))
            .first()
        )
        if existing:
            if not existing.active:
                existing.active = True
                db.commit()
            return existing.name

        max_order = db.query(CrmNiche).count()
        row = CrmNiche(
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
            again = db.query(CrmNiche).filter(CrmNiche.name.ilike(clean)).first()
            if again:
                return again.name
            raise
        return clean
    finally:
        db.close()


def remove_niche(name: str) -> None:
    clean = normalize_text(name)
    db = SessionLocal()
    try:
        row = db.query(CrmNiche).filter(CrmNiche.name.ilike(clean)).first()
        if not row:
            raise ValueError("Nicho não encontrado.")
        if row.is_system and clean.lower() in {n.lower() for n in DEFAULT_NICHES}:
            # Sistema: desativa em vez de apagar
            row.active = False
        else:
            db.delete(row)
        db.commit()
    finally:
        db.close()


def resolve_nicho_for_save(nicho: str, nicho_outro: str = "") -> str:
    """Se nicho for Outros e houver texto, cadastra o novo e retorna o nome final."""
    selected = normalize_text(nicho)
    custom = normalize_text(nicho_outro)
    if selected.lower() == OUTROS_LABEL.lower() and custom:
        return add_niche(custom, is_system=False)
    if selected:
        return selected
    return custom
