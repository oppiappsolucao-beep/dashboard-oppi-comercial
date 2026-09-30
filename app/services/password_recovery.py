"""Recuperação de senha: o código sai no log do serviço, não na tela."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time

import bcrypt

from app.config import settings
from app.services.legacy_core import normalize_text

logger = logging.getLogger(__name__)

_CODE_TTL_SEC = 20 * 60
_RESEND_COOLDOWN_SEC = 60
_MAX_ATTEMPTS = 5
_ADMIN_HASH_KEY = "admin_login_password_hash"


def _reset_key(username: str) -> str:
    return f"pwd_reset:{normalize_text(username).lower()}"


def _code_hash(username: str, code: str) -> str:
    raw = f"{settings.session_secret}:{normalize_text(username).lower()}:{code.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_meta(key: str) -> str:
    from database.connection import SessionLocal
    from database.models import AppMeta

    db = SessionLocal()
    try:
        row = db.get(AppMeta, key)
        return (row.value or "") if row else ""
    finally:
        db.close()


def _save_meta(key: str, value: str) -> None:
    from database.connection import SessionLocal
    from database.models import AppMeta

    db = SessionLocal()
    try:
        db.merge(AppMeta(key=key, value=value))
        db.commit()
    finally:
        db.close()


def _delete_meta(key: str) -> None:
    from database.connection import SessionLocal
    from database.models import AppMeta

    db = SessionLocal()
    try:
        row = db.get(AppMeta, key)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def _is_env_admin(username: str) -> bool:
    clean = normalize_text(username)
    admin = normalize_text(settings.app_username)
    return bool(clean and admin and clean == admin)


def user_can_recover(username: str) -> bool:
    if _is_env_admin(username):
        return True
    from app.services.account_users import get_account_user_by_username

    return get_account_user_by_username(username) is not None


def request_reset_code(username: str) -> None:
    """Gera um código se o usuário existir. A resposta da tela é sempre a mesma."""
    clean = normalize_text(username)
    if not user_can_recover(clean):
        return

    key = _reset_key(clean)
    now = time.time()
    try:
        current = json.loads(_load_meta(key) or "{}")
    except json.JSONDecodeError:
        current = {}
    created = float(current.get("created") or 0)
    expires = float(current.get("expires") or 0)
    if current.get("code_hash") and expires > now and (now - created) < _RESEND_COOLDOWN_SEC:
        return

    code = f"{secrets.randbelow(100_000_000):08d}"
    _save_meta(
        key,
        json.dumps(
            {
                "code_hash": _code_hash(clean, code),
                "created": now,
                "expires": now + _CODE_TTL_SEC,
                "attempts": 0,
            }
        ),
    )
    logger.warning(
        "Recuperacao de senha para '%s'. Codigo: %s. Valido por 20 minutos.",
        clean,
        code,
    )


def verify_admin_password_override(password: str) -> bool:
    stored = _load_meta(_ADMIN_HASH_KEY).strip()
    if not stored:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    except Exception:
        return False


def complete_password_reset(username: str, code: str, new_password: str) -> str | None:
    """Devolve None quando a senha foi trocada, ou uma mensagem de erro."""
    clean = normalize_text(username)
    clean_code = normalize_text(code)
    password = new_password.strip()
    if len(password) < 6:
        return "A senha deve ter pelo menos 6 caracteres."
    if not user_can_recover(clean):
        return "Código inválido ou expirado."

    key = _reset_key(clean)
    try:
        current = json.loads(_load_meta(key) or "{}")
    except json.JSONDecodeError:
        current = {}
    expires = float(current.get("expires") or 0)
    attempts = int(current.get("attempts") or 0)
    expected = normalize_text(current.get("code_hash"))
    if not expected or expires < time.time() or attempts >= _MAX_ATTEMPTS:
        _delete_meta(key)
        return "Código inválido ou expirado."
    if _code_hash(clean, clean_code) != expected:
        current["attempts"] = attempts + 1
        _save_meta(key, json.dumps(current))
        return "Código inválido ou expirado."

    if _is_env_admin(clean):
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        _save_meta(_ADMIN_HASH_KEY, hashed)

    from app.services.account_users import get_account_user_by_username, set_account_user_password

    if get_account_user_by_username(clean):
        set_account_user_password(clean, password)

    _delete_meta(key)
    return None
