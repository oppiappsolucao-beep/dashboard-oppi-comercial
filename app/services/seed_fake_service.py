"""Cadastro FAKE de serviço comercial — grava na aba Configuracoes da planilha."""
from __future__ import annotations

import logging

from app.config import settings
from app.services.app_settings import invalidate_app_settings_cache, load_app_settings
from app.services.commercial_services import add_commercial_service, list_commercial_services

logger = logging.getLogger(__name__)

FAKE_SERVICE_NAME = "Consultoria Comercial FAKE"


def find_fake_test_service() -> str | None:
    target = FAKE_SERVICE_NAME.lower()
    for name in list_commercial_services():
        if name.lower() == target:
            return name
    return None


def seed_fake_test_service() -> dict:
    if not settings.sheets_configured:
        raise RuntimeError("Planilha não configurada. Verifique GCP_SERVICE_ACCOUNT_B64 no Easypanel.")

    existing = find_fake_test_service()
    if existing:
        return {
            "created": False,
            "service_name": existing,
            "message": f"Serviço FAKE já cadastrado: {existing}",
        }

    add_commercial_service(FAKE_SERVICE_NAME)
    invalidate_app_settings_cache()
    load_app_settings(force_refresh=True)

    logger.info("Serviço FAKE cadastrado: %s", FAKE_SERVICE_NAME)
    return {
        "created": True,
        "service_name": FAKE_SERVICE_NAME,
        "message": (
            f"Serviço FAKE cadastrado na aba Configuracoes. "
            f"Nome: {FAKE_SERVICE_NAME}"
        ),
    }


def ensure_fake_test_service_on_startup() -> None:
    result = seed_fake_test_service()
    logger.info("Seed serviço FAKE: %s", result.get("message"))
