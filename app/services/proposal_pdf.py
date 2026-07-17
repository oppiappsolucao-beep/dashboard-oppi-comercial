"""Geração de PDF de proposta a partir de modelo Google Docs."""
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
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
    row_contact_email,
    row_contact_phone,
    row_field_value,
    row_get,
)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DRIVE_PARAMS = {"supportsAllDrives": "true"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

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
        scopes=GOOGLE_SCOPES,
    )
    return AuthorizedSession(credentials)


def _company_row(company_name: str, df: pd.DataFrame):
    return find_prepared_company_row(company_name, df)


def build_proposal_placeholder_values(
    company_name: str,
    df: pd.DataFrame,
    columns: dict,
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
) -> dict[str, str]:
    resolved_company = resolve_company_name(company_name, df)
    row = _company_row(company_name, df)
    if row is None:
        row = _company_row(resolved_company, df)
    commercial = build_client_commercial_summary(row, columns) if row is not None else {
        "servico": "Não informado",
        "valor_proposta": "Não informado",
        "colaboradores": "Não informado",
    }

    proposal_value = normalize_text(value) or normalize_text(commercial.get("valor_proposta", "Não informado"))
    if normalize_text(proposal_value) and normalize_text(proposal_value) != "Não informado":
        proposal_value = format_proposal_value_display(proposal_value)

    servico_value = normalize_text(servico) or normalize_text(commercial.get("servico", "Não informado"))
    colaboradores_value = normalize_text(colaboradores) or normalize_text(commercial.get("colaboradores", "Não informado"))

    now = pd.Timestamp.now(tz="America/Sao_Paulo")
    proposal_number = f"OPPI-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

    canonical = {
        "{{EMPRESA}}": resolved_company or "Não informado",
        "{{VALOR_PROPOSTA}}": proposal_value,
        "{{SERVICO}}": servico_value,
        "{{VENDEDOR}}": row_field_value(row, columns, "vendedor") or normalize_text(row_get(row, "_vendedor", "")) or "Sem vendedor",
        "{{DATA}}": now.strftime("%d/%m/%Y"),
        "{{NUMERO_PROPOSTA}}": proposal_number,
        "{{CNPJ}}": row_field_value(row, columns, "cnpj") or "Não informado",
        "{{TELEFONE}}": row_contact_phone(row, columns) or "Não informado",
        "{{EMAIL}}": row_contact_email(row, columns) or "Não informado",
        "{{COLABORADORES}}": colaboradores_value,
        "{{ENDERECO}}": row_field_value(row, columns, "endereco") or "Não informado",
    }

    values: dict[str, str] = {}
    for key, aliases in PLACEHOLDER_ALIASES.items():
        replacement = canonical[key]
        for alias in aliases:
            values[alias] = replacement
    return values


def _canonical_values(values: dict[str, str]) -> dict[str, str]:
    return {field_key: values[field_key] for field_key in PLACEHOLDER_ALIASES}


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _placeholder_xml_pattern(placeholder: str) -> re.Pattern[str]:
    parts = [re.escape(char) for char in placeholder]
    return re.compile(r"(?:<[^>]+>)*".join(parts))


def _replace_placeholders_in_docx(docx_bytes: bytes, values: dict[str, str]) -> bytes:
    input_buffer = io.BytesIO(docx_bytes)
    output_buffer = io.BytesIO()

    with zipfile.ZipFile(input_buffer, "r") as source, zipfile.ZipFile(output_buffer, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                text = content.decode("utf-8")
                for placeholder, replacement in values.items():
                    safe_replacement = _escape_xml(replacement or "—")
                    text = _placeholder_xml_pattern(placeholder).sub(safe_replacement, text)
                    if placeholder in text:
                        text = text.replace(placeholder, safe_replacement)
                content = text.encode("utf-8")
            target.writestr(item, content)

    return output_buffer.getvalue()


def _parse_drive_error(response) -> str:
    raw = normalize_text(getattr(response, "text", ""))
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
            error = payload.get("error", {})
            message = normalize_text(error.get("message"))
            if message:
                if "docs api has not been used" in message.lower():
                    return (
                        "A API Google Docs não está habilitada no projeto Google Cloud. "
                        "O sistema tentará gerar o PDF pelo modelo exportado."
                    )
                return message[:240]
        except Exception:
            pass

    if response.status_code == 403:
        return "Sem permissão no Google Drive. Verifique o compartilhamento do modelo."
    if response.status_code == 404:
        return "Modelo Google Docs não encontrado. Confira o link em Configurações → Geral."
    if raw:
        return raw[:240]
    return "Erro desconhecido ao acessar o Google Drive."


def _export_document_docx(session: AuthorizedSession, document_id: str) -> bytes:
    response = session.get(
        f"https://www.googleapis.com/drive/v3/files/{document_id}/export",
        params={"mimeType": DOCX_MIME, **DRIVE_PARAMS},
        timeout=90,
    )
    if response.status_code >= 400:
        raise RuntimeError(_parse_drive_error(response))
    return response.content


def _export_document_pdf(session: AuthorizedSession, document_id: str) -> bytes:
    response = session.get(
        f"https://www.googleapis.com/drive/v3/files/{document_id}/export",
        params={"mimeType": "application/pdf", **DRIVE_PARAMS},
        timeout=90,
    )
    if response.status_code >= 400:
        raise RuntimeError(_parse_drive_error(response))
    return response.content


def _upload_docx_and_export_pdf(session: AuthorizedSession, docx_bytes: bytes, title: str) -> bytes:
    metadata = {
        "name": title[:120],
        "mimeType": "application/vnd.google-apps.document",
    }
    upload_response = session.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType": "multipart", **DRIVE_PARAMS},
        files={
            "metadata": ("metadata", json.dumps(metadata), "application/json; charset=UTF-8"),
            "file": ("proposta.docx", docx_bytes, DOCX_MIME),
        },
        timeout=120,
    )
    if upload_response.status_code >= 400:
        raise RuntimeError(_parse_drive_error(upload_response))

    file_id = upload_response.json().get("id")
    if not file_id:
        raise RuntimeError("Google Drive não retornou o arquivo temporário da proposta.")

    try:
        return _export_document_pdf(session, file_id)
    finally:
        session.delete(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params=DRIVE_PARAMS,
            timeout=30,
        )


