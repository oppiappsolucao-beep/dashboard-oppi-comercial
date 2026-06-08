import base64
import html
import json
import re
import unicodedata
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from google.oauth2.service_account import Credentials
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound


# =========================================================
# CONFIGURAÇÕES GERAIS
# =========================================================
st.set_page_config(
    page_title="Dashboard Oppi Comercial",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

SHEET_ID = "1GAbrca0NSiJfPXaSte1qGxXCsGkQPacoRsm0PVB51gE"
WORKSHEET_NAME = "Folha1"
CACHE_TTL_SECONDS = 120

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

STATUS_OPTIONS = [
    "Novo Lead",
    "Conversando",
    "Sem interesse",
    "Não responde",
    "Proposta",
    "Reunião",
    "Fechado",
]

STATUS_COLORS = {
    "Novo Lead": ("#E8F0FF", "#5C8BFF"),
    "Conversando": ("#F8EFE6", "#B37A2A"),
    "Sem interesse": ("#E9F8FA", "#2F9FB3"),
    "Não responde": ("#FBECEF", "#DA5C78"),
    "Fechado": ("#EAF8EF", "#58B97A"),
    "Proposta": ("#EAF2FF", "#5C9DFF"),
    "Reunião": ("#F3EAFE", "#A65BDB"),
}


# =========================================================
# ESTADO DA SESSÃO
# =========================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "auth_error" not in st.session_state:
    st.session_state.auth_error = ""

if "selected_page" not in st.session_state:
    st.session_state.selected_page = "Visão Geral"

if "selected_cadastro_subpage" not in st.session_state:
    st.session_state.selected_cadastro_subpage = "Novo contrato"

if "selected_contract_sheet_row" not in st.session_state:
    st.session_state.selected_contract_sheet_row = None

if "navigation_session_token" not in st.session_state:
    st.session_state.navigation_session_token = ""


# =========================================================
# UTILITÁRIOS
# =========================================================
def render_html(content: str) -> None:
    """Renderiza HTML sem que o Streamlit o transforme em bloco de código."""
    clean_content = " ".join(
        line.strip()
        for line in content.splitlines()
        if line.strip()
    )
    st.markdown(clean_content, unsafe_allow_html=True)


def normalize_text(value) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def normalize_search_text(value) -> str:
    text = normalize_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", text).strip()


def parse_money(value) -> float:
    text = normalize_text(value)

    if not text:
        return 0.0

    text = text.replace("R$", "").replace(" ", "")

    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")

    try:
        return float(text)
    except Exception:
        return 0.0


def format_money(value) -> str:
    try:
        number = float(value)
    except Exception:
        number = 0.0

    return (
        f"R$ {number:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def parse_date(value):
    text = normalize_text(value)

    if not text:
        return pd.NaT

    return pd.to_datetime(text, errors="coerce", dayfirst=True)


def make_unique_headers(headers: list[str]) -> list[str]:
    result = []
    counter = {}

    for index, header in enumerate(headers):
        clean_header = normalize_text(header)

        if not clean_header:
            clean_header = f"Coluna {index + 1}"

        if clean_header in counter:
            counter[clean_header] += 1
            clean_header = f"{clean_header}_{counter[clean_header]}"
        else:
            counter[clean_header] = 1

        result.append(clean_header)

    return result


def first_existing_column(
    df: pd.DataFrame,
    possible_names: list[str],
) -> Optional[str]:
    normalized_columns = {
        normalize_search_text(column): column
        for column in df.columns
    }

    for name in possible_names:
        normalized_name = normalize_search_text(name)

        if normalized_name in normalized_columns:
            return normalized_columns[normalized_name]

    return None


def safe_series(
    df: pd.DataFrame,
    column: Optional[str],
    default_value="",
) -> pd.Series:
    if column and column in df.columns:
        return df[column]

    return pd.Series(
        [default_value] * len(df),
        index=df.index,
    )


def status_group(value: str) -> str:
    status = normalize_search_text(value)

    if not status:
        return "Novo Lead"

    if "reuniao" in status:
        return "Reunião"

    if "proposta" in status:
        return "Proposta"

    if any(word in status for word in ["fechado", "ganho", "cliente"]):
        return "Fechado"

    if any(word in status for word in ["nao responde", "sem resposta"]):
        return "Não responde"

    if any(word in status for word in ["sem interesse", "nao tem interesse"]):
        return "Sem interesse"

    if any(word in status for word in ["chamando", "conversando", "contato", "negoci", "andamento"]):
        return "Conversando"

    if any(word in status for word in ["novo", "lead"]):
        return "Novo Lead"

    return normalize_text(value)


def calculate_score(row: pd.Series, columns: dict) -> int:
    score = 0

    if normalize_text(row.get(columns.get("telefone_b2b", ""), "")):
        score += 15

    if normalize_text(row.get(columns.get("email", ""), "")):
        score += 10

    if normalize_text(row.get(columns.get("site", ""), "")):
        score += 10

    if normalize_text(row.get(columns.get("instagram", ""), "")):
        score += 10

    if normalize_text(row.get(columns.get("linkedin", ""), "")):
        score += 5

    if normalize_text(row.get(columns.get("socio_1", ""), "")):
        score += 10

    capital_value = parse_money(row.get(columns.get("capital", ""), ""))

    if capital_value >= 100000:
        score += 20
    elif capital_value >= 50000:
        score += 15
    elif capital_value > 0:
        score += 8

    grouped_status = status_group(row.get(columns.get("status", ""), ""))

    if grouped_status == "Fechado":
        score += 20
    elif grouped_status == "Proposta":
        score += 16
    elif grouped_status == "Reunião":
        score += 14
    elif grouped_status == "Conversando":
        score += 12
    elif grouped_status == "Novo Lead":
        score += 6

    return min(score, 100)


def score_classification(score: int) -> str:
    if score >= 70:
        return "Lead Quente"

    if score >= 40:
        return "Lead Morno"

    return "Lead Frio"




def get_logo_data_uri() -> str:
    """Usa a logo exatamente como a imagem original, sem remover fundo nem aplicar recorte."""
    possible_paths = [
        Path(__file__).parent / "logo_oppi.png",
        Path(__file__).parent / "logo.png",
        Path(__file__).parent / "assets" / "logo_oppi.png",
        Path(__file__).parent / "assets" / "logo.png",
    ]

    for file_path in possible_paths:
        if file_path.exists():
            mime = "image/png" if file_path.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
            return f"data:{mime};base64,{encoded}"

    return ""
# =========================================================
# CONEXÃO COM GOOGLE SHEETS
# =========================================================
@st.cache_resource
def get_gsheet_client():
    try:
        credentials_info = dict(st.secrets["gcp_service_account"])
    except Exception as error:
        raise RuntimeError(
            "Não encontrei a seção [gcp_service_account] nos Secrets do Streamlit."
        ) from error

    required_fields = [
        "type",
        "project_id",
        "private_key_id",
        "private_key",
        "client_email",
        "client_id",
        "auth_uri",
        "token_uri",
        "auth_provider_x509_cert_url",
        "client_x509_cert_url",
    ]

    missing_fields = [
        field
        for field in required_fields
        if not normalize_text(credentials_info.get(field, ""))
    ]

    if missing_fields:
        raise RuntimeError(
            "Estão faltando campos nos Secrets: " + ", ".join(missing_fields)
        )

    credentials_info["private_key"] = (
        str(credentials_info["private_key"])
        .replace("\\n", "\n")
        .strip()
        + "\n"
    )

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=SCOPES,
    )

    return gspread.authorize(credentials)


@st.cache_data(show_spinner=False, ttl=CACHE_TTL_SECONDS)
def load_sheet_data() -> pd.DataFrame:
    client = get_gsheet_client()
    spreadsheet = client.open_by_key(SHEET_ID)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    values = worksheet.get_all_values()

    if not values:
        return pd.DataFrame()

    headers = make_unique_headers(values[0])
    rows = values[1:]

    df = pd.DataFrame(rows, columns=headers)
    df["_sheet_row"] = list(range(2, len(rows) + 2))

    for column in df.columns:
        if column != "_sheet_row":
            df[column] = df[column].astype(str).str.strip()

    data_columns = [column for column in df.columns if column != "_sheet_row"]
    df = df[
        df[data_columns].apply(
            lambda row: any(normalize_text(value) for value in row),
            axis=1,
        )
    ].copy()

    return df.reset_index(drop=True)


def update_statuses_in_sheet(
    changes: list[dict],
    status_column_name: str,
    updated_at_column_name: Optional[str] = None,
) -> None:
    """Atualiza os status editados diretamente na planilha do Google Sheets."""
    if not changes:
        return

    client = get_gsheet_client()
    spreadsheet = client.open_by_key(SHEET_ID)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    headers = worksheet.row_values(1)

    if status_column_name not in headers:
        raise RuntimeError(
            f"Não encontrei a coluna '{status_column_name}' na planilha."
        )

    status_column_index = headers.index(status_column_name) + 1
    updated_at_column_index = None

    if updated_at_column_name and updated_at_column_name in headers:
        updated_at_column_index = headers.index(updated_at_column_name) + 1

    cells = []
    now_text = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d/%m/%Y %H:%M")

    for change in changes:
        sheet_row = int(change["sheet_row"])
        new_status = normalize_text(change["status"])

        if new_status not in STATUS_OPTIONS:
            raise RuntimeError(f"Status inválido: {new_status}")

        cells.append(gspread.Cell(sheet_row, status_column_index, new_status))

        if updated_at_column_index:
            cells.append(gspread.Cell(sheet_row, updated_at_column_index, now_text))

    worksheet.update_cells(cells, value_input_option="USER_ENTERED")
    st.cache_data.clear()


def _set_sheet_value_by_header(
    row_values: list[str],
    headers: list[str],
    aliases: list[str],
    value,
    occurrence: int = 1,
) -> bool:
    """Preenche uma coluna da planilha procurando pelo nome do cabeçalho."""
    normalized_aliases = {normalize_search_text(alias) for alias in aliases}
    found = 0

    for index, header in enumerate(headers):
        if normalize_search_text(header) in normalized_aliases:
            found += 1

            if found == occurrence:
                row_values[index] = normalize_text(value)
                return True

    return False


class DuplicateRegistrationError(RuntimeError):
    """Impede o cadastro quando telefone, CPF ou CNPJ já existe na planilha."""


def normalize_digits(value) -> str:
    """Mantém somente números para comparar telefones e CPFs independentemente da máscara."""
    return re.sub(r"\D", "", normalize_text(value))


def normalize_phone_for_duplicate(value) -> str:
    """Normaliza telefone brasileiro, removendo DDI 55 quando informado."""
    digits = normalize_digits(value)

    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]

    return digits if len(digits) >= 8 else ""


def normalize_cpf_for_duplicate(value) -> str:
    """Normaliza CPF para comparação, ignorando campos vazios ou incompletos."""
    digits = normalize_digits(value)
    return digits if len(digits) == 11 else ""


def normalize_cnpj_for_duplicate(value) -> str:
    """Normaliza CNPJ para comparação, ignorando campos vazios ou incompletos."""
    digits = normalize_digits(value)
    return digits if len(digits) == 14 else ""


def _header_matches_any(header: str, aliases: list[str]) -> bool:
    normalized_header = normalize_search_text(header)
    return any(alias in normalized_header for alias in aliases)


def validate_unique_company_registration(payload: dict, worksheet) -> None:
    """
    Bloqueia novo cadastro quando qualquer telefone, CPF ou CNPJ informado já existe
    em qualquer coluna correspondente da planilha. A leitura é feita diretamente
    da aba para evitar duplicidade mesmo quando o cache ainda não atualizou.
    """
    values = worksheet.get_all_values()

    if not values:
        return

    headers = values[0]
    rows = values[1:]

    phone_column_indexes = [
        index
        for index, header in enumerate(headers)
        if _header_matches_any(header, ["telefone", "celular", "whatsapp", "fone"])
    ]

    cpf_column_indexes = [
        index
        for index, header in enumerate(headers)
        if normalize_search_text(header) == "cpf"
        or _header_matches_any(header, ["cpf do", "cpf socio", "cpf sócio"])
    ]

    cnpj_column_indexes = [
        index
        for index, header in enumerate(headers)
        if normalize_search_text(header) == "cnpj"
        or _header_matches_any(header, ["cnpj da empresa", "cnpj empresa"])
    ]

    submitted_phones = {
        normalize_phone_for_duplicate(payload.get(field))
        for field in [
            "telefone_b2b",
            "telefone_fixo",
            "telefone_alternativo",
            "telefone_socio_1",
        ]
    }
    submitted_phones.discard("")

    submitted_cpfs = {
        normalize_cpf_for_duplicate(payload.get(field))
        for field in [
            "cpf_socio_1",
            "cpf_socio_2",
            "cpf_socio_3",
        ]
    }
    submitted_cpfs.discard("")

    submitted_cnpjs = {
        normalize_cnpj_for_duplicate(payload.get("cnpj"))
    }
    submitted_cnpjs.discard("")

    duplicate_phones = set()
    duplicate_cpfs = set()
    duplicate_cnpjs = set()

    for row in rows:
        for index in phone_column_indexes:
            if index >= len(row):
                continue

            existing_phone = normalize_phone_for_duplicate(row[index])

            if existing_phone and existing_phone in submitted_phones:
                duplicate_phones.add(existing_phone)

        for index in cpf_column_indexes:
            if index >= len(row):
                continue

            existing_cpf = normalize_cpf_for_duplicate(row[index])

            if existing_cpf and existing_cpf in submitted_cpfs:
                duplicate_cpfs.add(existing_cpf)

        for index in cnpj_column_indexes:
            if index >= len(row):
                continue

            existing_cnpj = normalize_cnpj_for_duplicate(row[index])

            if existing_cnpj and existing_cnpj in submitted_cnpjs:
                duplicate_cnpjs.add(existing_cnpj)

    if not duplicate_phones and not duplicate_cpfs and not duplicate_cnpjs:
        return

    messages = []

    if duplicate_phones:
        phones_text = ", ".join(sorted(duplicate_phones))
        messages.append(f"Telefone já cadastrado: {phones_text}")

    if duplicate_cpfs:
        cpfs_text = ", ".join(sorted(duplicate_cpfs))
        messages.append(f"CPF já cadastrado: {cpfs_text}")

    if duplicate_cnpjs:
        cnpjs_text = ", ".join(sorted(duplicate_cnpjs))
        messages.append(f"CNPJ já cadastrado: {cnpjs_text}")

    raise DuplicateRegistrationError(
        "Não foi possível cadastrar novamente. " + " | ".join(messages)
    )


def append_company_to_sheet(payload: dict) -> None:
    """Adiciona uma nova empresa na aba principal respeitando a estrutura atual da planilha."""
    client = get_gsheet_client()
    spreadsheet = client.open_by_key(SHEET_ID)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    headers = worksheet.row_values(1)

    validate_unique_company_registration(payload, worksheet)

    if not headers:
        raise RuntimeError("A primeira linha da planilha precisa conter os cabeçalhos.")

    row_values = [""] * len(headers)

    _set_sheet_value_by_header(row_values, headers, ["Nome da empresa", "Empresa", "Nome Empresa"], payload.get("empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Data de abertura", "Data abertura"], payload.get("data_abertura"))
    _set_sheet_value_by_header(row_values, headers, ["Capital", "Capital social"], payload.get("capital"))
    _set_sheet_value_by_header(row_values, headers, ["CNPJ"], payload.get("cnpj"))
    _set_sheet_value_by_header(row_values, headers, ["Endereço", "Endereco"], payload.get("endereco"))
    _set_sheet_value_by_header(row_values, headers, ["Email", "E-mail"], payload.get("email_empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Site empresa", "Site", "Website"], payload.get("site"))

    _set_sheet_value_by_header(row_values, headers, ["Telefone (b2b)", "Telefone b2b"], payload.get("telefone_b2b"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone fixo", "Fixo"], payload.get("telefone_fixo"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone lemitt", "Telefone alternativo", "Outro telefone"], payload.get("telefone_alternativo"))

    _set_sheet_value_by_header(row_values, headers, ["Sócio 1", "Socio 1", "Sócio1", "Socio1"], payload.get("socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_1"), occurrence=1)
    _set_sheet_value_by_header(row_values, headers, ["E-mail Sócio 1", "Email Sócio 1", "E-mail Socio 1", "Email Socio 1"], payload.get("email_socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone"], payload.get("telefone_socio_1"), occurrence=1)

    _set_sheet_value_by_header(row_values, headers, ["Sócio 2", "Socio 2", "Sócio2", "Socio2"], payload.get("socio_2"))
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_2"), occurrence=2)

    _set_sheet_value_by_header(row_values, headers, ["Sócio 3", "Socio 3", "Sócio3", "Socio3"], payload.get("socio_3"))
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_3"), occurrence=3)

    _set_sheet_value_by_header(row_values, headers, ["Instagram"], payload.get("instagram"))
    _set_sheet_value_by_header(row_values, headers, ["Linkedin", "LinkedIn"], payload.get("linkedin"))
    _set_sheet_value_by_header(row_values, headers, ["Vendedor", "Responsável", "Responsavel"], payload.get("vendedor"))
    _set_sheet_value_by_header(row_values, headers, ["Status", "Etapa"], payload.get("status"))
    _set_sheet_value_by_header(row_values, headers, ["Data do chamado", "Data chamado"], payload.get("data_chamado"))
    _set_sheet_value_by_header(row_values, headers, ["Última atualização", "Ultima atualização", "Ultima atualizacao"], payload.get("ultima_atualizacao"))
    _set_sheet_value_by_header(row_values, headers, ["Observações", "Observacoes", "Observação", "Observacao"], payload.get("observacoes"))

    worksheet.append_row(
        row_values,
        value_input_option="USER_ENTERED",
        insert_data_option="INSERT_ROWS",
    )

    st.cache_data.clear()


