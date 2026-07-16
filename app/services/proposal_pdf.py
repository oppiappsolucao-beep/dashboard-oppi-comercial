"""Geração de PDF de proposta a partir de modelo Google Docs."""
import io
import re
import uuid

import pandas as pd
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from app.services.app_settings import get_proposal_template_doc_id
from app.services.legacy_core import (
    _load_google_credentials_info,
    _pricing_generate_pdf,
    build_client_commercial_summary,
    format_proposal_value_display,
    identify_columns,
    load_sheet_data,
    normalize_text,
    prepare_data,
)

GOOGLE_DOCS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

PLACEHOLDER_ALIASES = {
    "{{EMPRESA}}": ["{{EMPRESA}}", "{{empresa}}", "{{NOME_EMPRESA}}", "{{nome_empresa}}"],
    "{{VALOR_PROPOSTA}}": ["{{VALOR_PROPOSTA}}", "{{valor_proposta}}", "{{VALOR}}", "{{valor}}"],
    "{{SERVICO}}": ["{{SERVICO}}", "{{servico}}", "{{SOLUCAO}}", "{{solucao}}"],
    "{{VENDEDOR}}": ["{{VENDEDOR}}", "{{vendedor}}"],
    "{{DATA}}": ["{{DATA}}", "{{data}}", "{{DATA_PROPOSTA}}", "{{data_proposta}}"],
    "{{NUMERO_PROPOSTA}}": ["{{NUMERO_PROPOSTA}}", "{{numero_proposta}}", "{{NUMERO}}", "{{numero}}"],
    "{{CNPJ}}": ["{{CNPJ}}", "{{cnpj}}"],
    "{{TELEFONE}}": ["{{TELEFONE}}", "{{telefone}}"],
    "{{EMAIL}}": ["{{EMAIL}}", "{{email}}"],
    "{{COLABORADORES}}": ["{{COLABORADORES}}", "{{colaboradores}}"],
    "{{ENDERECO}}": ["{{ENDERECO}}", "{{endereco}}"],
}


def _google_session() -> AuthorizedSession:
    credentials_info = _load_google_credentials_info()
    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=GOOGLE_DOCS_SCOPES,
    )
    return AuthorizedSession(credentials)


def _company_row(company_name: str, df: pd.DataFrame):
    if df.empty:
        return None
    matches = df[df["_empresa"].astype(str) == normalize_text(company_name)].copy()
    if matches.empty:
        return None
    if "_sheet_row" in matches.columns:
        matches = matches.sort_values("_sheet_row", ascending=False)
    return matches.iloc[0]


def _sheet_field(row, columns: dict, key: str) -> str:
    column_name = columns.get(key)
    if not row or not column_name or column_name not in row.index:
        return ""
    return normalize_text(row.get(column_name, ""))


def build_proposal_placeholder_values(
    company_name: str,
    df: pd.DataFrame,
    columns: dict,
    value: str | None = None,
) -> dict[str, str]:
    row = _company_row(company_name, df)
    commercial = build_client_commercial_summary(row, columns) if row is not None else {
        "servico": "Não informado",
        "valor_proposta": "Não informado",
        "colaboradores": "Não informado",
    }

    proposal_value = value or commercial.get("valor_proposta", "Não informado")
    if proposal_value and proposal_value != "Não informado":
        proposal_value = format_proposal_value_display(proposal_value)

    now = pd.Timestamp.now(tz="America/Sao_Paulo")
    proposal_number = f"OPPI-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

    canonical = {
        "{{EMPRESA}}": normalize_text(company_name) or "Não informado",
        "{{VALOR_PROPOSTA}}": proposal_value,
        "{{SERVICO}}": commercial.get("servico", "Não informado"),
        "{{VENDEDOR}}": normalize_text(row.get("_vendedor", "")) if row is not None else "Sem vendedor",
        "{{DATA}}": now.strftime("%d/%m/%Y"),
        "{{NUMERO_PROPOSTA}}": proposal_number,
        "{{CNPJ}}": _sheet_field(row, columns, "cnpj") or "Não informado",
        "{{TELEFONE}}": _sheet_field(row, columns, "telefone_b2b") or "Não informado",
        "{{EMAIL}}": _sheet_field(row, columns, "email") or "Não informado",
        "{{COLABORADORES}}": commercial.get("colaboradores", "Não informado"),
        "{{ENDERECO}}": _sheet_field(row, columns, "endereco") or "Não informado",
    }

    values: dict[str, str] = {}
    for key, aliases in PLACEHOLDER_ALIASES.items():
        replacement = canonical[key]
        for alias in aliases:
            values[alias] = replacement
    return values


def _replace_placeholders(session: AuthorizedSession, document_id: str, values: dict[str, str]) -> None:
    requests = []
    for placeholder, replacement in values.items():
        requests.append({
            "replaceAllText": {
                "containsText": {"text": placeholder, "matchCase": True},
                "replaceText": replacement or "—",
            }
        })

    response = session.post(
        f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate",
        json={"requests": requests},
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Erro ao preencher o modelo Google Docs: {response.text[:240]}")


def generate_proposal_pdf_from_template(
    company_name: str,
    df: pd.DataFrame,
    columns: dict,
    value: str | None = None,
) -> bytes:
    template_id = get_proposal_template_doc_id()
    if not template_id:
        raise RuntimeError("Modelo de proposta não configurado.")

    session = _google_session()
    copy_response = session.post(
        f"https://www.googleapis.com/drive/v3/files/{template_id}/copy",
        json={"name": f"Proposta {normalize_text(company_name) or 'Cliente'}"},
        timeout=60,
    )
    if copy_response.status_code >= 400:
        raise RuntimeError(
            "Não consegui copiar o modelo. Compartilhe o Google Docs com a conta de serviço do Google Sheets."
        )

    copy_id = copy_response.json().get("id")
    if not copy_id:
        raise RuntimeError("Google Drive não retornou o arquivo copiado.")

    try:
        values = build_proposal_placeholder_values(company_name, df, columns, value=value)
        _replace_placeholders(session, copy_id, values)

        export_response = session.get(
            f"https://www.googleapis.com/drive/v3/files/{copy_id}/export",
            params={"mimeType": "application/pdf"},
            timeout=90,
        )
        if export_response.status_code >= 400:
            raise RuntimeError("Erro ao exportar o PDF a partir do Google Docs.")

        return export_response.content
    finally:
        session.delete(
            f"https://www.googleapis.com/drive/v3/files/{copy_id}",
            timeout=30,
        )


def generate_proposal_pdf(
    company_name: str,
    df: pd.DataFrame | None = None,
    columns: dict | None = None,
    value: str | None = None,
) -> bytes:
    if df is None or columns is None:
        raw_df = load_sheet_data()
        columns = identify_columns(raw_df)
        df = prepare_data(raw_df, columns)

    template_id = get_proposal_template_doc_id()
    if template_id:
        try:
            return generate_proposal_pdf_from_template(company_name, df, columns, value=value)
        except Exception:
            pass

    return _pricing_generate_pdf(company_name, df, columns)


def proposal_pdf_filename(company_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", normalize_text(company_name)).strip("_") or "cliente"
    return f"Proposta_{safe}.pdf"
