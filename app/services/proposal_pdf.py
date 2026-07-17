"""Geração de PDF de proposta a partir de modelo Google Docs."""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

import pandas as pd
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from app.services.app_settings import get_proposal_pdf_folder_id, get_proposal_template_doc_id
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
    "https://www.googleapis.com/auth/documents",
]

DRIVE_PARAMS = {"supportsAllDrives": "true"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TEMPLATE_LOCK = threading.Lock()
DOCS_API_ENABLE_URL = (
    "https://console.cloud.google.com/apis/library/docs.googleapis.com"
    "?project=oppi-comercial-dashboard"
)

PLACEHOLDER_ALIASES = {
    "{{EMPRESA}}": ["{{EMPRESA}}", "{{empresa}}", "{{NOME_EMPRESA}}", "{{nome_empresa}}", "{{CONTRATANTE}}", "{{contratante}}"],
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
    "{{RUA}}": ["{{RUA}}", "{{rua}}"],
    "{{BAIRRO_CEP}}": ["{{BAIRRO_CEP}}", "{{bairro_cep}}"],
}

CONTRATANTE_LABEL_FILLS = (
    ("CONTRATANTE", "{{EMPRESA}}"),
    ("CNPJ", "{{CNPJ}}"),
    ("Rua", "{{RUA}}"),
    ("Bairro: CEP", "{{BAIRRO_CEP}}"),
    ("E-mail", "{{EMAIL}}"),
)


def _cep_from_endereco(endereco: str) -> str:
    text = normalize_text(endereco)
    if not text or text == "Não informado":
        return "Não informado"
    match = re.search(r"\b(\d{5}-?\d{3})\b", text)
    return match.group(1) if match else "Não informado"


def _build_contratante_docs_replacements(extended: dict[str, str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    empresa = extended.get("{{EMPRESA}}", "—")
    cnpj = extended.get("{{CNPJ}}", "—")
    rua = extended.get("{{RUA}}", "—")
    cep = _cep_from_endereco(extended.get("{{ENDERECO}}", ""))
    email = extended.get("{{EMAIL}}", "—")

    forward = [
        ("CONTRATANTE:", f"CONTRATANTE: {empresa}"),
        ("CNPJ:\nRua:", f"CNPJ: {cnpj}\nRua:"),
        ("Rua:\nBairro: CEP:", f"Rua: {rua}\nBairro: CEP:"),
        ("Bairro: CEP:\nE-mail:", f"Bairro: CEP: {cep}\nE-mail:"),
        ("E-mail:\nCONTRATADO:", f"E-mail: {email}\nCONTRATADO:"),
    ]
    restore = [(new, old) for old, new in reversed(forward)]
    return forward, restore


def _build_placeholder_docs_replacements(extended: dict[str, str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    forward: list[tuple[str, str]] = []
    restore: list[tuple[str, str]] = []
    for key, aliases in PLACEHOLDER_ALIASES.items():
        replacement = extended.get(key, "—") or "—"
        for alias in aliases:
            if alias.startswith("{{"):
                forward.append((alias, replacement))
                restore.append((replacement, alias))
    restore.reverse()
    return forward, restore


def _batch_replace_pairs(
    session: AuthorizedSession,
    document_id: str,
    pairs: list[tuple[str, str]],
) -> None:
    if not pairs:
        return

    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": old_text, "matchCase": True},
                "replaceText": new_text,
            }
        }
        for old_text, new_text in pairs
    ]
    response = session.post(
        f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate",
        json={"requests": requests},
        timeout=90,
    )
    if response.status_code >= 400:
        message = _parse_drive_error(response)
        raise RuntimeError(message)


def _generate_via_google_docs_inplace(
    session: AuthorizedSession,
    template_id: str,
    canonical: dict[str, str],
) -> bytes:
    extended = _extend_canonical(canonical)
    contratante_forward, contratante_restore = _build_contratante_docs_replacements(extended)
    placeholder_forward, placeholder_restore = _build_placeholder_docs_replacements(extended)
    forward = contratante_forward + placeholder_forward
    restore = placeholder_restore + contratante_restore

    with _TEMPLATE_LOCK:
        _batch_replace_pairs(session, template_id, forward)
        try:
            return _export_document_pdf(session, template_id)
        finally:
            _batch_replace_pairs(session, template_id, restore)


def _docs_api_disabled_message() -> str:
    return (
        "Ative a Google Docs API no projeto Google Cloud para exportar o PDF "
        f"com o layout original do modelo: {DOCS_API_ENABLE_URL}"
    )


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
        normalize_text(os.environ.get("APP_BUILD", "")),
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


def build_form_fallback_placeholder_values(
    company_name: str,
    df: pd.DataFrame,
    columns: dict,
    *,
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
) -> dict[str, str]:
    resolved_company = normalize_text(company_name)
    row = None
    try:
        row = find_prepared_company_row(company_name, df)
    except Exception:
        row = None

    def safe_field(key: str) -> str:
        try:
            text = row_field_value(row, columns, key)
            return text or "Não informado"
        except Exception:
            return "Não informado"

    now = pd.Timestamp.now(tz="America/Sao_Paulo")
    proposal_number = f"OPPI-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
    canonical = {
        "{{EMPRESA}}": resolved_company or "Não informado",
        "{{VALOR_PROPOSTA}}": format_proposal_value_display(value) if normalize_text(value) else "Não informado",
        "{{SERVICO}}": normalize_text(servico) or safe_field("servico"),
        "{{VENDEDOR}}": safe_field("vendedor"),
        "{{DATA}}": now.strftime("%d/%m/%Y"),
        "{{NUMERO_PROPOSTA}}": proposal_number,
        "{{CNPJ}}": safe_field("cnpj"),
        "{{TELEFONE}}": row_contact_phone(row, columns) or "Não informado",
        "{{EMAIL}}": row_contact_email(row, columns) or "Não informado",
        "{{COLABORADORES}}": normalize_text(colaboradores) or safe_field("colaboradores"),
        "{{ENDERECO}}": safe_field("endereco"),
    }
    return _values_from_canonical(canonical)


def _address_parts(endereco: str) -> dict[str, str]:
    text = normalize_text(endereco)
    if not text or text == "Não informado":
        return {"rua": "Não informado", "bairro_cep": "Não informado"}

    cep_match = re.search(r"\b(\d{5}-?\d{3})\b", text)
    cep = cep_match.group(1) if cep_match else ""
    rua = text
    if cep:
        rua = normalize_text(text.replace(cep, "")).strip(" ,-")
    bairro_cep = f"CEP: {cep}" if cep else "Não informado"
    return {"rua": rua or text, "bairro_cep": bairro_cep}


def _extend_canonical(canonical: dict[str, str]) -> dict[str, str]:
    extended = dict(canonical)
    parts = _address_parts(extended.get("{{ENDERECO}}", ""))
    extended["{{RUA}}"] = parts["rua"]
    extended["{{BAIRRO_CEP}}"] = parts["bairro_cep"]
    extended["{{CONTRATANTE}}"] = extended.get("{{EMPRESA}}", "")
    return extended


def _values_from_canonical(canonical: dict[str, str]) -> dict[str, str]:
    extended = _extend_canonical(canonical)
    values: dict[str, str] = {}
    for key, aliases in PLACEHOLDER_ALIASES.items():
        replacement = extended.get(key, "")
        for alias in aliases:
            values[alias] = replacement
    return values


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
    return _values_from_canonical(canonical)


def _canonical_values(values: dict[str, str]) -> dict[str, str]:
    extended = _extend_canonical({field_key: values.get(field_key, "") for field_key in PLACEHOLDER_ALIASES})
    return extended


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


def _find_marker_index(xml: str, marker: str) -> int:
    pattern = _placeholder_xml_pattern(marker)
    match = pattern.search(xml)
    return match.start() if match else len(xml)


def _fill_label_in_xml(xml: str, label: str, value: str, *, stop_before: str = "CONTRATADO") -> str:
    safe = _escape_xml(normalize_text(value) or "—")
    stop_idx = _find_marker_index(xml, stop_before)
    head, tail = xml[:stop_idx], xml[stop_idx:]
    pattern = _placeholder_xml_pattern(f"{label}:")
    head, _ = pattern.subn(lambda m: f"{m.group(0)} {safe}", head, count=1)
    return head + tail


def _apply_contratante_label_fills(xml: str, canonical: dict[str, str]) -> str:
    extended = _extend_canonical(canonical)
    for label, field_key in CONTRATANTE_LABEL_FILLS:
        xml = _fill_label_in_xml(xml, label, extended.get(field_key, ""))
    return xml


def _replace_placeholders_in_docx(docx_bytes: bytes, values: dict[str, str], canonical: dict[str, str] | None = None) -> bytes:
    input_buffer = io.BytesIO(docx_bytes)
    output_buffer = io.BytesIO()
    label_canonical = canonical or _canonical_values(values)

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
                text = _apply_contratante_label_fills(text, label_canonical)
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
                    return _docs_api_disabled_message()
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


def _get_template_drive_metadata(session: AuthorizedSession, template_id: str | None) -> dict:
    if not template_id:
        return {}
    response = session.get(
        f"https://www.googleapis.com/drive/v3/files/{template_id}",
        params={"fields": "parents,driveId", **DRIVE_PARAMS},
        timeout=30,
    )
    if response.status_code >= 400:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        return {}
    return payload


def _resolve_proposal_upload_folder(session: AuthorizedSession, template_id: str | None) -> str:
    configured = get_proposal_pdf_folder_id()
    if configured:
        return configured
    metadata = _get_template_drive_metadata(session, template_id)
    if not metadata.get("driveId"):
        return ""
    parents = metadata.get("parents") or []
    return normalize_text(parents[0]) if parents else ""


def _can_use_drive_pdf_fallback(session: AuthorizedSession, template_id: str | None) -> bool:
    if get_proposal_pdf_folder_id():
        return True
    metadata = _get_template_drive_metadata(session, template_id)
    return bool(metadata.get("driveId"))


def cleanup_service_account_proposal_files(*, keep_template_id: str | None = None) -> int:
    """Remove arquivos temporários de proposta da conta de serviço para liberar cota."""
    keep_template_id = keep_template_id or get_proposal_template_doc_id()
    session = _google_session()
    deleted = 0
    page_token = ""
    while True:
        params = {
            "pageSize": 100,
            "fields": "nextPageToken,files(id,name,mimeType)",
            "q": "trashed=false and 'me' in owners and name contains 'Proposta'",
            "spaces": "drive",
            **DRIVE_PARAMS,
        }
        if page_token:
            params["pageToken"] = page_token
        response = session.get("https://www.googleapis.com/drive/v3/files", params=params, timeout=60)
        if response.status_code >= 400:
            break
        payload = response.json()
        for item in payload.get("files", []):
            file_id = normalize_text(item.get("id"))
            if not file_id or file_id == keep_template_id:
                continue
            delete_response = session.delete(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params=DRIVE_PARAMS,
                timeout=30,
            )
            if delete_response.status_code < 400:
                deleted += 1
        page_token = normalize_text(payload.get("nextPageToken"))
        if not page_token:
            break
    return deleted


def check_pdf_engine_status() -> dict[str, str | bool]:
    binary = _libreoffice_binary()
    return {
        "libreoffice_installed": bool(binary),
        "libreoffice_path": binary or "",
        "app_build": normalize_text(os.environ.get("APP_BUILD", "")),
    }


def _upload_docx_and_export_pdf(
    session: AuthorizedSession,
    docx_bytes: bytes,
    title: str,
    *,
    template_id: str | None = None,
) -> bytes:
    folder_id = _resolve_proposal_upload_folder(session, template_id)
    if not folder_id:
        raise RuntimeError(
            "Conversão pelo Google Drive indisponível sem pasta em Shared Drive. "
            "Use rebuild com LibreOffice ou configure PROPOSAL_PDF_FOLDER_ID."
        )

    cleanup_service_account_proposal_files(keep_template_id=template_id or get_proposal_template_doc_id())

    metadata: dict[str, object] = {
        "name": title[:120],
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
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
        message = _parse_drive_error(upload_response)
        if "storage quota" in message.lower():
            raise RuntimeError(
                "A cota de armazenamento do Google Drive foi excedida. "
                "Mova o modelo para um Shared Drive ou configure PROPOSAL_PDF_FOLDER_ID."
            )
        raise RuntimeError(message)

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
    for candidate in ("soffice", "libreoffice", "/usr/bin/soffice", "/usr/bin/libreoffice"):
        if candidate.startswith("/"):
            path = Path(candidate)
            if path.is_file():
                return str(path)
            continue
        path = shutil.which(candidate)
        if path:
            return path

    nix_root = Path("/nix/store")
    if nix_root.is_dir():
        nix_candidates: list[Path] = []
        nix_candidates.extend(nix_root.glob("*-libreoffice-*/bin/soffice"))
        nix_candidates.extend(nix_root.glob("*libreoffice*-wrapped/bin/soffice"))
        nix_candidates.extend(nix_root.glob("*/bin/soffice"))
        for path in sorted({candidate for candidate in nix_candidates if candidate.is_file()}, reverse=True):
            return str(path)
    return None


def _convert_docx_with_libreoffice(docx_bytes: bytes) -> tuple[bytes | None, str]:
    binary = _libreoffice_binary()
    if not binary:
        return None, "LibreOffice não está instalado no servidor."

    with tempfile.TemporaryDirectory(prefix="oppi-proposal-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        docx_path = tmp_path / "proposta.docx"
        docx_path.write_bytes(docx_bytes)
        env = {
            **os.environ,
            "HOME": tmp_dir,
            "TMPDIR": tmp_dir,
            "SAL_USE_VCLPLUGIN": "gen",
            "LANG": "C.UTF-8",
        }
        profile_dir = tmp_path / "lo-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                binary,
                f"-env:UserInstallation=file://{profile_dir.as_posix()}",
                "--headless",
                "--invisible",
                "--norestore",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(tmp_path),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env=env,
        )
        pdf_path = tmp_path / "proposta.pdf"
        if result.returncode == 0 and pdf_path.exists():
            return pdf_path.read_bytes(), ""
        lo_error = normalize_text(result.stderr or result.stdout)[:240]
        return None, lo_error or f"LibreOffice retornou código {result.returncode}."


def _convert_docx_to_pdf(
    session: AuthorizedSession,
    docx_bytes: bytes,
    title: str,
    *,
    template_id: str | None = None,
) -> bytes:
    del session, title, template_id
    pdf_bytes, lo_error = _convert_docx_with_libreoffice(docx_bytes)
    if pdf_bytes:
        return pdf_bytes
    raise RuntimeError(
        "Não foi possível converter o modelo para PDF com LibreOffice. "
        f"Detalhe: {lo_error or 'LibreOffice indisponível'}."
    )


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

    resolved_company = normalize_text(company_name)
    try:
        values = build_proposal_placeholder_values(
            company_name,
            df,
            columns,
            value=value,
            servico=servico,
            colaboradores=colaboradores,
        )
    except Exception:
        values = build_form_fallback_placeholder_values(
            company_name,
            df,
            columns,
            value=value,
            servico=servico,
            colaboradores=colaboradores,
        )

    canonical = _canonical_values(values)
    session = _google_session()
    docs_error: Exception | None = None

    try:
        return _generate_via_google_docs_inplace(session, template_id, canonical)
    except Exception as error:
        docs_error = error

    docx_bytes = _export_document_docx(session, template_id)
    filled_docx = _replace_placeholders_in_docx(docx_bytes, values, canonical)
    try:
        return _convert_docx_to_pdf(
            session,
            filled_docx,
            f"Proposta {resolved_company or 'Cliente'}",
            template_id=template_id,
        )
    except Exception as lo_error:
        docs_detail = normalize_text(str(docs_error))[:220] if docs_error else ""
        lo_detail = normalize_text(str(lo_error))[:220]
        raise RuntimeError(
            "Não foi possível gerar o PDF com o layout do modelo Google Docs. "
            f"Google Docs: {docs_detail or 'indisponível'}. "
            f"LibreOffice: {lo_detail or 'indisponível'}."
        ) from lo_error


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

    pdf_bytes = generate_proposal_pdf_from_template(
        company_name,
        df,
        columns,
        value=value,
        servico=servico,
        colaboradores=colaboradores,
    )

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