# =========================================================
# IDENTIFICAÇÃO DAS COLUNAS
# =========================================================
def identify_columns(df: pd.DataFrame) -> dict:
    return {
        "empresa": first_existing_column(df, ["Nome da empresa", "Empresa", "Nome Empresa"]),
        "data_abertura": first_existing_column(df, ["Data de abertura", "Data abertura"]),
        "capital": first_existing_column(df, ["Capital", "Capital social"]),
        "cnpj": first_existing_column(df, ["CNPJ"]),
        "endereco": first_existing_column(df, ["Endereço", "Endereco"]),
        "email": first_existing_column(df, ["Email", "E-mail"]),
        "site": first_existing_column(df, ["Site empresa", "Site", "Website"]),
        "telefone_b2b": first_existing_column(df, ["Telefone (b2b)", "Telefone b2b", "Telefone"]),
        "telefone_fixo": first_existing_column(df, ["Telefone fixo", "Fixo"]),
        "telefone_alternativo": first_existing_column(df, ["Telefone lemitt", "Telefone alternativo", "Outro telefone"]),
        "socio_1": first_existing_column(df, ["Sócio 1", "Socio 1", "Sócio1", "Socio1"]),
        "cpf_socio_1": first_existing_column(df, ["CPF"]),
        "email_socio_1": first_existing_column(df, ["E-mail Sócio 1", "Email Sócio 1", "E-mail Socio 1", "Email Socio 1"]),
        "telefone_socio_1": first_existing_column(df, ["Telefone sócio 1", "Telefone socio 1", "Telefone cliente", "Telefone"]),
        "socio_2": first_existing_column(df, ["Sócio 2", "Socio 2", "Sócio2", "Socio2"]),
        "cpf_socio_2": first_existing_column(df, ["CPF_2"]),
        "socio_3": first_existing_column(df, ["Sócio 3", "Socio 3", "Sócio3", "Socio3"]),
        "cpf_socio_3": first_existing_column(df, ["CPF_3"]),
        "instagram": first_existing_column(df, ["Instagram"]),
        "linkedin": first_existing_column(df, ["Linkedin", "LinkedIn"]),
        "vendedor": first_existing_column(df, ["Vendedor", "Responsável", "Responsavel"]),
        "status": first_existing_column(df, ["Status", "Etapa"]),
        "data_chamado": first_existing_column(df, ["Data do chamado", "Data chamado"]),
        "ultima_atualizacao": first_existing_column(df, ["Última atualização", "Ultima atualização", "Ultima atualizacao"]),
        "observacoes": first_existing_column(df, ["Observações", "Observacoes", "Observação", "Observacao"]),
    }


def prepare_data(df: pd.DataFrame, columns: dict) -> pd.DataFrame:
    result = df.copy()

    result["_empresa"] = safe_series(result, columns.get("empresa"))
    result["_capital_num"] = safe_series(result, columns.get("capital")).apply(parse_money)
    result["_status_original"] = safe_series(result, columns.get("status")).replace("", "Novo Lead")
    result["_status_grupo"] = result["_status_original"].apply(status_group)
    result["_vendedor"] = safe_series(result, columns.get("vendedor")).replace("", "Sem vendedor")
    result["_telefone"] = safe_series(result, columns.get("telefone_b2b"))
    result["_data_chamado"] = safe_series(result, columns.get("data_chamado")).apply(parse_date)
    result["_ultima_atualizacao"] = safe_series(result, columns.get("ultima_atualizacao")).apply(parse_date)
    result["_pontuacao"] = result.apply(lambda row: calculate_score(row, columns), axis=1)
    result["_classificacao"] = result["_pontuacao"].apply(score_classification)

    return result


