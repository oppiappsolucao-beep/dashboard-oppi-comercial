"""Autenticação por chave API do Comercial (integrações machine-to-machine)."""
from __future__ import annotations

import hmac
import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


def comercial_api_configured() -> bool:
    return bool(get_settings().comercial_api_key)


def _extract_bearer(authorization: str | None) -> str:
    raw = (authorization or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def verify_comercial_api_key(provided: str) -> bool:
    expected = get_settings().comercial_api_key
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def require_comercial_api_key(
    x_oppi_comercial_key: str | None = Header(default=None, alias="X-Oppi-Comercial-Key"),
    authorization: str | None = Header(default=None),
) -> str:
    """Exige COMERCIAL_API_KEY via header X-Oppi-Comercial-Key ou Authorization: Bearer."""
    if not comercial_api_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API do Comercial não configurada. Defina COMERCIAL_API_KEY no EasyPanel.",
        )

    provided = (x_oppi_comercial_key or "").strip() or _extract_bearer(authorization)
    if not verify_comercial_api_key(provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave API inválida ou ausente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return provided


def generate_comercial_api_key(*, prefix: str = "oppi_crm") -> str:
    """Gera uma chave forte para configurar no EasyPanel (uso manual / docs)."""
    token = secrets.token_urlsafe(32)
    return f"{prefix}_{token}"
