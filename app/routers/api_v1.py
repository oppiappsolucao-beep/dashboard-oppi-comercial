"""API pública do Comercial (chave API) — cadastro + financeiro + Oppi Ponto."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.api_auth import comercial_api_configured, require_comercial_api_key
from app.services.api_provisioning import (
    ProvisioningError,
    get_cadastro_by_cnpj,
    provision_cadastro,
)
from app.services.oppi_ponto_client import oppi_ponto_configured

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


class CadastroCreateRequest(BaseModel):
    """Payload alinhado ao formulário de cadastro do Oppi Ponto."""

    tipo_cadastro: Literal["novo_cliente", "nova_filial"] = "novo_cliente"

    # Responsável / gestor
    responsavel_nome: str | None = Field(default=None, description="Nome completo do responsável")
    gestor_login: str | None = Field(default=None, description="Nome do gestor (login admin)")
    telefone: str | None = None
    whatsapp: str | None = None

    # Empresa
    cnpj: str
    razao_social: str
    cargo_gestor: str | None = None

    # Acesso
    email_verificacao: str | None = Field(
        default=None, description="E-mail para validação (contrato e código de aceite)"
    )
    email_cobranca: str | None = None
    email_login: str | None = None
    senha: str | None = None
    password: str | None = Field(default=None, description="Alias de senha")

    # Filial — gestor já existente
    gestor_email: str | None = Field(
        default=None, description="E-mail do gestor existente (nova_filial)"
    )
    matriz_cnpj: str | None = None
    empresa_matriz_sheet_row: int | None = None

    # Plano e pagamento
    plano_valor: str | float
    plano_vencimento: str
    pagamento_modalidade: Literal["boleto", "cartao_recorrente", "manual"] = "boleto"

    # CRM
    vendedor: str | None = None
    colaboradores: str | None = None
    observacoes: str | None = None
    sincronizar_ponto: bool = True


@router.get("/health")
def api_health(_: str = Depends(require_comercial_api_key)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "ok": True,
        "service": "dashboard-oppi-comercial",
        "api": "v1",
        "ponto_configured": oppi_ponto_configured(),
        "api_key_configured": comercial_api_configured(),
        "public_url": settings.public_app_url,
    }


@router.post("/cadastros", status_code=status.HTTP_201_CREATED)
def api_create_cadastro(
    body: CadastroCreateRequest,
    _: str = Depends(require_comercial_api_key),
) -> dict[str, Any]:
    """
    Cria cadastro no Comercial, gera financeiro e migra/onboard no Oppi Ponto.

    - boleto / cartao_recorrente → Ponto gera cobrança no Asaas
    - manual → sem Asaas (você cola o link depois)
    """
    payload = body.model_dump()
    try:
        return provision_cadastro(payload, sincronizar_ponto=body.sincronizar_ponto)
    except ProvisioningError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao provisionar cadastro: {exc}",
        ) from exc


@router.get("/cadastros/by-cnpj/{cnpj}")
def api_get_cadastro_by_cnpj(
    cnpj: str,
    _: str = Depends(require_comercial_api_key),
) -> dict[str, Any]:
    try:
        found = get_cadastro_by_cnpj(cnpj)
    except ProvisioningError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cadastro não encontrado.")
    return {"ok": True, "cadastro": found}