# =========================================================
# CSS DA TELA DE LOGIN
# =========================================================
def apply_login_css() -> None:
    render_html(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(circle at 72% 47%, rgba(213, 56, 255, 0.22), transparent 26%),
                    radial-gradient(circle at 18% 82%, rgba(125, 0, 255, 0.10), transparent 18%),
                    linear-gradient(90deg, #05050C 0%, #090819 37%, #140A2E 68%, #170A2A 100%);
            }

            header[data-testid="stHeader"] {
                background: transparent !important;
            }

            section[data-testid="stSidebar"],
            [data-testid="collapsedControl"] {
                display: none !important;
            }

            .block-container {
                max-width: 1320px !important;
                padding-top: 1rem !important;
                padding-bottom: 1rem !important;
            }

            .login-brand-panel {
                min-height: 620px;
                border-radius: 36px;
                border: 1px solid rgba(255,255,255,0.06);
                padding: 42px 38px;
                position: relative;
                overflow: hidden;
                background:
                    linear-gradient(180deg, rgba(0,0,0,0.73), rgba(2,2,14,0.96)),
                    linear-gradient(145deg, #090910, #030309);
                box-shadow: 0 24px 60px rgba(0,0,0,0.30);
            }

            .login-brand-panel::before {
                content: "";
                position: absolute;
                left: -16%;
                right: -18%;
                bottom: -6%;
                height: 190px;
                background:
                    radial-gradient(circle at 18% 70%, rgba(255, 42, 154, 0.35), transparent 22%),
                    radial-gradient(circle at 50% 85%, rgba(119, 30, 255, 0.30), transparent 24%),
                    radial-gradient(circle at 82% 75%, rgba(255, 42, 154, 0.22), transparent 20%);
                filter: blur(18px);
                opacity: 0.90;
            }

            .login-logo-wrap {
                margin: 6px 0 38px 0;
            }

            .login-logo-img {
                width: 116px;
                height: 116px;
                object-fit: contain;
                display: block;
                filter: drop-shadow(0 18px 42px rgba(203, 38, 255, 0.24));
            }

            .login-logo-fallback {
                width: 116px;
                height: 116px;
                border-radius: 50%;
                background: linear-gradient(145deg, #FF4BAA 10%, #9C19FF 88%);
                position: relative;
                box-shadow: 0 18px 42px rgba(203, 38, 255, 0.24);
            }

            .login-logo-fallback::before {
                content: "";
                position: absolute;
                width: 40px;
                height: 40px;
                border-radius: 50%;
                background: #06060B;
                top: 28px;
                left: 38px;
            }

            .login-logo-fallback::after {
                content: "";
                position: absolute;
                width: 42px;
                height: 42px;
                left: 4px;
                bottom: 2px;
                transform: rotate(-7deg);
                clip-path: polygon(0 100%, 26% 26%, 100% 0);
                background: linear-gradient(145deg, #FF4BAA 10%, #9C19FF 88%);
                border-bottom-left-radius: 18px;
            }

            .login-brand-title {
                font-size: 2.95rem;
                line-height: 1.04;
                font-weight: 950;
                color: #FFFFFF;
                letter-spacing: -0.05em;
            }

            .login-brand-highlight {
                display: block;
                background: linear-gradient(90deg, #FF4BAA 0%, #D73AFF 50%, #8C2BFF 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }

            .login-brand-subtitle {
                margin-top: 14px;
                color: rgba(255,255,255,0.84);
                font-size: 1.0rem;
            }

            .login-accent-line {
                width: 86px;
                height: 4px;
                border-radius: 999px;
                margin: 34px 0 34px 0;
                background: linear-gradient(90deg, #FF4BAA, #A62CFF);
            }

            .login-benefit {
                display: flex;
                align-items: center;
                gap: 18px;
                max-width: 320px;
                color: rgba(255,255,255,0.82);
                font-size: 0.94rem;
                line-height: 1.55;
            }

            .login-benefit-icon {
                width: 50px;
                height: 50px;
                min-width: 50px;
                border-radius: 15px;
                border: 2px solid rgba(183, 75, 255, 0.54);
                display: flex;
                align-items: center;
                justify-content: center;
                color: #D44BFF;
                font-size: 1.2rem;
            }

            .login-right-spacer {
                height: 66px;
            }

            [data-testid="stForm"] {
                position: relative !important;
                isolation: isolate !important;
                overflow: visible !important;
                background: #FFFFFF !important;
                border: none !important;
                border-radius: 30px !important;
                padding: 28px 32px 24px 32px !important;
                box-shadow:
                    0 28px 70px rgba(0,0,0,0.30),
                    0 0 0 1px rgba(255,255,255,0.55) !important;
                max-width: 640px !important;
                margin: 0 auto !important;
            }

            [data-testid="stForm"]::before {
                content: "";
                position: absolute;
                inset: -24px;
                border-radius: 42px;
                background:
                    radial-gradient(circle at 18% 52%, rgba(255, 72, 170, 0.34), transparent 34%),
                    radial-gradient(circle at 82% 50%, rgba(151, 42, 255, 0.34), transparent 36%),
                    radial-gradient(circle at 50% 100%, rgba(233, 56, 193, 0.24), transparent 32%);
                filter: blur(24px);
                z-index: -2;
                opacity: 1;
            }

            [data-testid="stForm"]::after {
                content: "";
                position: absolute;
                inset: -1px;
                border-radius: 31px;
                background: linear-gradient(135deg, rgba(255,255,255,0.90), rgba(255,255,255,0.72));
                z-index: -1;
            }

            .login-top-icon {
                width: 66px;
                height: 66px;
                border-radius: 50%;
                background: #F4EAFB;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 12px auto;
                color: #A640FF;
                font-size: 1.55rem;
            }

            .login-card-title {
                text-align: center;
                color: #1E2230;
                font-size: 1.55rem;
                font-weight: 850;
                line-height: 1.28;
            }

            .login-card-subtitle {
                text-align: center;
                color: #7B8090;
                font-size: 1rem;
                margin-top: 5px;
                margin-bottom: 18px;
            }

            [data-testid="stForm"] label {
                color: #1F2430 !important;
                font-size: 0.98rem !important;
                font-weight: 750 !important;
            }

            [data-testid="stForm"] [data-baseweb="input"] {
                min-height: 54px !important;
                border: 1px solid #D7DAE2 !important;
                border-radius: 15px !important;
                background: #FFFFFF !important;
                box-shadow: none !important;
            }

            [data-testid="stForm"] input {
                color: #1F2330 !important;
                font-size: 1rem !important;
            }

            [data-testid="stForm"] .stButton > button,
            [data-testid="stForm"] button[kind="secondaryFormSubmit"] {
                width: 100% !important;
                min-height: 54px !important;
                border: none !important;
                border-radius: 15px !important;
                color: #FFFFFF !important;
                font-size: 1.03rem !important;
                font-weight: 850 !important;
                letter-spacing: 0.01em !important;
                background: linear-gradient(90deg, #FF4BAA 0%, #D73AFF 54%, #8C2BFF 100%) !important;
                box-shadow:
                    0 16px 32px rgba(188, 32, 255, 0.28),
                    0 6px 18px rgba(255, 75, 170, 0.18) !important;
                margin-top: 0.45rem !important;
                transition: transform 0.15s ease, box-shadow 0.15s ease !important;
            }

            [data-testid="stForm"] .stButton > button:hover,
            [data-testid="stForm"] button[kind="secondaryFormSubmit"]:hover {
                transform: translateY(-1px);
                box-shadow:
                    0 18px 36px rgba(188, 32, 255, 0.32),
                    0 8px 22px rgba(255, 75, 170, 0.20) !important;
            }

            .login-forgot-row {
                display: grid;
                grid-template-columns: 1fr auto 1fr;
                gap: 18px;
                align-items: center;
                margin-top: 18px;
            }

            .login-forgot-line {
                height: 1px;
                background: #E7E7EC;
            }

            .login-forgot-text {
                color: #A23BFF;
                font-weight: 750;
                font-size: 0.92rem;
            }

            .login-error {
                max-width: 640px;
                margin: 14px auto 0 auto;
                padding: 12px 14px;
                border-radius: 14px;
                background: #FFF0F3;
                color: #A02B42;
                border: 1px solid #FFC7D0;
                font-weight: 650;
                font-size: 0.95rem;
            }

            @media (max-width: 1050px) {
                .login-brand-panel {
                    min-height: auto;
                    padding: 30px;
                }

                .login-logo-wrap {
                    margin-top: 0;
                    margin-bottom: 24px;
                }

                .login-brand-title {
                    font-size: 2.2rem;
                }

                .login-right-spacer {
                    height: 0;
                }
            }
        </style>
        """
    )


# =========================================================
# CSS DO DASHBOARD
# =========================================================
def apply_dashboard_css() -> None:
    render_html(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(circle at 78% 10%, rgba(202, 0, 255, 0.15), transparent 20%),
                    linear-gradient(120deg, #04040A 0%, #090915 34%, #140B2A 68%, #0A071A 100%);
            }

            header[data-testid="stHeader"] {
                background: transparent !important;
            }

            .block-container {
                max-width: 1600px !important;
                padding-top: 1.25rem !important;
                padding-bottom: 1.8rem !important;
            }

            section[data-testid="stSidebar"] {
                background:
                    radial-gradient(circle at top left, rgba(255,255,255,0.92) 0%, rgba(255,255,255,0.00) 32%),
                    radial-gradient(circle at bottom right, rgba(208,212,223,0.72) 0%, rgba(208,212,223,0.00) 34%),
                    linear-gradient(180deg, #F7F8FC 0%, #ECEEF4 38%, #DCE0E9 72%, #CED3DE 100%);
                border-right: 1px solid rgba(63, 53, 83, 0.12);
                box-shadow: 10px 0 34px rgba(0,0,0,0.16);
            }

            section[data-testid="stSidebar"] * {
                color: #20192F;
            }

            section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
                padding-top: 0.35rem !important;
            }

            .side-logo-wrap {
                width: 124px;
                height: 124px;
                display: flex;
                align-items: center;
                justify-content: center;
                overflow: visible;
                margin: -4px 0 8px -10px;
            }

            .side-logo-img {
                width: 116px;
                height: 116px;
                max-width: none;
                object-fit: contain;
                object-position: center;
                display: block;
                border-radius: 0;
                background: transparent;
                overflow: visible;
                filter: drop-shadow(0 12px 24px rgba(188, 45, 255, 0.18));
            }

            .side-logo-fallback {
                width: 92px;
                height: 92px;
                border-radius: 50%;
                background: linear-gradient(145deg, #FF4BAA 10%, #9C19FF 88%);
                position: relative;
                box-shadow: 0 12px 24px rgba(188, 45, 255, 0.20);
            }

            .side-logo-fallback::before {
                content: "";
                position: absolute;
                width: 32px;
                height: 32px;
                border-radius: 50%;
                background: #1B1725;
                top: 24px;
                left: 30px;
            }

            .side-logo-fallback::after {
                content: "";
                position: absolute;
                width: 32px;
                height: 32px;
                left: 2px;
                bottom: 1px;
                clip-path: polygon(0 100%, 25% 26%, 100% 0);
                background: linear-gradient(145deg, #FF4BAA 10%, #9C19FF 88%);
            }

            .side-title {
                color: #211A30;
                font-size: 1.16rem;
                font-weight: 900;
                line-height: 1.15;
            }

            .side-highlight {
                display: block;
                background: linear-gradient(90deg, #FF4BAA, #AE26FF);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }

            .side-subtitle {
                color: rgba(33,26,48,0.76);
                margin-top: 6px;
                font-size: 0.90rem;
            }

            .side-line {
                width: 70px;
                height: 4px;
                border-radius: 999px;
                margin: 18px 0 18px 0;
                background: linear-gradient(90deg, #FF4BAA, #AE26FF);
            }

            .side-tip {
                display: flex;
                gap: 12px;
                align-items: center;
                margin: 14px 0 18px 0;
                padding: 14px;
                border-radius: 16px;
                background: rgba(255,255,255,0.44);
                border: 1px solid rgba(90,76,118,0.12);
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.72);
            }

            .side-tip-icon {
                width: 44px;
                height: 44px;
                min-width: 44px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #D54BFF;
                border: 1px solid rgba(184, 70, 255, 0.55);
                border-radius: 14px;
            }

            .side-tip-text {
                font-size: 0.82rem;
                line-height: 1.48;
                color: rgba(33,26,48,0.82);
                font-weight: 700;
            }

            section[data-testid="stSidebar"] div[role="radiogroup"] {
                gap: 4px !important;
            }

            section[data-testid="stSidebar"] div[role="radiogroup"] > label {
                display: flex !important;
                align-items: center !important;
                margin: 0 !important;
                padding: 7px 0 !important;
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
            }

            section[data-testid="stSidebar"] div[role="radiogroup"] > label p,
            section[data-testid="stSidebar"] div[role="radiogroup"] > label span {
                color: #241C34 !important;
                font-weight: 800 !important;
            }

            section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover p,
            section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover span {
                color: #7D2DFF !important;
            }

            section[data-testid="stSidebar"] div[role="radiogroup"] > label input {
                accent-color: #FF4BAA !important;
            }

            .page-title {
                color: #FFFFFF;
                font-size: 2.55rem;
                line-height: 1.08;
                font-weight: 950;
                letter-spacing: -0.04em;
            }

            .page-subtitle {
                margin-top: 7px;
                margin-bottom: 16px;
                color: rgba(255,255,255,0.70);
                font-size: 0.94rem;
            }

            .metric-card {
                height: 188px;
                min-height: 188px;
                padding: 17px;
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.06);
                background: linear-gradient(145deg, rgba(22,20,42,0.98), rgba(10,9,25,0.98));
                box-shadow: 0 18px 46px rgba(0,0,0,0.22);
                box-sizing: border-box;
                display: flex;
                flex-direction: column;
            }

            .metric-icon {
                width: 44px;
                height: 44px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 13px;
                color: #FFFFFF;
                font-size: 1.1rem;
                margin-bottom: 14px;
            }

            .metric-label {
                min-height: 38px;
                color: rgba(255,255,255,0.78);
                font-size: 0.94rem;
                font-weight: 750;
                line-height: 1.18;
                display: flex;
                align-items: flex-end;
            }

            .metric-value {
                margin-top: 5px;
                color: #FFFFFF;
                font-size: 1.95rem;
                font-weight: 950;
                line-height: 1;
            }

            .metric-note {
                margin-top: 8px;
                color: #55DF7D;
                font-size: 0.84rem;
                font-weight: 700;
            }

            .section-heading {
                color: #FFFFFF;
                font-size: 1.45rem;
                font-weight: 900;
                margin-bottom: 4px;
            }

            .section-subtitle {
                color: rgba(255,255,255,0.68);
                font-size: 0.92rem;
                margin-bottom: 12px;
            }

            .status-wrap {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }

            .status-row {
                display: grid;
                grid-template-columns: 1fr auto auto;
                align-items: center;
                gap: 12px;
                padding: 11px 12px;
                border-radius: 14px;
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.04);
            }

            .status-left {
                color: #FFFFFF;
                font-size: 0.92rem;
                font-weight: 750;
            }

            .status-count {
                color: #FFFFFF;
                font-weight: 850;
            }

            .status-percent {
                color: rgba(255,255,255,0.66);
                font-weight: 750;
            }

            /* Filtros da visão geral: todos no mesmo estilo escuro, mesma altura e sem borda branca extra */
            div[data-testid="stSelectbox"] > div[data-baseweb="select"] > div,
            div[data-testid="stTextInput"] div[data-baseweb="input"] > div,
            div[data-testid="stDateInput"] div[data-baseweb="input"] > div {
                min-height: 54px !important;
                height: 54px !important;
                border-radius: 15px !important;
                border: 1px solid rgba(255, 75, 170, 0.72) !important;
                background: rgba(8, 7, 24, 0.92) !important;
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.03), 0 10px 30px rgba(0,0,0,0.16) !important;
                color: #FFFFFF !important;
                outline: none !important;
            }

            div[data-testid="stSelectbox"] > div[data-baseweb="select"] > div:hover,
            div[data-testid="stTextInput"] div[data-baseweb="input"] > div:hover,
            div[data-testid="stDateInput"] div[data-baseweb="input"] > div:hover {
                border-color: rgba(255, 75, 170, 0.88) !important;
            }

            div[data-testid="stSelectbox"] > div[data-baseweb="select"] > div:focus-within,
            div[data-testid="stTextInput"] div[data-baseweb="input"] > div:focus-within,
            div[data-testid="stDateInput"] div[data-baseweb="input"] > div:focus-within {
                border-color: rgba(255, 75, 170, 1) !important;
                box-shadow: 0 0 0 1px rgba(255, 75, 170, 0.35), 0 0 22px rgba(169, 28, 255, 0.16) !important;
                background: rgba(8, 7, 24, 0.98) !important;
            }

            /* Remove bordas extras/brancas internas especificamente do Período e Busca */
            div[data-testid="stTextInput"] [data-baseweb="base-input"],
            div[data-testid="stTextInput"] [data-baseweb="input"],
            div[data-testid="stTextInput"] [data-baseweb="input"] > div,
            div[data-testid="stDateInput"] [data-baseweb="base-input"],
            div[data-testid="stDateInput"] [data-baseweb="input"],
            div[data-testid="stDateInput"] [data-baseweb="input"] > div,
            div[data-testid="stDateInput"] > div,
            div[data-testid="stTextInput"] > div {
                border: none !important;
                outline: none !important;
            }

            div[data-testid="stTextInput"] [data-baseweb="input"],
            div[data-testid="stTextInput"] [data-baseweb="input"] > div,
            div[data-testid="stDateInput"] [data-baseweb="input"],
            div[data-testid="stDateInput"] [data-baseweb="input"] > div {
                min-height: 54px !important;
                height: 54px !important;
                box-shadow: none !important;
                box-sizing: border-box !important;
                overflow: visible !important;
            }

            /* Evita corte da borda superior, principalmente no campo Buscar empresa ou telefone */
            div[data-testid="stTextInput"],
            div[data-testid="stDateInput"] {
                padding-top: 3px !important;
                overflow: visible !important;
            }

            div[data-testid="stTextInput"] > div,
            div[data-testid="stDateInput"] > div,
            div[data-testid="stTextInput"] [data-baseweb="base-input"],
            div[data-testid="stDateInput"] [data-baseweb="base-input"] {
                overflow: visible !important;
                box-sizing: border-box !important;
            }

            label {
                color: rgba(255,255,255,0.88) !important;
                font-weight: 700 !important;
            }

            div[data-testid="stSelectbox"] * {
                color: #FFFFFF !important;
            }

            div[data-testid="stTextInput"] [data-baseweb="input"],
            div[data-testid="stTextInput"] [data-baseweb="input"] > div,
            div[data-testid="stTextInput"] input,
            div[data-testid="stDateInput"] [data-baseweb="input"],
            div[data-testid="stDateInput"] [data-baseweb="input"] > div,
            div[data-testid="stDateInput"] input {
                background: transparent !important;
                color: #FFFFFF !important;
                -webkit-text-fill-color: #FFFFFF !important;
                caret-color: #FF4BAA !important;
                min-height: 54px !important;
                height: 54px !important;
                line-height: 54px !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
            }

            div[data-testid="stTextInput"] input::placeholder,
            div[data-testid="stDateInput"] input::placeholder {
                color: rgba(255,255,255,0.54) !important;
                -webkit-text-fill-color: rgba(255,255,255,0.54) !important;
            }

            div[data-testid="stDateInput"] button,
            div[data-testid="stDateInput"] svg {
                color: #FFFFFF !important;
                fill: #FFFFFF !important;
            }

            div[data-testid="stTextInput"] input:-webkit-autofill,
            div[data-testid="stTextInput"] input:-webkit-autofill:hover,
            div[data-testid="stTextInput"] input:-webkit-autofill:focus,
            div[data-testid="stDateInput"] input:-webkit-autofill,
            div[data-testid="stDateInput"] input:-webkit-autofill:hover,
            div[data-testid="stDateInput"] input:-webkit-autofill:focus {
                -webkit-box-shadow: 0 0 0 1000px #0B0918 inset !important;
                -webkit-text-fill-color: #FFFFFF !important;
                caret-color: #FF4BAA !important;
            }

            .stButton > button {
                min-height: 48px !important;
                border: none !important;
                border-radius: 15px !important;
                color: #FFFFFF !important;
                font-weight: 800 !important;
                background: linear-gradient(90deg, #FF4BAA 0%, #A91CFF 100%) !important;
            }

            .latest-calls-shell {
                margin-top: 18px;
                margin-bottom: 14px;
                padding: 22px 24px 18px 24px;
                border-radius: 26px;
                background: linear-gradient(145deg, rgba(22,20,42,0.98), rgba(10,9,25,0.98));
                border: 1px solid rgba(255,255,255,0.06);
                box-shadow: 0 18px 46px rgba(0,0,0,0.22);
            }

            .latest-filter-title {
                color: #FFFFFF;
                font-size: 1.08rem;
                font-weight: 900;
                line-height: 1.2;
                margin-bottom: 4px;
            }

            .latest-filter-subtitle {
                color: rgba(255,255,255,0.68);
                font-size: 0.88rem;
                line-height: 1.45;
            }

            .latest-filter-spacer {
                height: 2px;
            }

            .latest-calls-head {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 16px;
                margin-bottom: 4px;
            }

            .latest-calls-title {
                color: #FFFFFF;
                font-size: 1.05rem;
                font-weight: 900;
                line-height: 1.2;
                margin-bottom: 4px;
            }

            .latest-calls-subtitle {
                color: rgba(255,255,255,0.68);
                font-size: 0.88rem;
                line-height: 1.45;
            }

            .latest-calls-chip {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 8px 14px;
                min-width: 88px;
                border-radius: 999px;
                background: rgba(255, 246, 217, 0.08);
                border: 1px solid rgba(232, 194, 67, 0.92);
                color: #E8C243;
                font-size: 0.78rem;
                font-weight: 900;
                letter-spacing: 0.04em;
                text-transform: uppercase;
            }

            .latest-status-card {
                min-height: 132px;
                height: 132px;
                padding: 14px 12px 12px 12px;
                border-radius: 20px;
                background: linear-gradient(145deg, rgba(22,20,42,0.98), rgba(10,9,25,0.98));
                border: 1px solid rgba(255,255,255,0.06);
                box-shadow: 0 18px 46px rgba(0,0,0,0.22);
                margin-bottom: 12px;
            }

            .latest-status-top {
                display: flex;
                align-items: center;
                gap: 10px;
                margin-bottom: 8px;
            }

            .latest-status-icon {
                width: 36px;
                height: 36px;
                min-width: 36px;
                border-radius: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 0.95rem;
                font-weight: 900;
            }

            .latest-status-name {
                color: #FFFFFF;
                font-size: 0.82rem;
                font-weight: 850;
                line-height: 1.2;
            }

            .latest-status-number {
                color: #FFFFFF;
                font-size: 1.15rem;
                line-height: 1;
                font-weight: 950;
                margin-top: 4px;
            }

            .latest-status-caption {
                color: #55DF7D;
                font-size: 0.72rem;
                font-weight: 700;
                margin-top: 6px;
            }

            .latest-table-card {
                margin-top: 30px;
                padding: 0;
                border-radius: 26px;
                background: linear-gradient(135deg, rgba(255,75,170,0.96), rgba(169,28,255,0.96));
                box-shadow:
                    0 20px 52px rgba(0,0,0,0.26),
                    0 0 34px rgba(169,28,255,0.18);
            }

            .latest-table-card-inner {
                margin: 1px;
                padding: 18px 18px 16px 18px;
                border-radius: 25px;
                background:
                    radial-gradient(circle at 100% 0%, rgba(169,28,255,0.16), transparent 28%),
                    radial-gradient(circle at 0% 100%, rgba(255,75,170,0.12), transparent 30%),
                    linear-gradient(145deg, rgba(22,20,42,0.99), rgba(10,9,25,0.99));
                border: 1px solid rgba(255,255,255,0.06);
            }

            .latest-table-head {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 14px;
                margin-bottom: 12px;
            }

            .latest-table-title-wrap {
                display: flex;
                align-items: center;
                gap: 12px;
            }

            .latest-table-icon {
                width: 42px;
                height: 42px;
                min-width: 42px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 14px;
                background: linear-gradient(135deg, rgba(255,75,170,0.22), rgba(169,28,255,0.24));
                border: 1px solid rgba(255,75,170,0.34);
                color: #FF8CCC;
                font-size: 1.05rem;
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
            }

            .latest-table-title {
                color: #FFFFFF;
                font-size: 1.04rem;
                font-weight: 900;
                line-height: 1.2;
                margin-bottom: 4px;
            }

            .latest-table-subtitle {
                color: rgba(255,255,255,0.66);
                font-size: 0.84rem;
                line-height: 1.45;
            }

            .latest-table-badges {
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: flex-end;
                gap: 8px;
            }

            .latest-table-badge,
            .latest-table-status-badge {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 7px 12px;
                border-radius: 999px;
                font-size: 0.76rem;
                font-weight: 900;
                white-space: nowrap;
            }

            /* Ao clicar em “Ver nomes”, exibe somente a planilha editável. */
            div[data-testid="stDataEditor"] {
                margin-top: 24px !important;
            }

            .latest-table-badge {
                background: rgba(255, 246, 217, 0.08);
                border: 1px solid rgba(232, 194, 67, 0.92);
                color: #E8C243;
            }

            .latest-table-status-badge {
                background: rgba(255,75,170,0.10);
                border: 1px solid rgba(255,75,170,0.44);
                color: #FF8CCC;
            }

            .latest-company-fields {
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin-top: 10px;
            }

            .latest-company-field {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                padding: 6px 9px;
                border-radius: 999px;
                background: rgba(255,255,255,0.045);
                border: 1px solid rgba(255,255,255,0.07);
                color: rgba(255,255,255,0.72);
                font-size: 0.72rem;
                font-weight: 750;
            }

            .latest-editor-help {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                margin: 14px 0 12px 0;
                padding: 14px 16px;
                border-radius: 16px;
                background:
                    linear-gradient(90deg, rgba(255,75,170,0.10), rgba(169,28,255,0.10)),
                    rgba(13,11,31,0.94);
                border: 1px solid rgba(255,75,170,0.30);
                color: rgba(255,255,255,0.76);
                font-size: 0.84rem;
                line-height: 1.45;
                box-shadow: 0 12px 30px rgba(0,0,0,0.16);
            }

            .latest-editor-help strong {
                color: #FF8CCC;
            }

            .latest-sync-badge {
                display: inline-flex;
                align-items: center;
                gap: 7px;
                flex-shrink: 0;
                padding: 7px 11px;
                border-radius: 999px;
                background: rgba(85,223,125,0.10);
                border: 1px solid rgba(85,223,125,0.38);
                color: #55DF7D;
                font-size: 0.74rem;
                font-weight: 900;
                letter-spacing: 0.02em;
            }

            .latest-status-legend {
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin: 10px 0 14px 0;
            }

            .latest-status-pill {
                display: inline-flex;
                align-items: center;
                gap: 7px;
                padding: 6px 10px;
                border-radius: 999px;
                background: rgba(255,255,255,0.045);
                border: 1px solid rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.86);
                font-size: 0.73rem;
                font-weight: 800;
            }

            .latest-status-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                display: inline-block;
                box-shadow: 0 0 12px currentColor;
            }

            /* Tabela comercial compacta com botão Copiar */
            .premium-inline-table-header {
                margin-top: 4px;
                margin-bottom: 3px;
                padding: 7px 8px;
                border-radius: 9px;
                background: linear-gradient(90deg, rgba(255,75,170,0.15), rgba(169,28,255,0.15));
                border: 1px solid rgba(255,75,170,0.24);
                color: rgba(255,255,255,0.94);
                font-size: 0.73rem;
                font-weight: 850;
            }

            .premium-inline-cell {
                min-height: 30px;
                display: flex;
                align-items: center;
                padding: 4px 7px;
                border-radius: 7px;
                background: rgba(255,255,255,0.97);
                border: 1px solid rgba(169,28,255,0.08);
                color: #261C35;
                font-size: 0.77rem;
                line-height: 1.16;
                word-break: break-word;
            }

            .premium-inline-cell.phone {
                color: #5C2A83;
                font-weight: 850;
            }

            .premium-inline-cell.date {
                justify-content: center;
                color: #5B5369;
                font-size: 0.73rem;
            }

            .premium-inline-cell.muted {
                color: #6E667A;
            }

            .premium-inline-hint {
                margin: 5px 0 7px 0;
                padding: 8px 11px;
                border-radius: 10px;
                background: linear-gradient(90deg, rgba(255,75,170,0.07), rgba(169,28,255,0.07));
                border: 1px solid rgba(255,75,170,0.17);
                color: rgba(255,255,255,0.72);
                font-size: 0.74rem;
                line-height: 1.30;
            }

            .premium-inline-hint strong {
                color: #FF79C4;
            }

            /* Linhas compactas: sem espaços exagerados entre empresas */
            .st-key-compact_inline_table div[data-testid="stHorizontalBlock"] {
                gap: 0.34rem !important;
                margin-bottom: 0 !important;
            }

            .st-key-compact_inline_table div[data-testid="stVerticalBlock"] {
                gap: 0.20rem !important;
            }

            .st-key-compact_inline_table div[data-testid="stElementContainer"] {
                margin-bottom: 0 !important;
            }

            .st-key-compact_inline_table div[data-testid="stSelectbox"] {
                margin-bottom: 0 !important;
            }

            .st-key-compact_inline_table div[data-testid="stSelectbox"] > div[data-baseweb="select"] {
                min-height: 34px !important;
                height: 34px !important;
            }

            .st-key-compact_inline_table div[data-testid="stSelectbox"] > div[data-baseweb="select"] > div {
                min-height: 34px !important;
                height: 34px !important;
                border-radius: 7px !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
                display: flex !important;
                align-items: center !important;
                overflow: visible !important;
            }

            .st-key-compact_inline_table div[data-testid="stSelectbox"] span,
            .st-key-compact_inline_table div[data-testid="stSelectbox"] p {
                line-height: 1.15 !important;
                white-space: nowrap !important;
                overflow: visible !important;
                text-overflow: clip !important;
            }

            .st-key-compact_inline_table iframe {
                min-height: 30px !important;
                height: 30px !important;
            }

            /* A tabela não deve ampliar no hover */
            .premium-inline-table-header,
            .premium-inline-table-header:hover,
            .premium-inline-cell,
            .premium-inline-cell:hover,
            .st-key-compact_inline_table,
            .st-key-compact_inline_table * {
                transform: none !important;
                transition:
                    border-color 0.16s ease,
                    background 0.16s ease !important;
            }

            /* Planilha editável: detalhes em rosa e roxo, sem zoom */
            div[data-testid="stDataEditor"] {
                overflow: hidden;
                border-radius: 20px;
                border: 1px solid rgba(255,75,170,0.52);
                background: linear-gradient(180deg, rgba(255,255,255,0.995), rgba(249,247,255,0.995));
                box-shadow:
                    0 18px 44px rgba(0,0,0,0.24),
                    0 0 0 1px rgba(169,28,255,0.10),
                    0 0 32px rgba(169,28,255,0.14);
                transform: none !important;
                transition:
                    border-color 0.22s ease,
                    box-shadow 0.22s ease !important;
            }

            div[data-testid="stDataEditor"] [role="grid"] {
                border-radius: 20px;
                overflow: hidden;
                transform: none !important;
            }

            div[data-testid="stDataEditor"]:hover {
                transform: none !important;
                border-color: rgba(255,75,170,0.80);
                box-shadow:
                    0 20px 48px rgba(0,0,0,0.25),
                    0 0 0 1px rgba(169,28,255,0.22),
                    0 0 36px rgba(255,75,170,0.16) !important;
            }

            div[data-testid="stDataEditor"] * {
                transform: none !important;
            }

            div[data-testid="stDataEditor"] [role="columnheader"] {
                background: linear-gradient(90deg, rgba(255,75,170,0.22), rgba(169,28,255,0.22)) !important;
                border-bottom: 1px solid rgba(169,28,255,0.24) !important;
                color: #2A183E !important;
                font-weight: 900 !important;
                letter-spacing: 0.01em !important;
            }

            div[data-testid="stDataEditor"] [role="gridcell"] {
                border-color: rgba(169,28,255,0.10) !important;
                color: #261C35 !important;
                background: rgba(255,255,255,0.99) !important;
            }

            div[data-testid="stDataEditor"] [role="row"]:nth-child(even) [role="gridcell"] {
                background: rgba(169,28,255,0.045) !important;
            }

            div[data-testid="stDataEditor"] [role="row"]:hover [role="gridcell"] {
                background: linear-gradient(90deg, rgba(255,75,170,0.095), rgba(169,28,255,0.065)) !important;
            }

            div[data-testid="stDataEditor"] [role="gridcell"]:focus,
            div[data-testid="stDataEditor"] [role="gridcell"]:focus-within {
                outline: 2px solid rgba(255,75,170,0.82) !important;
                outline-offset: -2px !important;
                background: rgba(255,75,170,0.10) !important;
            }

            div[data-testid="stDataEditor"] button,
            div[data-testid="stDataEditor"] svg {
                color: #A91CFF !important;
            }

            div[data-testid="stDataFrame"] {
                overflow: hidden;
                border-radius: 16px;
                border: 1px solid rgba(20,16,36,0.10);
                box-shadow: 0 8px 18px rgba(14, 13, 27, 0.04);
            }

            /* Lista de nomes em Todos os contratos: visual preto, rosa e roxo */
            .contracts-names-count-card {
                margin: 14px 0 14px 0;
                padding: 13px 16px;
                border-radius: 14px;
                color: rgba(38,31,53,0.82);
                font-size: 0.86rem;
                line-height: 1.4;
                background:
                    radial-gradient(circle at 100% 0%, rgba(255,255,255,0.80), transparent 35%),
                    linear-gradient(90deg, rgba(247,248,252,0.98), rgba(220,224,233,0.98));
                border: 1px solid rgba(255,75,170,0.30);
                box-shadow: 0 12px 30px rgba(0,0,0,0.14), 0 0 14px rgba(169,28,255,0.06);
            }

            .contracts-names-count-card strong {
                color: #FF79C4;
                font-weight: 950;
            }

            .contracts-names-table {
                margin-top: 10px;
                overflow: hidden;
                border-radius: 18px;
                border: 1px solid rgba(255,75,170,0.44);
                background:
                    radial-gradient(circle at 100% 0%, rgba(169,28,255,0.14), transparent 34%),
                    linear-gradient(145deg, rgba(13,11,31,0.99), rgba(7,6,18,0.99));
                box-shadow:
                    0 20px 46px rgba(0,0,0,0.30),
                    0 0 0 1px rgba(169,28,255,0.08),
                    0 0 26px rgba(169,28,255,0.12);
            }

            .contracts-names-table-header {
                padding: 14px 18px;
                color: #FFFFFF;
                font-size: 0.90rem;
                font-weight: 950;
                letter-spacing: 0.02em;
                text-transform: uppercase;
                background:
                    linear-gradient(90deg, rgba(255,75,170,0.34), rgba(169,28,255,0.32)),
                    rgba(12,10,28,0.98);
                border-bottom: 1px solid rgba(255,75,170,0.34);
            }

            .contracts-names-table-row {
                padding: 12px 18px;
                color: rgba(255,255,255,0.90);
                font-size: 0.90rem;
                font-weight: 650;
                line-height: 1.25;
                background: rgba(11,10,27,0.96);
                border-bottom: 1px solid rgba(255,75,170,0.10);
                transition:
                    background 0.18s ease,
                    color 0.18s ease,
                    padding-left 0.18s ease,
                    box-shadow 0.18s ease !important;
            }

            .contracts-names-table-row:nth-child(odd) {
                background: rgba(18,13,38,0.97);
            }

            .contracts-names-table-row:last-child {
                border-bottom: none;
            }

            .contracts-names-table-row:hover {
                padding-left: 22px;
                color: #FFFFFF;
                background: linear-gradient(90deg, rgba(255,75,170,0.18), rgba(169,28,255,0.13));
                box-shadow: inset 4px 0 0 #FF4BAA;
            }

            .contracts-filter-summary-grid {
                display: grid;
                grid-template-columns: repeat(7, minmax(0, 1fr));
                gap: 10px;
                margin: 16px 0 4px 0;
            }

            .contracts-filter-summary-card {
                min-height: 110px;
                padding: 13px 12px;
                border-radius: 18px;
                border: 1px solid rgba(255,255,255,0.06);
                background: linear-gradient(145deg, rgba(22,20,42,0.98), rgba(10,9,25,0.98));
                box-shadow: 0 14px 34px rgba(0,0,0,0.20);
                transition: transform 0.20s ease, box-shadow 0.20s ease, border-color 0.20s ease !important;
            }

            .contracts-filter-summary-card:hover {
                transform: scale(1.025);
                border-color: rgba(255,75,170,0.40);
                box-shadow: 0 18px 42px rgba(0,0,0,0.24), 0 0 20px rgba(169,28,255,0.10);
            }

            .contracts-filter-summary-top {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-bottom: 7px;
            }

            .contracts-filter-summary-icon {
                width: 34px;
                height: 34px;
                min-width: 34px;
                border-radius: 11px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 0.90rem;
                font-weight: 900;
            }

            .contracts-filter-summary-name {
                color: #FFFFFF;
                font-size: 0.77rem;
                font-weight: 850;
                line-height: 1.15;
            }

            .contracts-filter-summary-count {
                color: #FFFFFF;
                font-size: 1.18rem;
                line-height: 1;
                font-weight: 950;
            }

            .contracts-filter-summary-caption {
                margin-top: 7px;
                color: #55DF7D;
                font-size: 0.69rem;
                line-height: 1.25;
                font-weight: 800;
            }

            @media (max-width: 1200px) {
                .contracts-filter-summary-grid {
                    grid-template-columns: repeat(4, minmax(0, 1fr));
                }
            }

            /* Animações suaves de zoom ao passar o mouse */
            .metric-card,
            .latest-calls-shell,
            .latest-status-card,
            .latest-table-card,
            .latest-placeholder-card,
            .status-row,
            .side-tip,
            section[data-testid="stSidebar"] div[role="radiogroup"] > label,
            .stButton > button,
            div[data-testid="stSelectbox"] > div[data-baseweb="select"] > div,
            div[data-testid="stTextInput"] div[data-baseweb="input"] > div,
            div[data-testid="stDateInput"] div[data-baseweb="input"] > div {
                transition:
                    transform 0.22s ease,
                    box-shadow 0.22s ease,
                    border-color 0.22s ease,
                    filter 0.22s ease,
                    background 0.22s ease !important;
                transform-origin: center center;
                will-change: transform;
            }

            .metric-card:hover,
            .latest-calls-shell:hover,
            .latest-status-card:hover,
            .latest-table-card:hover,
            .latest-placeholder-card:hover,
            .status-row:hover,
            .side-tip:hover {
                transform: scale(1.025);
                box-shadow: 0 22px 54px rgba(0,0,0,0.28), 0 0 24px rgba(169, 28, 255, 0.12) !important;
                border-color: rgba(255, 75, 170, 0.34) !important;
                z-index: 3;
            }

            section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
                transform: scale(1.035);
            }

            .stButton > button:hover {
                transform: scale(1.035);
                filter: brightness(1.06);
                box-shadow: 0 14px 30px rgba(169, 28, 255, 0.28) !important;
            }

            div[data-testid="stSelectbox"] > div[data-baseweb="select"] > div:hover,
            div[data-testid="stTextInput"] div[data-baseweb="input"] > div:hover,
            div[data-testid="stDateInput"] div[data-baseweb="input"] > div:hover {
                transform: scale(1.018);
            }

            /* A tabela é a única área sem animação de zoom */
            div[data-testid="stDataEditor"],
            div[data-testid="stDataEditor"]:hover,
            div[data-testid="stDataEditor"] *,
            div[data-testid="stDataEditor"] *:hover,
            div[data-testid="stDataFrame"],
            div[data-testid="stDataFrame"]:hover,
            div[data-testid="stDataFrame"] *,
            div[data-testid="stDataFrame"] *:hover {
                transform: none !important;
                will-change: auto !important;
            }

            /* Menu lateral preservado com submenu flutuante lateral no Cadastro */
            .oppi-side-nav {
                display: flex;
                flex-direction: column;
                gap: 4px;
                margin: 2px 0 14px 0;
                overflow: visible !important;
            }

            .oppi-nav-link,
            .oppi-nav-summary {
                min-height: 38px;
                display: flex;
                align-items: center;
                gap: 10px;
                width: 100%;
                padding: 7px 0;
                color: #241C34 !important;
                text-decoration: none !important;
                font-size: 0.93rem;
                font-weight: 800;
                line-height: 1;
                cursor: pointer;
                list-style: none;
                transition: color 0.16s ease, transform 0.16s ease;
            }

            .oppi-nav-link:hover,
            .oppi-nav-summary:hover {
                color: #7D2DFF !important;
                transform: translateX(3px);
            }

            .oppi-nav-summary::-webkit-details-marker {
                display: none;
            }

            .oppi-nav-dot {
                width: 16px;
                height: 16px;
                min-width: 16px;
                border-radius: 50%;
                border: 1px solid rgba(70, 62, 90, 0.34);
                background: rgba(255,255,255,0.84);
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.84);
            }

            .oppi-nav-link.active .oppi-nav-dot,
            .oppi-cadastro-details.active .oppi-nav-dot {
                border: 4px solid #FF5C64;
                background: #FFFFFF;
                box-shadow: none;
            }

            .oppi-nav-arrow {
                margin-left: auto;
                padding-right: 8px;
                color: #241C34;
                font-size: 1.35rem;
                font-weight: 950;
                line-height: 1;
            }

            .oppi-cadastro-details {
                position: relative;
                overflow: visible !important;
            }

            .oppi-cadastro-flyout {
                display: none;
                position: fixed;
                left: 304px;
                top: 222px;
                width: 275px;
                z-index: 999999;
                overflow: hidden;
                border-radius: 0 12px 12px 0;
                border: 1px solid rgba(63,53,83,0.16);
                border-left: 4px solid #A91CFF;
                background:
                    radial-gradient(circle at top left, rgba(255,255,255,0.96) 0%, rgba(255,255,255,0.00) 34%),
                    radial-gradient(circle at bottom right, rgba(208,212,223,0.78) 0%, rgba(208,212,223,0.00) 40%),
                    linear-gradient(180deg, #F7F8FC 0%, #ECEEF4 42%, #DCE0E9 100%);
                box-shadow: 0 22px 48px rgba(0,0,0,0.22), 0 0 18px rgba(169,28,255,0.10);
            }

            .oppi-cadastro-details[open] .oppi-cadastro-flyout {
                display: block;
            }

            .oppi-flyout-title {
                padding: 16px 18px 13px 18px;
                color: #241C34;
                font-size: 1rem;
                font-weight: 900;
                border-bottom: 1px solid rgba(63,53,83,0.14);
                background: rgba(255,255,255,0.46);
            }

            .oppi-flyout-link {
                display: block;
                min-height: 46px;
                padding: 14px 18px 12px 18px;
                color: #241C34 !important;
                text-decoration: none !important;
                font-size: 0.88rem;
                font-weight: 700;
                background: transparent;
                transition: background 0.16s ease, padding-left 0.16s ease, color 0.16s ease;
            }

            .oppi-flyout-link:hover,
            .oppi-flyout-link.active {
                padding-left: 22px;
                color: #5F1DB8 !important;
                background: linear-gradient(90deg, rgba(255,75,170,0.14), rgba(169,28,255,0.13));
                box-shadow: inset 3px 0 0 #A91CFF;
            }

            @media (max-width: 900px) {
                .oppi-cadastro-flyout {
                    left: 286px;
                    top: 210px;
                    width: 245px;
                }
            }

            @media (prefers-reduced-motion: reduce) {
                .metric-card,
                .latest-calls-shell,
                .latest-status-card,
                .latest-table-card,
                .latest-placeholder-card,
                .status-row,
                .side-tip,
                section[data-testid="stSidebar"] div[role="radiogroup"] > label,
                .stButton > button,
                div[data-testid="stSelectbox"] > div[data-baseweb="select"] > div,
                div[data-testid="stTextInput"] div[data-baseweb="input"] > div,
                div[data-testid="stDateInput"] div[data-baseweb="input"] > div {
                    transition: none !important;
                    transform: none !important;
                }
            }
        </style>
        """
    )


def apply_registration_css() -> None:
    render_html(
        """
        <style>
            .registration-header-card {
                margin-bottom: 18px;
                padding: 24px 26px 22px 26px;
                border-radius: 24px;
                background:
                    radial-gradient(circle at 100% 0%, rgba(169,28,255,0.20), transparent 32%),
                    radial-gradient(circle at 0% 100%, rgba(255,75,170,0.12), transparent 34%),
                    linear-gradient(145deg, rgba(22,20,42,0.99), rgba(10,9,25,0.99));
                border: 1px solid rgba(255,75,170,0.32);
                box-shadow: 0 18px 46px rgba(0,0,0,0.22), 0 0 26px rgba(169,28,255,0.10);
            }

            .registration-kicker {
                color: #FF79C4;
                font-size: 0.76rem;
                font-weight: 900;
                letter-spacing: 0.16em;
                text-transform: uppercase;
                margin-bottom: 10px;
            }

            .registration-title {
                color: #FFFFFF;
                font-size: 2rem;
                font-weight: 950;
                letter-spacing: -0.035em;
                line-height: 1.05;
            }

            .registration-subtitle {
                margin-top: 9px;
                color: rgba(255,255,255,0.68);
                font-size: 0.94rem;
                line-height: 1.45;
            }

            .registration-section {
                margin: 8px 0 14px 0;
                padding: 14px 16px;
                border-radius: 16px;
                background: rgba(255,255,255,0.96);
                border: 1px solid rgba(255,75,170,0.38);
                box-shadow:
                    0 8px 18px rgba(169,28,255,0.08),
                    inset 0 1px 0 rgba(255,255,255,0.92);
            }

            .registration-section-title {
                color: #1E1729;
                font-size: 0.95rem;
                font-weight: 900;
                letter-spacing: 0.015em;
            }

            .registration-section-text {
                margin-top: 4px;
                color: rgba(30,23,41,0.72);
                font-size: 0.80rem;
                line-height: 1.4;
            }

            .registration-note {
                margin: 0 0 14px 0;
                padding: 12px 14px;
                border-radius: 14px;
                color: rgba(38,31,53,0.78);
                background: rgba(255,255,255,0.48);
                border: 1px solid rgba(90,76,118,0.12);
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.68);
                font-size: 0.83rem;
                line-height: 1.45;
            }

            .registration-note strong {
                color: #FF79C4;
            }

            [data-testid="stForm"] {
                padding: 20px !important;
                border-radius: 24px !important;
                background:
                    radial-gradient(circle at top left, rgba(255,255,255,0.92) 0%, rgba(255,255,255,0.00) 32%),
                    radial-gradient(circle at bottom right, rgba(208,212,223,0.72) 0%, rgba(208,212,223,0.00) 34%),
                    linear-gradient(180deg, #F7F8FC 0%, #ECEEF4 38%, #DCE0E9 72%, #CED3DE 100%) !important;
                border: 1px solid rgba(63,53,83,0.14) !important;
                box-shadow:
                    0 18px 46px rgba(0,0,0,0.18),
                    inset 0 1px 0 rgba(255,255,255,0.80) !important;
                overflow: visible !important;
            }

            [data-testid="stForm"] form,
            [data-testid="stForm"] div[data-testid="stVerticalBlock"],
            [data-testid="stForm"] div[data-testid="column"],
            [data-testid="stForm"] div[data-testid="stElementContainer"],
            [data-testid="stForm"] div[data-testid="stTextInput"],
            [data-testid="stForm"] div[data-testid="stTextArea"],
            [data-testid="stForm"] div[data-testid="stSelectbox"],
            [data-testid="stForm"] div[data-testid="stDateInput"] {
                overflow: visible !important;
            }

            [data-testid="stForm"] div[data-testid="stTextInput"],
            [data-testid="stForm"] div[data-testid="stTextArea"],
            [data-testid="stForm"] div[data-testid="stSelectbox"],
            [data-testid="stForm"] div[data-testid="stDateInput"] {
                padding: 4px 0 !important;
                position: relative !important;
            }

            [data-testid="stForm"] label {
                color: #1E1828 !important;
                -webkit-text-fill-color: #1E1828 !important;
                font-size: 0.86rem !important;
                font-weight: 800 !important;
            }

            [data-testid="stForm"] div[data-baseweb="input"] > div,
            [data-testid="stForm"] div[data-baseweb="select"] > div {
                min-height: 48px !important;
                border-radius: 13px !important;
                border: 1px solid rgba(255,75,170,0.58) !important;
                background: #FFFFFF !important;
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.88) !important;
                color: #1E1828 !important;
                transition:
                    transform 0.20s ease,
                    box-shadow 0.20s ease,
                    border-color 0.20s ease !important;
                transform-origin: center center;
                position: relative !important;
                z-index: 1 !important;
            }

            [data-testid="stForm"] div[data-baseweb="input"] > div:hover,
            [data-testid="stForm"] div[data-baseweb="select"] > div:hover {
                transform: scale(1.012) !important;
                border-color: rgba(255,75,170,0.82) !important;
                box-shadow:
                    0 0 0 1px rgba(255,75,170,0.12),
                    0 0 14px rgba(169,28,255,0.12),
                    0 8px 18px rgba(169,28,255,0.10),
                    inset 0 1px 0 rgba(255,255,255,0.03) !important;
                z-index: 8 !important;
            }

            [data-testid="stForm"] div[data-testid="stTextInput"]:hover,
            [data-testid="stForm"] div[data-testid="stSelectbox"]:hover,
            [data-testid="stForm"] div[data-testid="stDateInput"]:hover {
                z-index: 15 !important;
            }

            [data-testid="stForm"] div[data-baseweb="textarea"] {
                border: none !important;
                background: transparent !important;
                box-shadow: none !important;
            }

            [data-testid="stForm"] div[data-testid="stTextArea"]:hover {
                z-index: 15 !important;
            }

            [data-testid="stForm"] div[data-testid="stTextArea"] > div,
            [data-testid="stForm"] div[data-testid="stTextArea"] > div > div {
                overflow: visible !important;
                border-radius: 13px !important;
                background: transparent !important;
                box-shadow: none !important;
            }

            [data-testid="stForm"] div[data-baseweb="textarea"] > div {
                min-height: 124px !important;
                border-radius: 13px !important;
                border: 1px solid rgba(255,75,170,0.58) !important;
                background: #FFFFFF !important;
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.88) !important;
                transition:
                    transform 0.18s ease,
                    box-shadow 0.18s ease,
                    border-color 0.18s ease !important;
                transform-origin: center center;
                position: relative !important;
                z-index: 1 !important;
                overflow: visible !important;
            }

            [data-testid="stForm"] div[data-baseweb="textarea"] > div:hover,
            [data-testid="stForm"] div[data-baseweb="textarea"] > div:focus-within {
                transform: scale(1.012) !important;
                border-color: rgba(255,75,170,0.82) !important;
                box-shadow:
                    0 0 0 1px rgba(255,75,170,0.12),
                    0 0 14px rgba(169,28,255,0.12),
                    0 8px 18px rgba(169,28,255,0.10),
                    inset 0 1px 0 rgba(255,255,255,0.03) !important;
                z-index: 8 !important;
            }

            [data-testid="stForm"] div[data-testid="stTextArea"] textarea,
            [data-testid="stForm"] div[data-baseweb="textarea"] textarea {
                min-height: 124px !important;
                border: none !important;
                border-radius: 13px !important;
                outline: none !important;
                background: transparent !important;
                box-shadow: none !important;
                color: #1E1828 !important;
                position: relative !important;
                z-index: 1 !important;
                resize: vertical !important;
            }

            [data-testid="stForm"] div[data-baseweb="textarea"] > div:focus-within,
            [data-testid="stForm"] div[data-baseweb="input"] > div:focus-within,
            [data-testid="stForm"] div[data-baseweb="select"] > div:focus-within {
                border: 1px solid rgba(255,75,170,0.88) !important;
                box-shadow:
                    0 0 0 1px rgba(255,75,170,0.14),
                    0 0 16px rgba(169,28,255,0.14),
                    0 8px 18px rgba(169,28,255,0.10) !important;
                z-index: 10 !important;
            }

            /* Labels dos campos em preto para permanecerem legíveis sobre o fundo cinza */
            [data-testid="stForm"] div[data-testid="stSelectbox"] label,
            [data-testid="stForm"] div[data-testid="stSelectbox"] label p,
            [data-testid="stForm"] div[data-testid="stSelectbox"] label span,
            [data-testid="stForm"] div[data-testid="stSelectbox"] [data-testid="stWidgetLabel"],
            [data-testid="stForm"] div[data-testid="stSelectbox"] [data-testid="stWidgetLabel"] p {
                color: #1E1828 !important;
                -webkit-text-fill-color: #1E1828 !important;
            }

            /* Campos do formulário: caixas brancas e texto preto sobre o fundo cinza */
            [data-testid="stForm"] div[data-baseweb="input"] input,
            [data-testid="stForm"] div[data-baseweb="select"] *,
            [data-testid="stForm"] div[data-testid="stDateInput"] input {
                color: #1E1828 !important;
                -webkit-text-fill-color: #1E1828 !important;
            }

            [data-testid="stForm"] div[data-baseweb="input"] input::placeholder,
            [data-testid="stForm"] div[data-testid="stDateInput"] input::placeholder {
                color: rgba(30,24,40,0.52) !important;
                -webkit-text-fill-color: rgba(30,24,40,0.52) !important;
            }

            [data-testid="stForm"] div[data-testid="stTextArea"] div[data-baseweb="textarea"] > div {
                background: #FFFFFF !important;
            }

            [data-testid="stForm"] div[data-testid="stTextArea"] textarea {
                background: transparent !important;
                color: #1E1828 !important;
                -webkit-text-fill-color: #1E1828 !important;
            }

            [data-testid="stForm"] div[data-testid="stTextArea"] textarea::placeholder {
                color: rgba(30,24,40,0.52) !important;
                -webkit-text-fill-color: rgba(30,24,40,0.52) !important;
            }


            /* Lista clicável das empresas cadastradas com fundo cinza igual ao menu */
            .st-key-contracts_names_list {
                margin-top: 10px !important;
                overflow: hidden !important;
                border-radius: 18px !important;
                border: 1px solid rgba(255,75,170,0.34) !important;
                background:
                    radial-gradient(circle at top left, rgba(255,255,255,0.94) 0%, rgba(255,255,255,0.00) 34%),
                    radial-gradient(circle at bottom right, rgba(208,212,223,0.72) 0%, rgba(208,212,223,0.00) 36%),
                    linear-gradient(180deg, #F7F8FC 0%, #ECEEF4 42%, #DCE0E9 72%, #CED3DE 100%) !important;
                box-shadow:
                    0 18px 42px rgba(0,0,0,0.18),
                    0 0 0 1px rgba(169,28,255,0.08),
                    0 0 18px rgba(169,28,255,0.08) !important;
            }

            .st-key-contracts_names_list div[data-testid="stVerticalBlock"] {
                gap: 0 !important;
            }

            .st-key-contracts_names_list div[data-testid="stElementContainer"] {
                margin: 0 !important;
                padding: 0 !important;
            }

            .st-key-contracts_names_list .stButton > button {
                width: 100% !important;
                min-height: 45px !important;
                margin: 0 !important;
                padding: 11px 18px !important;
                justify-content: flex-start !important;
                border: none !important;
                border-bottom: 1px solid rgba(255,75,170,0.10) !important;
                border-radius: 0 !important;
                color: #211A30 !important;
                background: rgba(255,255,255,0.92) !important;
                box-shadow: none !important;
                font-size: 0.90rem !important;
                font-weight: 700 !important;
                line-height: 1.25 !important;
                text-align: left !important;
                transition:
                    background 0.18s ease,
                    color 0.18s ease,
                    padding-left 0.18s ease,
                    box-shadow 0.18s ease !important;
            }

            .st-key-contracts_names_list div[data-testid="stElementContainer"]:nth-child(even) .stButton > button {
                background: rgba(232,235,242,0.96) !important;
            }

            .st-key-contracts_names_list .stButton > button:hover {
                transform: none !important;
                padding-left: 22px !important;
                color: #211A30 !important;
                background: linear-gradient(90deg, rgba(255,75,170,0.16), rgba(169,28,255,0.11), rgba(255,255,255,0.94)) !important;
                box-shadow: inset 4px 0 0 #FF4BAA, 0 0 20px rgba(169,28,255,0.12) !important;
            }

            /* Mantém a lista igual à tabela aprovada: linhas juntas e nomes alinhados à esquerda */
            .st-key-contracts_names_list,
            .st-key-contracts_names_list > div,
            .st-key-contracts_names_list div[data-testid="stVerticalBlock"],
            .st-key-contracts_names_list div[data-testid="stElementContainer"],
            .st-key-contracts_names_list .stButton {
                margin-top: 0 !important;
                margin-bottom: 0 !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
                gap: 0 !important;
                row-gap: 0 !important;
            }

            .st-key-contracts_names_list .stButton > button {
                min-height: 45px !important;
                height: 45px !important;
                display: flex !important;
                align-items: center !important;
                justify-content: flex-start !important;
                padding: 0 18px !important;
                margin: 0 !important;
                border-radius: 0 !important;
            }

            .st-key-contracts_names_list .stButton > button p,
            .st-key-contracts_names_list .stButton > button span,
            .st-key-contracts_names_list .stButton > button div {
                width: 100% !important;
                margin: 0 !important;
                padding: 0 !important;
                text-align: left !important;
                justify-content: flex-start !important;
                line-height: 1.15 !important;
            }

            .contracts-names-clickable-header {
                padding: 14px 18px;
                color: #FFFFFF;
                font-size: 0.90rem;
                font-weight: 950;
                letter-spacing: 0.02em;
                text-transform: uppercase;
                background:
                    linear-gradient(90deg, rgba(255,75,170,0.42), rgba(169,28,255,0.42)),
                    rgba(20,15,43,0.98);
                border-bottom: 1px solid rgba(255,75,170,0.30);
            }

            /* Página de visualização do cadastro preenchido */
            .contract-detail-shell {
                padding: 20px !important;
                border-radius: 24px !important;
                background:
                    radial-gradient(circle at top left, rgba(255,255,255,0.92) 0%, rgba(255,255,255,0.00) 32%),
                    radial-gradient(circle at bottom right, rgba(208,212,223,0.72) 0%, rgba(208,212,223,0.00) 34%),
                    linear-gradient(180deg, #F7F8FC 0%, #ECEEF4 38%, #DCE0E9 72%, #CED3DE 100%) !important;
                border: 1px solid rgba(63,53,83,0.14) !important;
                box-shadow:
                    0 18px 46px rgba(0,0,0,0.18),
                    inset 0 1px 0 rgba(255,255,255,0.80) !important;
            }

            .contract-detail-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 14px 16px;
                margin-bottom: 16px;
            }

            .contract-detail-grid.three-columns {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }

            .contract-detail-field {
                min-width: 0;
            }

            .contract-detail-field.full-width {
                grid-column: 1 / -1;
            }

            .contract-detail-label {
                margin-bottom: 7px;
                color: #1E1828;
                font-size: 0.84rem;
                font-weight: 780;
            }

            .contract-detail-value {
                min-height: 48px;
                display: flex;
                align-items: center;
                padding: 12px 14px;
                border-radius: 13px;
                border: 1px solid rgba(255,75,170,0.58);
                background: #FFFFFF;
                color: #1E1828;
                font-size: 0.91rem;
                line-height: 1.35;
                word-break: break-word;
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
                transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
            }

            .contract-detail-value:hover {
                transform: scale(1.012);
                border-color: rgba(255,75,170,0.82);
                box-shadow:
                    0 0 0 1px rgba(255,75,170,0.12),
                    0 0 14px rgba(169,28,255,0.12),
                    0 8px 18px rgba(169,28,255,0.10),
                    inset 0 1px 0 rgba(255,255,255,0.03);
            }

            .contract-detail-value.long-text {
                min-height: 94px;
                align-items: flex-start;
                white-space: pre-wrap;
            }

            .st-key-contract_detail_back .stButton > button {
                width: auto !important;
                min-height: 42px !important;
                margin-bottom: 14px !important;
                padding: 0 18px !important;
                border-radius: 13px !important;
            }

            @media (max-width: 900px) {
                .contract-detail-grid,
                .contract-detail-grid.three-columns {
                    grid-template-columns: 1fr;
                }
            }

            [data-testid="stForm"] input,
            [data-testid="stForm"] textarea,
            [data-testid="stForm"] div[data-baseweb="select"] * {
                color: #FFFFFF !important;
                -webkit-text-fill-color: #FFFFFF !important;
            }

            [data-testid="stForm"] input::placeholder,
            [data-testid="stForm"] textarea::placeholder {
                color: rgba(255,255,255,0.44) !important;
                -webkit-text-fill-color: rgba(255,255,255,0.44) !important;
            }

            [data-testid="stForm"] .stButton > button,
            [data-testid="stForm"] button[kind="secondaryFormSubmit"] {
                width: 100% !important;
                min-height: 52px !important;
                margin-top: 10px !important;
                border: none !important;
                border-radius: 14px !important;
                color: #FFFFFF !important;
                font-size: 0.96rem !important;
                font-weight: 900 !important;
                background: linear-gradient(90deg, #FF4BAA 0%, #D73AFF 54%, #8C2BFF 100%) !important;
                box-shadow: 0 14px 30px rgba(169,28,255,0.26) !important;
            }

            .registration-required {
                color: #FF79C4;
                font-weight: 900;
            }
        </style>
        """
    )


# =========================================================
# SESSÃO DE NAVEGAÇÃO PARA LINKS INTERNOS
# =========================================================
@st.cache_resource
def get_navigation_session_registry() -> set[str]:
    """Mantém tokens temporários para preservar o login ao usar os links do submenu."""
    return set()


def create_navigation_session_token() -> str:
    token = uuid.uuid4().hex
    get_navigation_session_registry().add(token)
    st.session_state.navigation_session_token = token
    st.query_params["session"] = token
    return token


def restore_navigation_session_from_url() -> None:
    """Restaura o login quando um link interno recarrega a página com token válido."""
    if st.session_state.get("authenticated", False):
        return

    token = normalize_text(st.query_params.get("session", ""))

    if token and token in get_navigation_session_registry():
        st.session_state.authenticated = True
        st.session_state.navigation_session_token = token
        st.session_state.auth_error = ""


def revoke_navigation_session_token() -> None:
    token = normalize_text(st.session_state.get("navigation_session_token", ""))

    if token:
        get_navigation_session_registry().discard(token)

    st.session_state.navigation_session_token = ""


# =========================================================
# LOGIN
# =========================================================
def check_login(username: str, password: str) -> bool:
    expected_user = st.secrets.get("APP_USERNAME", "oppitech")
    expected_password = st.secrets.get("APP_PASSWORD", "100316Rahi*")

    return username == expected_user and password == expected_password


def render_login_page() -> None:
    apply_login_css()
    logo_data_uri = get_logo_data_uri()

    left_column, right_column = st.columns([0.86, 1.14], gap="large")

    with left_column:
        logo_html = (
            f'<div class="login-logo-wrap"><img src="{logo_data_uri}" class="login-logo-img" alt="Oppi Tech"></div>'
            if logo_data_uri
            else '<div class="login-logo-wrap"><div class="login-logo-fallback"></div></div>'
        )

        render_html(
            f"""
            <div class="login-brand-panel">
                {logo_html}
                <div class="login-brand-title">
                    Dashboard
                    <span class="login-brand-highlight">Oppi Comercial</span>
                </div>
                <div class="login-brand-subtitle">Painel de gestão comercial</div>
                <div class="login-accent-line"></div>
                <div class="login-benefit">
                    <div class="login-benefit-icon">🛡️</div>
                    <div>Segurança, performance e inteligência para impulsionar seus resultados.</div>
                </div>
            </div>
            """
        )

    with right_column:
        render_html('<div class="login-right-spacer"></div>')

        with st.form("login_form", clear_on_submit=False):
            render_html(
                """
                <div class="login-top-icon">🛡️</div>
                <div class="login-card-title">Acesse o painel comercial da Oppi Tech</div>
                <div class="login-card-subtitle">Faça login para continuar</div>
                """
            )

            username = st.text_input(
                "Usuário",
                placeholder="Digite seu usuário",
            )

            password = st.text_input(
                "Senha",
                type="password",
                placeholder="Digite sua senha",
            )

            submitted = st.form_submit_button(
                "Entrar",
                use_container_width=True,
            )

            render_html(
                """
                <div class="login-forgot-row">
                    <div class="login-forgot-line"></div>
                    <div class="login-forgot-text">Esqueceu sua senha?</div>
                    <div class="login-forgot-line"></div>
                </div>
                """
            )

        if submitted:
            if check_login(username, password):
                st.session_state.authenticated = True
                st.session_state.auth_error = ""
                create_navigation_session_token()
                st.rerun()
            else:
                st.session_state.auth_error = "Usuário ou senha inválidos."

        if st.session_state.auth_error:
            render_html(
                f'<div class="login-error">{html.escape(st.session_state.auth_error)}</div>'
            )


# =========================================================
# SIDEBAR
# =========================================================
def _query_param_value(name: str) -> str:
    value = st.query_params.get(name, "")

    if isinstance(value, list):
        return normalize_text(value[-1] if value else "")

    return normalize_text(value)


def _sync_navigation_from_query_params() -> None:
    requested_page = normalize_search_text(_query_param_value("page"))
    requested_contracts_page = normalize_search_text(_query_param_value("contracts"))

    if requested_page == "visao-geral":
        st.session_state.selected_page = "Visão Geral"
    elif requested_page == "pesos-e-medidas":
        st.session_state.selected_page = "Pesos e Medidas"
    elif requested_page == "cadastro":
        st.session_state.selected_page = "Cadastro"

        if requested_contracts_page == "todos":
            st.session_state.selected_cadastro_subpage = "Todos os contratos"
        elif requested_contracts_page == "novo":
            st.session_state.selected_cadastro_subpage = "Novo contrato"


def render_sidebar() -> str:
    _sync_navigation_from_query_params()

    with st.sidebar:
        logo_data_uri = get_logo_data_uri()
        logo_html = (
            f'<div class="side-logo-wrap"><img src="{logo_data_uri}" class="side-logo-img" alt="Oppi Tech"></div>'
            if logo_data_uri
            else '<div class="side-logo-wrap"><div class="side-logo-fallback"></div></div>'
        )

        render_html(
            f"""
            {logo_html}
            <div class="side-title">
                Dashboard
                <span class="side-highlight">Oppi Comercial</span>
            </div>
            <div class="side-subtitle">Painel de gestão comercial</div>
            <div class="side-line"></div>
            """
        )

        if st.session_state.selected_page == "Propostas":
            st.session_state.selected_page = "Cadastro"

        if st.session_state.selected_page not in ["Visão Geral", "Cadastro", "Pesos e Medidas"]:
            st.session_state.selected_page = "Visão Geral"

        overview_active = "active" if st.session_state.selected_page == "Visão Geral" else ""
        cadastro_active = "active" if st.session_state.selected_page == "Cadastro" else ""
        scores_active = "active" if st.session_state.selected_page == "Pesos e Medidas" else ""
        details_open = "open" if st.session_state.selected_page == "Cadastro" else ""
        novo_active = "active" if st.session_state.get("selected_cadastro_subpage", "Novo contrato") == "Novo contrato" else ""
        todos_active = "active" if st.session_state.get("selected_cadastro_subpage", "Novo contrato") == "Todos os contratos" else ""
        navigation_token = normalize_text(st.session_state.get("navigation_session_token", ""))
        session_query = f"&session={navigation_token}" if navigation_token else ""

        render_html(
            f"""
            <nav class="oppi-side-nav">
                <a class="oppi-nav-link {overview_active}" href="?page=visao-geral{session_query}" target="_self">
                    <span class="oppi-nav-dot"></span>
                    <span>Visão Geral</span>
                </a>

                <details class="oppi-cadastro-details {cadastro_active}" {details_open}>
                    <summary class="oppi-nav-summary">
                        <span class="oppi-nav-dot"></span>
                        <span>Cadastro</span>
                        <span class="oppi-nav-arrow">›</span>
                    </summary>

                    <div class="oppi-cadastro-flyout">
                        <div class="oppi-flyout-title">Cadastro</div>
                        <a class="oppi-flyout-link {novo_active}" href="?page=cadastro&contracts=novo{session_query}" target="_self">Novo contrato</a>
                        <a class="oppi-flyout-link {todos_active}" href="?page=cadastro&contracts=todos{session_query}" target="_self">Todos os contratos</a>
                    </div>
                </details>

                <a class="oppi-nav-link {scores_active}" href="?page=pesos-e-medidas{session_query}" target="_self">
                    <span class="oppi-nav-dot"></span>
                    <span>Pesos e Medidas</span>
                </a>
            </nav>
            """
        )

        # Fecha somente a caixinha lateral do Cadastro ao clicar fora dela.
        # O design aprovado do menu e do submenu permanece intacto.
        components.html(
            """
            <script>
                (function () {
                    function getParentDocument() {
                        try {
                            if (window.frameElement && window.frameElement.ownerDocument) {
                                return window.frameElement.ownerDocument;
                            }
                        } catch (error) {}

                        try {
                            return window.parent.document;
                        } catch (error) {
                            return null;
                        }
                    }

                    function installOutsideClickHandler() {
                        const parentDocument = getParentDocument();

                        if (!parentDocument) {
                            window.setTimeout(installOutsideClickHandler, 250);
                            return;
                        }

                        const handlerKey = "__oppiCadastroFlyoutOutsideClickHandler__";

                        if (window.parent[handlerKey]) {
                            return;
                        }

                        window.parent[handlerKey] = true;

                        parentDocument.addEventListener(
                            "pointerdown",
                            function (event) {
                                const openDetails = parentDocument.querySelector(
                                    ".oppi-cadastro-details[open]"
                                );

                                if (!openDetails) {
                                    return;
                                }

                                if (openDetails.contains(event.target)) {
                                    return;
                                }

                                openDetails.removeAttribute("open");
                            },
                            true
                        );

                        parentDocument.addEventListener(
                            "keydown",
                            function (event) {
                                if (event.key !== "Escape") {
                                    return;
                                }

                                const openDetails = parentDocument.querySelector(
                                    ".oppi-cadastro-details[open]"
                                );

                                if (openDetails) {
                                    openDetails.removeAttribute("open");
                                }
                            },
                            true
                        );
                    }

                    installOutsideClickHandler();
                })();
            </script>
            """,
            height=0,
            scrolling=False,
        )

        render_html(
            """
            <div class="side-tip">
                <div class="side-tip-icon">🛡️</div>
                <div class="side-tip-text">Segurança, performance e inteligência para impulsionar seus resultados.</div>
            </div>
            """
        )

        if st.button("Sair", use_container_width=True, key="sidebar_logout"):
            revoke_navigation_session_token()
            st.session_state.authenticated = False
            st.session_state.auth_error = ""
            st.query_params.clear()
            st.rerun()

    return st.session_state.selected_page


# =========================================================
# COMPONENTES DO DASHBOARD
# =========================================================
def render_metric_card(
    title: str,
    value: str,
    note: str,
    icon: str,
    background: str,
) -> None:
    render_html(
        f"""
        <div class="metric-card">
            <div class="metric-icon" style="background:{background};">{icon}</div>
            <div class="metric-label">{html.escape(title)}</div>
            <div class="metric-value">{html.escape(value)}</div>
            <div class="metric-note">{html.escape(note)}</div>
        </div>
        """
    )


def render_status_summary(filtered_df: pd.DataFrame) -> None:
    statuses = [
        ("Novo Lead", "#697BFF"),
        ("Conversando", "#C67A25"),
        ("Sem interesse", "#45B6C6"),
        ("Não responde", "#DF5578"),
        ("Proposta", "#5C9DFF"),
        ("Reunião", "#A65BDB"),
        ("Fechado", "#70C854"),
    ]

    total = max(len(filtered_df), 1)
    rows_html = ""

    for status_name, color in statuses:
        count = int((filtered_df["_status_grupo"] == status_name).sum())
        percent = round((count / total) * 100)

        rows_html += (
            f'<div class="status-row">'
            f'<div class="status-left"><span style="color:{color};">●</span>&nbsp;&nbsp;{status_name}</div>'
            f'<div class="status-count">{count}</div>'
            f'<div class="status-percent">{percent}%</div>'
            f'</div>'
        )

    render_html(
        f"""
        <div class="section-heading">Resumo por status</div>
        <div class="section-subtitle">Distribuição atual dos leads no comercial.</div>
        <div class="status-wrap">{rows_html}</div>
        """
    )



def render_phone_copy_button(phone: str, row_key: str) -> None:
    """Renderiza um botão gradiente que copia o telefone no navegador."""
    safe_phone = normalize_text(phone)
    phone_json = json.dumps(safe_phone, ensure_ascii=False)

    components.html(
        f"""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8" />
            <style>
                * {{
                    box-sizing: border-box;
                }}

                html, body {{
                    margin: 0;
                    padding: 0;
                    width: 100%;
                    height: 30px;
                    overflow: hidden;
                    background: transparent;
                    font-family: Arial, sans-serif;
                }}

                button {{
                    width: 100%;
                    height: 29px;
                    border: none;
                    border-radius: 7px;
                    cursor: pointer;
                    color: #FFFFFF;
                    font-size: 11px;
                    font-weight: 800;
                    letter-spacing: 0.01em;
                    background: linear-gradient(90deg, #FF4BAA 0%, #A91CFF 100%);
                    box-shadow: 0 8px 18px rgba(169, 28, 255, 0.22);
                    transition:
                        filter 0.16s ease,
                        box-shadow 0.16s ease;
                }}

                button:hover {{
                    filter: brightness(1.08);
                    box-shadow: 0 10px 20px rgba(169, 28, 255, 0.30);
                }}

                button:active {{
                    filter: brightness(0.96);
                }}

                button.copied {{
                    background: linear-gradient(90deg, #20B56B 0%, #55DF7D 100%);
                    box-shadow: 0 8px 18px rgba(32, 181, 107, 0.20);
                }}
            </style>
        </head>
        <body>
            <button id="copy-{html.escape(row_key)}" type="button" onclick="copyPhone()">
                Copiar
            </button>

            <script>
                const phoneValue = {phone_json};
                const button = document.getElementById("copy-{html.escape(row_key)}");

                function showCopied() {{
                    button.textContent = "Copiado!";
                    button.classList.add("copied");

                    setTimeout(() => {{
                        button.textContent = "Copiar";
                        button.classList.remove("copied");
                    }}, 1400);
                }}

                function fallbackCopy(value) {{
                    const textarea = document.createElement("textarea");
                    textarea.value = value;
                    textarea.setAttribute("readonly", "");
                    textarea.style.position = "fixed";
                    textarea.style.opacity = "0";
                    document.body.appendChild(textarea);
                    textarea.select();
                    textarea.setSelectionRange(0, textarea.value.length);
                    document.execCommand("copy");
                    document.body.removeChild(textarea);
                    showCopied();
                }}

                async function copyPhone() {{
                    if (!phoneValue) {{
                        button.textContent = "Sem número";
                        setTimeout(() => {{
                            button.textContent = "Copiar";
                        }}, 1400);
                        return;
                    }}

                    try {{
                        if (navigator.clipboard && window.isSecureContext) {{
                            await navigator.clipboard.writeText(phoneValue);
                            showCopied();
                        }} else {{
                            fallbackCopy(phoneValue);
                        }}
                    }} catch (error) {{
                        fallbackCopy(phoneValue);
                    }}
                }}
            </script>
        </body>
        </html>
        """,
        height=30,
        scrolling=False,
    )


def render_latest_calls_section(
    filtered_df: pd.DataFrame,
    columns: dict,
    source_df: pd.DataFrame,
) -> None:
    statuses = [
        ("Novo Lead", "✦", "#E8F0FF", "#5C8BFF"),
        ("Conversando", "•", "#F8EFE6", "#B37A2A"),
        ("Sem interesse", "⊘", "#E9F8FA", "#2F9FB3"),
        ("Não responde", "⚑", "#FBECEF", "#DA5C78"),
        ("Proposta", "▤", "#EAF2FF", "#5C9DFF"),
        ("Reunião", "◉", "#F3EAFE", "#A65BDB"),
        ("Fechado", "✓", "#EAF8EF", "#58B97A"),
    ]

    selected_card_key = "ultimos_chamados_status_selecionado"

    if selected_card_key not in st.session_state:
        st.session_state[selected_card_key] = None

    valid_dates = source_df["_data_chamado"].dropna()

    if valid_dates.empty:
        date_max = date.today()
        date_min = date_max - timedelta(days=30)
    else:
        date_min = valid_dates.min().date()
        date_max = valid_dates.max().date()

    seller_options = sorted(
        [
            seller
            for seller in source_df["_vendedor"].dropna().astype(str).unique().tolist()
            if normalize_text(seller)
        ]
    )

    render_html(
        """
        <div class="latest-calls-shell">
            <div class="latest-calls-head">
                <div>
                    <div class="latest-filter-title">Filtros dos chamados</div>
                    <div class="latest-filter-subtitle">Refine os resultados por vendedor, status, período ou empresa.</div>
                </div>
                <div class="latest-calls-chip">Filtros</div>
            </div>
        </div>
        """
    )

    filter_1, filter_2, filter_3, filter_4 = st.columns(4, gap="medium")

    with filter_1:
        st.selectbox(
            "Vendedor",
            ["Todos os vendedores"] + seller_options,
            key="dashboard_filter_seller",
        )

    with filter_2:
        st.selectbox(
            "Status",
            ["Todos os status"] + STATUS_OPTIONS,
            key="dashboard_filter_status",
        )

    with filter_3:
        st.date_input(
            "Período",
            min_value=date_min,
            max_value=max(date_max, date.today()),
            key="dashboard_filter_period",
        )

    with filter_4:
        st.text_input(
            "Buscar empresa ou telefone",
            placeholder="Digite para buscar...",
            key="dashboard_filter_search",
        )

    st.write("")

    def choose_status(status_name: str) -> None:
        st.session_state[selected_card_key] = status_name

    selected_status = st.session_state.get(selected_card_key)
    card_columns = st.columns(7, gap="small")

    for column, (status_name, icon, bg_color, icon_color) in zip(card_columns, statuses):
        count = int((filtered_df["_status_grupo"] == status_name).sum())
        active = selected_status == status_name

        border = "1px solid rgba(255, 75, 170, 0.85)" if active else "1px solid rgba(255,255,255,0.06)"
        shadow = "0 0 0 1px rgba(169, 28, 255, 0.18), 0 18px 46px rgba(0,0,0,0.28), 0 0 22px rgba(255, 75, 170, 0.14)" if active else "0 18px 46px rgba(0,0,0,0.22)"

        with column:
            render_html(
                f"""
                <div class="latest-status-card" style="border:{border}; box-shadow:{shadow};">
                    <div class="latest-status-top">
                        <div class="latest-status-icon" style="background:{bg_color}; color:{icon_color};">{icon}</div>
                        <div class="latest-status-name">{html.escape(status_name)}</div>
                    </div>
                    <div class="latest-status-number">{count}</div>
                    <div class="latest-status-caption">registros nesta sessão</div>
                </div>
                """
            )

            st.button(
                "Ver nomes",
                key=f"btn_ultimos_{status_name}",
                use_container_width=True,
                on_click=choose_status,
                args=(status_name,),
            )

    selected_status = st.session_state.get(selected_card_key)
    search_term = normalize_text(st.session_state.get("dashboard_filter_search", ""))
    global_search_active = bool(search_term)

    # Quando o campo de busca estiver preenchido, a tabela exibe todos os
    # registros encontrados na planilha inteira, independentemente do card de
    # status que estava selecionado anteriormente.
    if global_search_active:
        selected_df = filtered_df.copy()
    else:
        if not selected_status:
            render_html(
                """
                <div class="latest-placeholder-card">
                    Selecione um status clicando em “Ver nomes” para visualizar os registros.
                </div>
                """
            )
            return

        selected_df = filtered_df[filtered_df["_status_grupo"] == selected_status].copy()

    selected_df = selected_df.sort_values(
        ["_data_chamado", "_empresa"],
        ascending=[False, True],
    )

    display_df = pd.DataFrame(
        {
            "Empresa": selected_df["_empresa"],
            "Telefone": selected_df["_telefone"],
            "E-mail": safe_series(selected_df, columns.get("email")),
            "CNPJ": safe_series(selected_df, columns.get("cnpj")),
            "Status": selected_df["_status_grupo"],
            "Vendedor": selected_df["_vendedor"],
            "Data": selected_df["_data_chamado"].dt.strftime("%d/%m/%Y").fillna(""),
        }
    )

    if display_df.empty:
        st.info("Nenhum chamado encontrado para este status no período selecionado.")
        return

    editor_df = display_df.copy()
    editor_df["_sheet_row"] = selected_df["_sheet_row"].astype(int).values

    flash_message = st.session_state.pop("status_auto_save_success", None)
    if flash_message:
        st.success(flash_message)

    flash_error = st.session_state.pop("status_auto_save_error", None)
    if flash_error:
        st.error(flash_error)

    render_html(
        """
        <div class="premium-inline-hint">
            Altere o <strong>Status</strong> pelo seletor ou use <strong>Copiar</strong> para copiar o telefone.
        </div>
        """
    )

    with st.container(key="compact_inline_table"):
        header_columns = st.columns(
            [3.15, 1.45, 0.92, 1.65, 1.35, 0.90],
            gap="small",
        )

        header_labels = [
            "Empresa",
            "Telefone",
            "Copiar",
            "Status",
            "Vendedor",
            "Data",
        ]

        for column, label in zip(header_columns, header_labels):
            with column:
                render_html(f'<div class="premium-inline-table-header">{html.escape(label)}</div>')

        status_column_name = columns.get("status")

        for _, row in editor_df.iterrows():
            sheet_row = int(row["_sheet_row"])
            original_status = normalize_text(row["Status"])

            if original_status not in STATUS_OPTIONS:
                original_status = "Novo Lead"

            row_columns = st.columns(
                [3.15, 1.45, 0.92, 1.65, 1.35, 0.90],
                gap="small",
            )

            with row_columns[0]:
                render_html(
                    f'<div class="premium-inline-cell">{html.escape(normalize_text(row["Empresa"]) or "Sem empresa")}</div>'
                )

            with row_columns[1]:
                render_html(
                    f'<div class="premium-inline-cell phone">{html.escape(normalize_text(row["Telefone"]) or "Sem número")}</div>'
                )

            with row_columns[2]:
                render_phone_copy_button(
                    normalize_text(row["Telefone"]),
                    row_key=f"phone-{sheet_row}",
                )

            status_widget_key = f"inline_status_{sheet_row}_{normalize_search_text(original_status).replace(' ', '_')}"

            def save_inline_status(
                sheet_row_value: int = sheet_row,
                widget_key: str = status_widget_key,
                previous_status: str = original_status,
            ) -> None:
                new_status = normalize_text(st.session_state.get(widget_key, previous_status))

                if new_status == previous_status:
                    return

                if not status_column_name:
                    st.session_state["status_auto_save_error"] = (
                        "Não encontrei a coluna Status na planilha."
                    )
                    return

                try:
                    update_statuses_in_sheet(
                        changes=[
                            {
                                "sheet_row": sheet_row_value,
                                "status": new_status,
                            }
                        ],
                        status_column_name=status_column_name,
                        updated_at_column_name=columns.get("ultima_atualizacao"),
                    )

                    st.session_state["status_auto_save_success"] = (
                        f"Status alterado para “{new_status}” e salvo diretamente na planilha."
                    )
                except Exception as error:
                    st.session_state["status_auto_save_error"] = (
                        "Não consegui atualizar o status diretamente na planilha: "
                        f"{error}"
                    )
                    st.session_state[widget_key] = previous_status

            with row_columns[3]:
                st.selectbox(
                    "Status",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(original_status),
                    key=status_widget_key,
                    label_visibility="collapsed",
                    on_change=save_inline_status,
                )

            with row_columns[4]:
                render_html(
                    f'<div class="premium-inline-cell muted">{html.escape(normalize_text(row["Vendedor"]) or "Sem vendedor")}</div>'
                )

            with row_columns[5]:
                render_html(
                    f'<div class="premium-inline-cell date">{html.escape(normalize_text(row["Data"]))}</div>'
                )

def prepare_filters(df: pd.DataFrame) -> pd.DataFrame:
    title_column, refresh_column = st.columns([3.8, 1.0], gap="large")

    with title_column:
        render_html(
            """
            <div class="page-title">Visão Geral</div>
            <div class="page-subtitle">Acompanhe o desempenho da operação comercial em tempo real.</div>
            """
        )

    with refresh_column:
        st.write("")
        if st.button("🔄 Atualizar dados", use_container_width=True):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()

    valid_dates = df["_data_chamado"].dropna()

    if valid_dates.empty:
        date_max = date.today()
        date_min = date_max - timedelta(days=30)
    else:
        date_min = valid_dates.min().date()
        date_max = valid_dates.max().date()

    seller_options = sorted(
        [
            seller
            for seller in df["_vendedor"].dropna().astype(str).unique().tolist()
            if normalize_text(seller)
        ]
    )

    if "dashboard_filter_seller" not in st.session_state:
        st.session_state.dashboard_filter_seller = "Todos os vendedores"

    if st.session_state.dashboard_filter_seller not in ["Todos os vendedores"] + seller_options:
        st.session_state.dashboard_filter_seller = "Todos os vendedores"

    if "dashboard_filter_status" not in st.session_state:
        st.session_state.dashboard_filter_status = "Todos os status"

    if st.session_state.dashboard_filter_status not in ["Todos os status"] + STATUS_OPTIONS:
        st.session_state.dashboard_filter_status = "Todos os status"

    if "dashboard_filter_period" not in st.session_state:
        st.session_state.dashboard_filter_period = (date_min, date_max)

    if "dashboard_filter_search" not in st.session_state:
        st.session_state.dashboard_filter_search = ""

    selected_seller = st.session_state.dashboard_filter_seller
    selected_status = st.session_state.dashboard_filter_status
    selected_range = st.session_state.dashboard_filter_period
    search_term = st.session_state.dashboard_filter_search

    # A busca por empresa ou telefone é global: quando o usuário digita algo
    # nesse campo, pesquisamos a planilha inteira e ignoramos os filtros de
    # vendedor, status e período. Dessa forma, nenhum registro fica oculto por
    # um filtro aplicado anteriormente.
    if normalize_text(search_term):
        term = normalize_search_text(search_term)
        global_search_df = df.copy()

        global_search_df = global_search_df[
            global_search_df.apply(
                lambda row: term
                in normalize_search_text(
                    " | ".join(
                        [
                            normalize_text(row.get("_empresa", "")),
                            normalize_text(row.get("_telefone", "")),
                        ]
                    )
                ),
                axis=1,
            )
        ].copy()

        return global_search_df

    filtered_df = df.copy()

    if selected_seller != "Todos os vendedores":
        filtered_df = filtered_df[filtered_df["_vendedor"] == selected_seller].copy()

    if selected_status != "Todos os status":
        filtered_df = filtered_df[filtered_df["_status_grupo"] == selected_status].copy()

    if isinstance(selected_range, (tuple, list)) and len(selected_range) == 2:
        start_date, end_date = selected_range

        filtered_df = filtered_df[
            filtered_df["_data_chamado"].isna()
            | (
                (filtered_df["_data_chamado"].dt.date >= start_date)
                & (filtered_df["_data_chamado"].dt.date <= end_date)
            )
        ].copy()

    return filtered_df


# =========================================================
# PÁGINA: VISÃO GERAL
# =========================================================
def render_overview_page(df: pd.DataFrame, columns: dict) -> None:
    registration_success = st.session_state.pop("company_registration_success", None)

    if registration_success:
        st.success(registration_success)

    filtered_df = prepare_filters(df)

    today = date.today()
    start_week = today - timedelta(days=today.weekday())
    start_month = today.replace(day=1)

    called_today = int((filtered_df["_data_chamado"].dt.date == today).sum())
    called_week = int((filtered_df["_data_chamado"].dt.date >= start_week).sum())
    called_month = int((filtered_df["_data_chamado"].dt.date >= start_month).sum())
    companies = int(filtered_df["_empresa"].replace("", pd.NA).dropna().nunique())

    card_1, card_2, card_3, card_4 = st.columns(4, gap="medium")

    with card_1:
        render_metric_card("Chamados hoje", str(called_today), "Base atual", "☎", "linear-gradient(135deg,#FF4BAA,#C223FF)")

    with card_2:
        render_metric_card("Chamados na semana", str(called_week), "Base atual", "🗓", "linear-gradient(135deg,#AE4BFF,#6E23FF)")

    with card_3:
        render_metric_card("Chamados no mês", str(called_month), "Base atual", "📊", "linear-gradient(135deg,#FF4BAA,#8F2BFF)")

    with card_4:
        render_metric_card("Empresas cadastradas no mês", str(companies), "Base atual filtrada", "🏢", "linear-gradient(135deg,#8F2BFF,#C94AFF)")

    st.write("")

    chart_column, status_column = st.columns([2.1, 1.0], gap="large")

    with chart_column:
        render_html(
            """
            <div class="section-heading">Chamados por semana</div>
            <div class="section-subtitle">Volume de chamados agrupado por semana conforme o período selecionado.</div>
            """
        )

        chart_df = filtered_df.dropna(subset=["_data_chamado"]).copy()

        if chart_df.empty:
            current_week_start = (
                pd.Timestamp.today().normalize()
                - pd.to_timedelta(pd.Timestamp.today().weekday(), unit="D")
            )
            week_starts = pd.date_range(
                end=current_week_start,
                periods=4,
                freq="7D",
            )
            chart_df = pd.DataFrame({"InicioSemana": week_starts})
            chart_df["Quantidade"] = 0
        else:
            chart_df["InicioSemana"] = (
                chart_df["_data_chamado"].dt.normalize()
                - pd.to_timedelta(chart_df["_data_chamado"].dt.weekday, unit="D")
            )
            chart_df = (
                chart_df.groupby("InicioSemana")
                .size()
                .reset_index(name="Quantidade")
                .sort_values("InicioSemana")
            )

        chart_df["FimSemana"] = chart_df["InicioSemana"] + pd.Timedelta(days=6)
        chart_df["Semana"] = (
            chart_df["InicioSemana"].dt.strftime("%d/%m")
            + " – "
            + chart_df["FimSemana"].dt.strftime("%d/%m")
        )

        # Mantém o preenchimento roxo mesmo quando existe apenas uma semana.
        # O ponto auxiliar serve apenas para desenhar a área visualmente e não
        # altera a contagem dos chamados.
        plot_df = chart_df.copy()

        if len(plot_df) == 1:
            support_point = plot_df.iloc[0].copy()
            support_point["InicioSemana"] = support_point["FimSemana"]
            plot_df = pd.concat(
                [plot_df, pd.DataFrame([support_point])],
                ignore_index=True,
            )

        figure = px.area(
            plot_df,
            x="InicioSemana",
            y="Quantidade",
            markers=True,
            custom_data=["Semana"],
        )

        figure.update_traces(
            line=dict(
                color="#E14BFF",
                width=4,
                shape="spline",
            ),
            marker=dict(
                size=9,
                color="#FFFFFF",
                line=dict(width=3, color="#D74BFF"),
            ),
            fill="tozeroy",
            fillcolor="rgba(224,67,255,0.34)",
            hovertemplate="Semana: %{customdata[0]}<br>Chamados: %{y}<extra></extra>",
        )

        figure.update_layout(
            height=370,
            margin=dict(l=20, r=20, t=8, b=8),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FFFFFF"),
            xaxis_title="",
            yaxis_title="",
        )

        figure.update_xaxes(
            showgrid=False,
            tickmode="array",
            tickvals=chart_df["InicioSemana"].tolist(),
            ticktext=chart_df["Semana"].tolist(),
        )
        figure.update_yaxes(gridcolor="rgba(255,255,255,0.08)")

        st.plotly_chart(figure, use_container_width=True)

    with status_column:
        render_status_summary(filtered_df)

    st.write("")
    render_latest_calls_section(filtered_df, columns, df)


# =========================================================
# PÁGINA: CADASTRO
# =========================================================
def render_proposals_page(df: pd.DataFrame, columns: dict) -> None:
    apply_registration_css()

    render_html(
        """
        <div class="registration-header-card">
            <div class="registration-kicker">OPPI COMERCIAL • NOVO CONTRATO</div>
            <div class="registration-title">Novo contrato</div>
            <div class="registration-subtitle">
                Registre uma nova empresa e envie os dados diretamente para a planilha comercial.
            </div>
        </div>
        """
    )

    seller_options = sorted(
        {
            normalize_text(value)
            for value in df["_vendedor"].tolist()
            if normalize_text(value) and normalize_text(value) != "Sem vendedor"
        }
    )

    if not seller_options:
        seller_options = ["Sem vendedor"]

    with st.form("company_registration_form", clear_on_submit=True):
        render_html(
            """
            <div class="registration-note">
                Preencha os dados abaixo. Os campos com <strong>*</strong> são obrigatórios.
                Ao finalizar, a empresa será adicionada automaticamente à aba Folha1.
            </div>
            <div class="registration-section">
                <div class="registration-section-title">DADOS DA EMPRESA</div>
                <div class="registration-section-text">Informações principais da empresa e dados institucionais.</div>
            </div>
            """
        )

        company_col, opening_col = st.columns([1.65, 0.75], gap="medium")

        with company_col:
            empresa = st.text_input(
                "Nome da empresa *",
                placeholder="Digite o nome da empresa",
            )

        with opening_col:
            data_abertura = st.text_input(
                "Data de abertura",
                placeholder="DD/MM/AAAA",
            )

        cnpj_col, capital_col = st.columns(2, gap="medium")

        with cnpj_col:
            cnpj = st.text_input(
                "CNPJ *",
                placeholder="00.000.000/0000-00",
            )

        with capital_col:
            capital = st.text_input(
                "Capital social",
                placeholder="R$ 0,00",
            )

        endereco = st.text_input(
            "Endereço",
            placeholder="Rua, avenida, número, bairro, cidade, estado e CEP",
        )

        email_col, site_col = st.columns(2, gap="medium")

        with email_col:
            email_empresa = st.text_input(
                "E-mail da empresa",
                placeholder="contato@empresa.com.br",
            )

        with site_col:
            site = st.text_input(
                "Site da empresa",
                placeholder="www.empresa.com.br",
            )

        render_html(
            """
            <div class="registration-section">
                <div class="registration-section-title">TELEFONES DA EMPRESA</div>
                <div class="registration-section-text">Contatos principais utilizados no acompanhamento comercial.</div>
            </div>
            """
        )

        phone_1, phone_2, phone_3 = st.columns(3, gap="medium")

        with phone_1:
            telefone_b2b = st.text_input(
                "Telefone B2B *",
                placeholder="(00) 00000-0000",
            )

        with phone_2:
            telefone_fixo = st.text_input(
                "Telefone fixo *",
                placeholder="(00) 0000-0000",
            )

        with phone_3:
            telefone_alternativo = st.text_input(
                "Telefone alternativo *",
                placeholder="(00) 00000-0000",
            )

        render_html(
            """
            <div class="registration-section">
                <div class="registration-section-title">SÓCIOS E RESPONSÁVEIS</div>
                <div class="registration-section-text">Cadastre os principais responsáveis vinculados à empresa.</div>
            </div>
            """
        )

        socio_1_col, cpf_1_col = st.columns([1.55, 0.85], gap="medium")

        with socio_1_col:
            socio_1 = st.text_input(
                "Sócio 1",
                placeholder="Nome completo do primeiro sócio",
            )

        with cpf_1_col:
            cpf_socio_1 = st.text_input(
                "CPF do sócio 1",
                placeholder="000.000.000-00",
            )

        email_socio_col, telefone_socio_col = st.columns(2, gap="medium")

        with email_socio_col:
            email_socio_1 = st.text_input(
                "E-mail do sócio 1",
                placeholder="socio@empresa.com.br",
            )

        with telefone_socio_col:
            telefone_socio_1 = st.text_input(
                "Telefone do sócio 1",
                placeholder="(00) 00000-0000",
            )

        socio_2_col, cpf_2_col = st.columns([1.55, 0.85], gap="medium")

        with socio_2_col:
            socio_2 = st.text_input(
                "Sócio 2",
                placeholder="Nome completo do segundo sócio",
            )

        with cpf_2_col:
            cpf_socio_2 = st.text_input(
                "CPF do sócio 2",
                placeholder="000.000.000-00",
            )

        socio_3_col, cpf_3_col = st.columns([1.55, 0.85], gap="medium")

        with socio_3_col:
            socio_3 = st.text_input(
                "Sócio 3",
                placeholder="Nome completo do terceiro sócio",
            )

        with cpf_3_col:
            cpf_socio_3 = st.text_input(
                "CPF do sócio 3",
                placeholder="000.000.000-00",
            )

        render_html(
            """
            <div class="registration-section">
                <div class="registration-section-title">REDES SOCIAIS E ACOMPANHAMENTO</div>
                <div class="registration-section-text">Complete os dados comerciais e defina o status inicial do atendimento.</div>
            </div>
            """
        )

        social_1, social_2 = st.columns(2, gap="medium")

        with social_1:
            instagram = st.text_input(
                "Instagram",
                placeholder="@empresa",
            )

        with social_2:
            linkedin = st.text_input(
                "LinkedIn",
                placeholder="Link ou usuário do perfil",
            )

        vendedor_col, status_col, called_at_col = st.columns([1.15, 1.15, 0.85], gap="medium")

        with vendedor_col:
            vendedor = st.selectbox(
                "Vendedor *",
                seller_options,
            )

        with status_col:
            status = st.selectbox(
                "Status comercial *",
                STATUS_OPTIONS,
                index=0,
            )

        with called_at_col:
            data_chamado = st.date_input(
                "Data do chamado *",
                value=date.today(),
                format="DD/MM/YYYY",
            )

        observacoes = st.text_area(
            "Observações",
            placeholder="Digite informações adicionais importantes sobre a empresa ou o atendimento comercial.",
            height=120,
        )

        submitted = st.form_submit_button(
            "Cadastrar empresa",
            use_container_width=True,
        )

    if submitted:
        if not normalize_text(empresa):
            st.error("Preencha o nome da empresa para concluir o cadastro.")
            return

        if not normalize_text(cnpj):
            st.error("Preencha o CNPJ para concluir o cadastro.")
            return

        if not normalize_cnpj_for_duplicate(cnpj):
            st.error("Digite um CNPJ válido com 14 números.")
            return

        if not normalize_text(telefone_b2b):
            st.error("Preencha o telefone B2B para concluir o cadastro.")
            return

        if not normalize_text(telefone_fixo):
            st.error("Preencha o telefone fixo para concluir o cadastro.")
            return

        if not normalize_text(telefone_alternativo):
            st.error("Preencha o telefone alternativo para concluir o cadastro.")
            return

        for phone_label, phone_value in [
            ("Telefone B2B", telefone_b2b),
            ("Telefone fixo", telefone_fixo),
            ("Telefone alternativo", telefone_alternativo),
        ]:
            if not normalize_phone_for_duplicate(phone_value):
                st.error(f"Digite um número válido no campo {phone_label}.")
                return

        now_text = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d/%m/%Y %H:%M")

        try:
            append_company_to_sheet(
                {
                    "empresa": empresa,
                    "data_abertura": data_abertura,
                    "capital": capital,
                    "cnpj": cnpj,
                    "endereco": endereco,
                    "email_empresa": email_empresa,
                    "site": site,
                    "telefone_b2b": telefone_b2b,
                    "telefone_fixo": telefone_fixo,
                    "telefone_alternativo": telefone_alternativo,
                    "socio_1": socio_1,
                    "cpf_socio_1": cpf_socio_1,
                    "email_socio_1": email_socio_1,
                    "telefone_socio_1": telefone_socio_1,
                    "socio_2": socio_2,
                    "cpf_socio_2": cpf_socio_2,
                    "socio_3": socio_3,
                    "cpf_socio_3": cpf_socio_3,
                    "instagram": instagram,
                    "linkedin": linkedin,
                    "vendedor": vendedor,
                    "status": status,
                    "data_chamado": data_chamado.strftime("%d/%m/%Y"),
                    "ultima_atualizacao": now_text,
                    "observacoes": observacoes,
                }
            )
        except DuplicateRegistrationError as error:
            st.error(str(error))
            return
        except Exception as error:
            st.error("Não consegui cadastrar a empresa na planilha.")
            st.code(str(error))
            return

        # Após o cadastro, abre a Visão Geral já com os dados atualizados.
        # Também limpa filtros antigos que poderiam esconder a nova empresa.
        st.session_state.selected_page = "Visão Geral"
        st.session_state.dashboard_filter_seller = "Todos os vendedores"
        st.session_state.dashboard_filter_status = "Todos os status"
        st.session_state.dashboard_filter_search = ""
        st.session_state.pop("dashboard_filter_period", None)
        st.session_state["ultimos_chamados_status_selecionado"] = status
        st.session_state["company_registration_success"] = (
            f"Empresa “{normalize_text(empresa)}” cadastrada com sucesso na planilha e adicionada à Visão Geral com o status “{normalize_text(status)}”."
        )
        st.rerun()


# =========================================================
# PÁGINA: TODOS OS CONTRATOS
# =========================================================
def _contract_detail_value(row: pd.Series, columns: dict, key: str) -> str:
    column_name = columns.get(key)

    if column_name and column_name in row.index:
        value = normalize_text(row.get(column_name, ""))

        if value:
            return value

    return "Não informado"


def _contract_detail_field(label: str, value: str, full_width: bool = False, long_text: bool = False) -> str:
    field_classes = ["contract-detail-field"]
    value_classes = ["contract-detail-value"]

    if full_width:
        field_classes.append("full-width")

    if long_text:
        value_classes.append("long-text")

    return (
        f'<div class="{" ".join(field_classes)}">'
        f'<div class="contract-detail-label">{html.escape(label)}</div>'
        f'<div class="{" ".join(value_classes)}">{html.escape(normalize_text(value) or "Não informado")}</div>'
        f'</div>'
    )


def render_contract_detail_page(df: pd.DataFrame, columns: dict, sheet_row: int) -> None:
    selected_rows = df[df["_sheet_row"].astype(int) == int(sheet_row)].copy()

    if selected_rows.empty:
        st.session_state.selected_contract_sheet_row = None
        st.warning("Não encontrei os dados dessa empresa na planilha.")
        return

    row = selected_rows.iloc[0]
    company_name = normalize_text(row.get("_empresa", "")) or "Empresa cadastrada"

    with st.container(key="contract_detail_back"):
        if st.button("← Voltar para empresas cadastradas", key="back_to_contracts_names"):
            st.session_state.selected_contract_sheet_row = None
            st.rerun()

    render_html(
        f"""
        <div class="registration-header-card">
            <div class="registration-kicker">OPPI COMERCIAL • EMPRESA CADASTRADA</div>
            <div class="registration-title">{html.escape(company_name)}</div>
            <div class="registration-subtitle">
                Visualize os dados cadastrados diretamente na planilha comercial.
            </div>
        </div>
        """
    )

    company_fields = "".join(
        [
            _contract_detail_field("Nome da empresa", _contract_detail_value(row, columns, "empresa")),
            _contract_detail_field("Data de abertura", _contract_detail_value(row, columns, "data_abertura")),
            _contract_detail_field("CNPJ", _contract_detail_value(row, columns, "cnpj")),
            _contract_detail_field("Capital social", _contract_detail_value(row, columns, "capital")),
            _contract_detail_field("Endereço", _contract_detail_value(row, columns, "endereco"), full_width=True),
            _contract_detail_field("E-mail da empresa", _contract_detail_value(row, columns, "email")),
            _contract_detail_field("Site da empresa", _contract_detail_value(row, columns, "site")),
        ]
    )

    phone_fields = "".join(
        [
            _contract_detail_field("Telefone B2B", _contract_detail_value(row, columns, "telefone_b2b")),
            _contract_detail_field("Telefone fixo", _contract_detail_value(row, columns, "telefone_fixo")),
            _contract_detail_field("Telefone alternativo", _contract_detail_value(row, columns, "telefone_alternativo")),
        ]
    )

    partner_fields = "".join(
        [
            _contract_detail_field("Sócio 1", _contract_detail_value(row, columns, "socio_1")),
            _contract_detail_field("CPF do sócio 1", _contract_detail_value(row, columns, "cpf_socio_1")),
            _contract_detail_field("E-mail do sócio 1", _contract_detail_value(row, columns, "email_socio_1")),
            _contract_detail_field("Telefone do sócio 1", _contract_detail_value(row, columns, "telefone_socio_1")),
            _contract_detail_field("Sócio 2", _contract_detail_value(row, columns, "socio_2")),
            _contract_detail_field("CPF do sócio 2", _contract_detail_value(row, columns, "cpf_socio_2")),
            _contract_detail_field("Sócio 3", _contract_detail_value(row, columns, "socio_3")),
            _contract_detail_field("CPF do sócio 3", _contract_detail_value(row, columns, "cpf_socio_3")),
        ]
    )

    tracking_fields = "".join(
        [
            _contract_detail_field("Instagram", _contract_detail_value(row, columns, "instagram")),
            _contract_detail_field("LinkedIn", _contract_detail_value(row, columns, "linkedin")),
            _contract_detail_field("Vendedor", normalize_text(row.get("_vendedor", "")) or "Não informado"),
            _contract_detail_field("Status comercial", normalize_text(row.get("_status_grupo", "")) or "Não informado"),
            _contract_detail_field("Data do chamado", _contract_detail_value(row, columns, "data_chamado")),
            _contract_detail_field("Última atualização", _contract_detail_value(row, columns, "ultima_atualizacao")),
            _contract_detail_field("Observações", _contract_detail_value(row, columns, "observacoes"), full_width=True, long_text=True),
        ]
    )

    render_html(
        f"""
        <div class="contract-detail-shell">
            <div class="registration-section">
                <div class="registration-section-title">DADOS DA EMPRESA</div>
                <div class="registration-section-text">Informações institucionais cadastradas na planilha.</div>
            </div>
            <div class="contract-detail-grid">{company_fields}</div>

            <div class="registration-section">
                <div class="registration-section-title">TELEFONES DA EMPRESA</div>
                <div class="registration-section-text">Contatos utilizados no acompanhamento comercial.</div>
            </div>
            <div class="contract-detail-grid three-columns">{phone_fields}</div>

            <div class="registration-section">
                <div class="registration-section-title">SÓCIOS E RESPONSÁVEIS</div>
                <div class="registration-section-text">Responsáveis vinculados à empresa.</div>
            </div>
            <div class="contract-detail-grid">{partner_fields}</div>

            <div class="registration-section">
                <div class="registration-section-title">REDES SOCIAIS E ACOMPANHAMENTO</div>
                <div class="registration-section-text">Informações comerciais e status atual do atendimento.</div>
            </div>
            <div class="contract-detail-grid">{tracking_fields}</div>
        </div>
        """
    )


def render_all_contracts_page(df: pd.DataFrame, columns: dict) -> None:
    apply_registration_css()

    selected_sheet_row = st.session_state.get("selected_contract_sheet_row")

    if selected_sheet_row:
        render_contract_detail_page(df, columns, int(selected_sheet_row))
        return

    render_html(
        """
        <div class="registration-header-card">
            <div class="registration-kicker">OPPI COMERCIAL • CADASTROS</div>
            <div class="registration-title">Empresas cadastradas</div>
            <div class="registration-subtitle">
                Consulte os nomes de todas as empresas cadastradas na planilha comercial.
            </div>
        </div>
        """
    )

    valid_dates = df["_data_chamado"].dropna()

    if valid_dates.empty:
        date_max = date.today()
        date_min = date_max - timedelta(days=30)
    else:
        date_min = valid_dates.min().date()
        date_max = valid_dates.max().date()

    seller_options = sorted(
        [
            seller
            for seller in df["_vendedor"].dropna().astype(str).unique().tolist()
            if normalize_text(seller)
        ]
    )

    if "contracts_names_filter_seller" not in st.session_state:
        st.session_state.contracts_names_filter_seller = "Todos os vendedores"

    if "contracts_names_filter_status" not in st.session_state:
        st.session_state.contracts_names_filter_status = "Todos os status"

    if "contracts_names_filter_period" not in st.session_state:
        st.session_state.contracts_names_filter_period = (date_min, date_max)

    if "contracts_names_filter_search" not in st.session_state:
        st.session_state.contracts_names_filter_search = ""

    filter_col_1, filter_col_2, filter_col_3, filter_col_4 = st.columns(4, gap="medium")

    with filter_col_1:
        selected_seller = st.selectbox(
            "Vendedor",
            ["Todos os vendedores"] + seller_options,
            key="contracts_names_filter_seller",
        )

    with filter_col_2:
        selected_status = st.selectbox(
            "Status",
            ["Todos os status"] + STATUS_OPTIONS,
            key="contracts_names_filter_status",
        )

    with filter_col_3:
        selected_period = st.date_input(
            "Período",
            min_value=date_min,
            max_value=max(date_max, date.today()),
            key="contracts_names_filter_period",
        )

    with filter_col_4:
        search_term = st.text_input(
            "Buscar empresa",
            placeholder="Digite o nome da empresa...",
            key="contracts_names_filter_search",
        )

    filtered_df = df.copy()

    if selected_seller != "Todos os vendedores":
        filtered_df = filtered_df[filtered_df["_vendedor"] == selected_seller].copy()

    if selected_status != "Todos os status":
        filtered_df = filtered_df[filtered_df["_status_grupo"] == selected_status].copy()

    if isinstance(selected_period, (tuple, list)) and len(selected_period) == 2:
        start_date, end_date = selected_period
        filtered_df = filtered_df[
            filtered_df["_data_chamado"].isna()
            | (
                (filtered_df["_data_chamado"].dt.date >= start_date)
                & (filtered_df["_data_chamado"].dt.date <= end_date)
            )
        ].copy()

    if normalize_text(search_term):
        term = normalize_search_text(search_term)
        filtered_df = filtered_df[
            filtered_df["_empresa"].apply(
                lambda value: term in normalize_search_text(value)
            )
        ].copy()

    names_df = filtered_df[["_empresa", "_sheet_row"]].copy()
    names_df["Empresa"] = names_df["_empresa"].apply(normalize_text)
    names_df = names_df[names_df["Empresa"] != ""].copy()
    names_df = names_df.sort_values(
        "Empresa",
        key=lambda series: series.map(normalize_search_text),
    )

    render_html(
        f"""
        <div class="contracts-names-count-card">
            Exibindo <strong>{len(names_df)}</strong> empresa(s) cadastrada(s) na planilha.
        </div>
        """
    )

    if names_df.empty:
        st.info("Nenhuma empresa cadastrada foi encontrada com os filtros informados.")
        return

    with st.container(key="contracts_names_list"):
        render_html('<div class="contracts-names-clickable-header">Empresas cadastradas</div>')

        for _, company_row in names_df.iterrows():
            sheet_row = int(company_row["_sheet_row"])
            company_name = normalize_text(company_row["Empresa"])

            if st.button(
                company_name,
                key=f"open_contract_detail_{sheet_row}",
                use_container_width=True,
            ):
                st.session_state.selected_contract_sheet_row = sheet_row
                st.rerun()


# =========================================================
# PÁGINA: PESOS E MEDIDAS
# =========================================================
def render_scoring_page(df: pd.DataFrame, columns: dict) -> None:
    render_html(
        """
        <div class="page-title">Pesos e Medidas</div>
        <div class="page-subtitle">Pontuação dos leads com base na qualidade dos dados e avanço no comercial.</div>
        """
    )

    hot = int((df["_classificacao"] == "Lead Quente").sum())
    warm = int((df["_classificacao"] == "Lead Morno").sum())
    cold = int((df["_classificacao"] == "Lead Frio").sum())
    average = int(round(df["_pontuacao"].mean())) if not df.empty else 0

    card_1, card_2, card_3, card_4 = st.columns(4, gap="medium")

    with card_1:
        render_metric_card("Leads Quentes", str(hot), "Pontuação acima de 70", "🔥", "linear-gradient(135deg,#FF4BAA,#D83BFF)")

    with card_2:
        render_metric_card("Leads Mornos", str(warm), "Pontuação entre 40 e 69", "🌤", "linear-gradient(135deg,#FF9C2D,#FFCC45)")

    with card_3:
        render_metric_card("Leads Frios", str(cold), "Pontuação abaixo de 40", "❄", "linear-gradient(135deg,#5F8BFF,#66C2FF)")

    with card_4:
        render_metric_card("Pontuação Média", str(average), "Média da base", "⚖", "linear-gradient(135deg,#7A39FF,#D64AFF)")

    st.write("")

    ranking_df = df.sort_values(by="_pontuacao", ascending=False).copy()

    display_df = pd.DataFrame(
        {
            "Empresa": ranking_df["_empresa"],
            "CNPJ": safe_series(ranking_df, columns.get("cnpj")),
            "Capital": ranking_df["_capital_num"].apply(format_money),
            "Telefone": ranking_df["_telefone"],
            "Email": safe_series(ranking_df, columns.get("email")),
            "Instagram": safe_series(ranking_df, columns.get("instagram")),
            "Vendedor": ranking_df["_vendedor"],
            "Status": ranking_df["_status_grupo"],
            "Pontuação": ranking_df["_pontuacao"],
            "Classificação": ranking_df["_classificacao"],
        }
    )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=560,
    )


# =========================================================
# TRATAMENTO DE ERROS
# =========================================================
def render_connection_error(error: Exception) -> None:
    apply_dashboard_css()

    render_html(
        """
        <div class="page-title">Dashboard Oppi Comercial</div>
        <div class="page-subtitle">Erro ao conectar com a planilha.</div>
        """
    )

    if isinstance(error, SpreadsheetNotFound):
        st.error(
            "A credencial foi aceita, mas a planilha não foi localizada. "
            "Confirme se o SHEET_ID está correto e se a planilha foi compartilhada "
            "diretamente com o e-mail da conta de serviço."
        )
        st.code(SHEET_ID)
        return

    if isinstance(error, WorksheetNotFound):
        st.error(
            f"A planilha foi localizada, mas não encontrei a aba '{WORKSHEET_NAME}'."
        )
        return

    st.error("Não consegui carregar os dados da planilha.")
    st.code(str(error))


# =========================================================
# APLICAÇÃO PRINCIPAL
# =========================================================
def main() -> None:
    restore_navigation_session_from_url()

    if not st.session_state.authenticated:
        render_login_page()
        return

    apply_dashboard_css()
    page = render_sidebar()

    try:
        df = load_sheet_data()
    except Exception as error:
        render_connection_error(error)
        return

    if df.empty:
        render_html(
            """
            <div class="page-title">Dashboard Oppi Comercial</div>
            <div class="page-subtitle">A conexão foi realizada, mas a planilha está vazia.</div>
            """
        )
        st.warning("A planilha foi encontrada, mas não possui registros preenchidos.")
        return

    columns = identify_columns(df)
    prepared_df = prepare_data(df, columns)

    if page == "Visão Geral":
        render_overview_page(prepared_df, columns)
    elif page == "Cadastro":
        cadastro_subpage = st.session_state.get("selected_cadastro_subpage", "Novo contrato")

        if cadastro_subpage == "Todos os contratos":
            render_all_contracts_page(prepared_df, columns)
        else:
            render_proposals_page(prepared_df, columns)
    elif page == "Pesos e Medidas":
        render_scoring_page(prepared_df, columns)


if __name__ == "__main__":
    main()
