"""Geração de PDF de proposta a partir de modelo Google Docs."""
import hashlib
import io
import re
import uuid
from pathlib import Path

import pandas as pd
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from app.services.app_settings import get_proposal_template_doc_id
from app.services.legacy_core import (
    _load_google_credentials_info,
    build_client_commercial_summary,
    find_prepared_company_row,
    format_proposal_value_display,
    identify_columns,
    load_sheet_data,
    normalize_text,
    prepare_data,
    resolve_company_name,
)

GOOGLE_DOCS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

DRIVE_PARAMS = {"supportsAllDrives": "true"}

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


def _proposal_cache_dir() -> Path:
    path = Path(__file__).resolve().parent.parent.parent / "storage" / "proposal_pdfs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _proposal_cache_key(
    company_name: str,
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
) -> str:
    payload = "|".join([
        normalize_text(company_name).lower(),
        normalize_text(value),
        normalize_text(servico),
        normalize_text(colaboradores),
        normalize_text(get_proposal_template_doc_id()),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _proposal_cache_path(cache_key: str) -> Path:
    return _proposal_cache_dir() / f"{cache_key}.pdf"


def get_cached_proposal_pdf(cache_key: str) -> bytes | None:
    path = _proposal_cache_path(cache_key)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception:
        return None


def store_proposal_pdf_cache(cache_key: str, pdf_bytes: bytes) -> None:
    _proposal_cache_path(cache_key).write_bytes(pdf_bytes)


def _service_account_email() -> str:
    try:
        credentials_info = _load_google_credentials_info()
    except Exception:
        return ""
    return normalize_text(credentials_info.get("client_email", ""))


def _google_session() -> AuthorizedSession:
    credentials_info = _load_google_credentials_info()
    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=GOOGLE_DOCS_SCOPES,
    )
    return AuthorizedSession(credentials)


def _company_row(company_name: str, df: pd.DataFrame):
    return find_prepared_company_row(company_name, df)


def _sheet_field(row, columns: dict, key: str) -> str:
    column_name = columns.get(key)
    if not row or not column_name or column_name not in row.index:
        return ""
    return normalize_text(row.get(column_name, ""))


def _sheet_email(row, columns: dict) -> str:
    for key in ("email", "email_socio_1"):
        value = _sheet_field(row, columns, key)
        if value:
            return value
    return ""


def build_proposal_placeholder_values(
    company_name: str,
    df: pd.DataFrame,
    columns: dict,
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
) -> dict[str, str]:
    resolved_company = resolve_company_name(company_name, df)
    row = _company_row(resolved_company, df)
    commercial = build_client_commercial_summary(row, columns) if row is not None else {
        "servico": "Não informado",
        "valor_proposta": "Não informado",
        "colaboradores": "Não informado",
    }

    proposal_value = normalize_text(value) or commercial.get("valor_proposta", "Não informado")
    if proposal_value and proposal_value != "Não informado":
        proposal_value = format_proposal_value_display(proposal_value)

    servico_value = normalize_text(servico) or commercial.get("servico", "Não informado")
    colaboradores_value = normalize_text(colaboradores) or commercial.get("colaboradores", "Não informado")

    now = pd.Timestamp.now(tz="America/Sao_Paulo")
    proposal_number = f"OPPI-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

    canonical = {
        "{{EMPRESA}}": resolved_company or "Não informado",
        "{{VALOR_PROPOSTA}}": proposal_value,
        "{{SERVICO}}": servico_value,
        "{{VENDEDOR}}": normalize_text(row.get("_vendedor", "")) if row is not None else "Sem vendedor",
        "{{DATA}}": now.strftime("%d/%m/%Y"),
        "{{NUMERO_PROPOSTA}}": proposal_number,
        "{{CNPJ}}": _sheet_field(row, columns, "cnpj") or "Não informado",
        "{{TELEFONE}}": _sheet_field(row, columns, "telefone_b2b") or _sheet_field(row, columns, "telefone_socio_1") or "Não informado",
        "{{EMAIL}}": _sheet_email(row, columns) or "Não informado",
        "{{COLABORADORES}}": colaboradores_value,
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


def _copy_template_error_detail(response) -> str:
    service_email = _service_account_email()
    base = "Não consegui copiar o modelo Google Docs."
    if service_email:
        base += f" Compartilhe o documento como Editor com {service_email}."
    detail = normalize_text(getattr(response, "text", ""))[:180]
    if detail:
        base += f" Retorno da API: {detail}"
    return base


def generate_proposal_pdf_from_template(
    company_name: str,
    df: pd.DataFrame,
    columns: dict,
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
) -> bytes:
    template_id = get_proposal_template_doc_id()
    if not template_id:
        raise RuntimeError("Modelo de proposta não configurado.")

    resolved_company = resolve_company_name(company_name, df)
    session = _google_session()
    copy_response = session.post(
        f"https://www.googleapis.com/drive/v3/files/{template_id}/copy",
        params=DRIVE_PARAMS,
        json={"name": f"Proposta {resolved_company or 'Cliente'}"},
        timeout=60,
    )
    if copy_response.status_code >= 400:
        raise RuntimeError(_copy_template_error_detail(copy_response))

    copy_id = copy_response.json().get("id")
    if not copy_id:
        raise RuntimeError("Google Drive não retornou o arquivo copiado.")

    try:
        values = build_proposal_placeholder_values(
            resolved_company,
            df,
            columns,
            value=value,
            servico=servico,
            colaboradores=colaboradores,
        )
        _replace_placeholders(session, copy_id, values)

        export_response = session.get(
            f"https://www.googleapis.com/drive/v3/files/{copy_id}/export",
            params={"mimeType": "application/pdf", **DRIVE_PARAMS},
            timeout=90,
        )
        if export_response.status_code >= 400:
            raise RuntimeError("Erro ao exportar o PDF a partir do Google Docs.")

        return export_response.content
    finally:
        session.delete(
            f"https://www.googleapis.com/drive/v3/files/{copy_id}",
            params=DRIVE_PARAMS,
            timeout=30,
        )


def generate_proposal_pdf(
    company_name: str,
    df: pd.DataFrame | None = None,
    columns: dict | None = None,
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
    *,
    use_cache: bool = True,
) -> bytes:
    if df is None or columns is None:
        raw_df = load_sheet_data()
        columns = identify_columns(raw_df)
        df = prepare_data(raw_df, columns)

    resolved_company = resolve_company_name(company_name, df)
    cache_key = _proposal_cache_key(resolved_company, value, servico, colaboradores)
    if use_cache:
        cached = get_cached_proposal_pdf(cache_key)
        if cached:
            return cached

    template_id = get_proposal_template_doc_id()
    if not template_id:
        raise RuntimeError(
            "Modelo de proposta não configurado. Informe o link do Google Docs em Configurações → Geral."
        )

    try:
        pdf_bytes = generate_proposal_pdf_from_template(
            resolved_company,
            df,
            columns,
            value=value,
            servico=servico,
            colaboradores=colaboradores,
        )
    except Exception as error:
        service_email = _service_account_email()
        hint = f" Compartilhe o documento com {service_email}." if service_email else ""
        raise RuntimeError(
            "Não foi possível gerar o PDF a partir do modelo Google Docs."
            f"{hint} Detalhe: {error}"
        ) from error

    store_proposal_pdf_cache(cache_key, pdf_bytes)
    return pdf_bytes


def prepare_generated_proposal_pdf(
    company_name: str,
    df: pd.DataFrame,
    columns: dict,
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
) -> tuple[str | None, str | None]:
    """Gera o PDF antecipadamente. Retorna (cache_key, erro)."""
    resolved_company = resolve_company_name(company_name, df)
    cache_key = _proposal_cache_key(resolved_company, value, servico, colaboradores)
    try:
        generate_proposal_pdf(
            resolved_company,
            df,
            columns,
            value=value,
            servico=servico,
            colaboradores=colaboradores,
            use_cache=True,
        )
    except Exception as error:
        return cache_key, str(error)
    return cache_key, None


def proposal_pdf_filename(company_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", normalize_text(company_name)).strip("_") or "cliente"
    return f"Proposta_{safe}.pdf"
