"""Cliente HTTP do Oppi Ponto (plataforma) para o CRM Comercial."""
from __future__ import annotations

import logging
import re
from typing import Any

import requests

from app.config import settings

log = logging.getLogger(__name__)


class OppiPontoError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def oppi_ponto_configured() -> bool:
    return bool(settings.oppi_ponto_api_url and settings.oppi_ponto_crm_api_key)


def _headers() -> dict[str, str]:
    return {
        "X-Oppi-Crm-Key": settings.oppi_ponto_crm_api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _url(path: str) -> str:
    base = settings.oppi_ponto_api_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/api/"):
        path = "/api" + path
    return base + path


def _request(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None) -> dict:
    if not oppi_ponto_configured():
        raise OppiPontoError(
            "Oppi Ponto não configurado. Defina OPPI_PONTO_API_URL e OPPI_PONTO_CRM_API_KEY no EasyPanel."
        )
    try:
        response = requests.request(
            method,
            _url(path),
            headers=_headers(),
            json=json_body,
            params=params,
            timeout=45,
        )
    except requests.RequestException as exc:
        raise OppiPontoError(f"Falha de rede ao falar com Oppi Ponto: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text
        try:
            data = response.json()
            detail = data.get("detail") or data
        except Exception:
            pass
        raise OppiPontoError(
            f"Oppi Ponto HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
            payload=detail,
        )
    if not response.content:
        return {"ok": True}
    try:
        return response.json()
    except Exception:
        return {"ok": True, "raw": response.text}


def normalize_cnpj(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def get_company_by_cnpj(cnpj: str) -> dict:
    digits = normalize_cnpj(cnpj)
    return _request("GET", f"/platform/companies/by-cnpj/{digits}")


def set_company_status(company_id: int, *, ativo: bool, bloqueado: bool | None = None) -> dict:
    params: dict[str, Any] = {"ativo": str(ativo).lower()}
    if bloqueado is not None:
        params["bloqueado"] = str(bloqueado).lower()
    return _request("PATCH", f"/platform/companies/{int(company_id)}/status", params=params)


def release_payment(
    company_id: int,
    *,
    motivo: str,
    plano_vencimento: str | None = None,
    aplicar_filiais: bool = True,
) -> dict:
    body: dict[str, Any] = {
        "motivo": motivo,
        "aplicar_filiais": aplicar_filiais,
    }
    if plano_vencimento:
        body["plano_vencimento"] = plano_vencimento
    return _request("POST", f"/platform/companies/{int(company_id)}/release-payment", json_body=body)


def onboard_company(payload: dict) -> dict:
    return _request("POST", "/platform/companies/onboard", json_body=payload)
