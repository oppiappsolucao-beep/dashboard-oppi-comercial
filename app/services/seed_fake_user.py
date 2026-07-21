"""Cadastro FAKE de usuário — grava na aba Usuarios da planilha."""
from __future__ import annotations

import logging
import uuid

from app.config import settings
from app.services.account_users import (
    _hash_password,
    _now_iso,
    _serialize_user,
    append_account_user_to_sheet,
    invalidate_account_users_cache,
    load_account_users,
    user_exists_in_sheet,
)

logger = logging.getLogger(__name__)

FAKE_USER_NAME = "Usuário FAKE Teste"
FAKE_USER_USERNAME = "usuario.fake"
FAKE_USER_PASSWORD = "fake2026"
FAKE_USER_EMAIL = "usuario.fake@oppitech.com.br"
FAKE_USER_ROLE = "Vendedor"


def find_fake_test_user() -> dict | None:
    target = FAKE_USER_USERNAME.lower()
    for user in load_account_users():
        if user["username"].lower() == target:
            return dict(user)
    return None


def seed_fake_test_user() -> dict:
    if not settings.sheets_configured:
        raise RuntimeError("Planilha não configurada. Verifique GCP_SERVICE_ACCOUNT_B64 no Easypanel.")

    existing = find_fake_test_user()
    if existing:
        return {
            "created": False,
            "user_id": existing["id"],
            "username": existing["username"],
            "name": existing["name"],
            "message": (
                f"Usuário FAKE já cadastrado. Login: {FAKE_USER_USERNAME} · "
                f"Senha: {FAKE_USER_PASSWORD}"
            ),
        }

    if user_exists_in_sheet(FAKE_USER_USERNAME):
        invalidate_account_users_cache()
        existing = find_fake_test_user()
        if existing:
            return {
                "created": False,
                "user_id": existing["id"],
                "username": existing["username"],
                "name": existing["name"],
                "message": (
                    f"Usuário FAKE já cadastrado. Login: {FAKE_USER_USERNAME} · "
                    f"Senha: {FAKE_USER_PASSWORD}"
                ),
            }

    user = _serialize_user({
        "id": str(uuid.uuid4()),
        "name": FAKE_USER_NAME,
        "email": FAKE_USER_EMAIL,
        "username": FAKE_USER_USERNAME,
        "password_hash": _hash_password(FAKE_USER_PASSWORD),
        "role": FAKE_USER_ROLE,
        "active": True,
        "last_access": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    })

    append_account_user_to_sheet(user)
    invalidate_account_users_cache()
    load_account_users(force_refresh=True)

    logger.info("Usuário FAKE cadastrado: %s", FAKE_USER_USERNAME)
    return {
        "created": True,
        "user_id": user["id"],
        "username": user["username"],
        "name": user["name"],
        "message": (
            f"Usuário FAKE cadastrado na aba Usuarios. "
            f"Login: {FAKE_USER_USERNAME} · Senha: {FAKE_USER_PASSWORD}"
        ),
    }


def ensure_fake_test_user_on_startup() -> None:
    result = seed_fake_test_user()
    logger.info("Seed usuário FAKE: %s", result.get("message"))