def _libreoffice_binary() -> str | None:
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _convert_docx_to_pdf(session: AuthorizedSession, docx_bytes: bytes, title: str) -> bytes:
    binary = _libreoffice_binary()
    if binary:
        with tempfile.TemporaryDirectory(prefix="oppi-proposal-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            docx_path = tmp_path / "proposta.docx"
            docx_path.write_bytes(docx_bytes)

            result = subprocess.run(
                [binary, "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(docx_path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode == 0:
                pdf_path = tmp_path / "proposta.pdf"
                if pdf_path.exists():
                    return pdf_path.read_bytes()

    return _upload_docx_and_export_pdf(session, docx_bytes, title)


def _generate_reportlab_fallback(canonical: dict[str, str]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Proposta - {canonical['{{EMPRESA}}']}",
        author="Oppi Comercial",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ProposalTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=colors.HexColor("#6D28D9"),
        spaceAfter=8,
    )
    label_style = ParagraphStyle(
        "ProposalLabel",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=colors.HexColor("#271B35"),
    )
    value_style = ParagraphStyle(
        "ProposalValue",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.HexColor("#2B2237"),
        spaceAfter=6,
    )

    rows = [
        ("Empresa", canonical["{{EMPRESA}}"]),
        ("Serviço", canonical["{{SERVICO}}"]),
        ("Valor da proposta", canonical["{{VALOR_PROPOSTA}}"]),
        ("Colaboradores", canonical["{{COLABORADORES}}"]),
        ("Vendedor", canonical["{{VENDEDOR}}"]),
        ("Data", canonical["{{DATA}}"]),
        ("Número da proposta", canonical["{{NUMERO_PROPOSTA}}"]),
        ("CNPJ", canonical["{{CNPJ}}"]),
        ("Telefone", canonical["{{TELEFONE}}"]),
        ("E-mail", canonical["{{EMAIL}}"]),
        ("Endereço", canonical["{{ENDERECO}}"]),
    ]

    story = [
        Paragraph("Proposta Comercial Oppi", title_style),
        Spacer(1, 6 * mm),
        Paragraph(
            "PDF gerado automaticamente. Para usar o layout completo do Google Docs, "
            "garanta que o modelo esteja compartilhado com a service account.",
            value_style,
        ),
        Spacer(1, 8 * mm),
    ]

    table_data = [[Paragraph("Campo", label_style), Paragraph("Valor", label_style)]]
    for label, value in rows:
        table_data.append([Paragraph(label, label_style), Paragraph(value or "—", value_style)])

    table = Table(table_data, colWidths=[55 * mm, 115 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE9FE")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8B4FE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    doc.build(story)
    return buffer.getvalue()


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
    values = build_proposal_placeholder_values(
        resolved_company,
        df,
        columns,
        value=value,
        servico=servico,
        colaboradores=colaboradores,
    )
    canonical = _canonical_values(values)
    session = _google_session()

    try:
        docx_bytes = _export_document_docx(session, template_id)
        filled_docx = _replace_placeholders_in_docx(docx_bytes, values)
        return _convert_docx_to_pdf(session, filled_docx, f"Proposta {resolved_company or 'Cliente'}")
    except Exception as template_error:
        try:
            return _generate_reportlab_fallback(canonical)
        except Exception as fallback_error:
            raise RuntimeError(
                f"Não foi possível gerar o PDF pelo modelo Google Docs ({template_error}). "
                f"Fallback local também falhou ({fallback_error})."
            ) from fallback_error


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
        message = str(error)
        if "Não foi possível gerar o PDF" not in message:
            service_email = _service_account_email()
            hint = f" Compartilhe o documento com {service_email}." if service_email else ""
            raise RuntimeError(
                "Não foi possível gerar o PDF a partir do modelo Google Docs."
                f"{hint} Detalhe: {message}"
            ) from error
        raise

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
        return cache_key, _format_pdf_generation_error(error)
    return cache_key, None


def _format_pdf_generation_error(error: Exception) -> str:
    message = normalize_text(str(error))
    if "truth value of a series is ambiguous" in message.lower():
        return (
            "Erro ao ler os dados da planilha para montar a proposta. "
            "Atualize o sistema e tente novamente."
        )
    for prefix in (
        "Não foi possível gerar o PDF a partir do modelo Google Docs.",
        "Detalhe: ",
    ):
        message = message.replace(prefix, " ")
    if message.startswith("{"):
        try:
            payload = json.loads(message)
            api_message = normalize_text(payload.get("error", {}).get("message"))
            if api_message:
                return api_message[:500]
        except Exception:
            pass
    return normalize_text(message)[:500]


def proposal_pdf_filename(company_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", normalize_text(company_name)).strip("_") or "cliente"
    return f"Proposta_{safe}.pdf"
