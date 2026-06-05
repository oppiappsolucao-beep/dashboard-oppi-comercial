import base64
import html
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
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
        "socio_2": first_existing_column(df, ["Sócio 2", "Socio 2", "Sócio2", "Socio2"]),
        "socio_3": first_existing_column(df, ["Sócio 3", "Socio 3", "Sócio3", "Socio3"]),
        "instagram": first_existing_column(df, ["Instagram"]),
        "linkedin": first_existing_column(df, ["Linkedin", "LinkedIn"]),
        "vendedor": first_existing_column(df, ["Vendedor", "Responsável", "Responsavel"]),
        "status": first_existing_column(df, ["Status", "Etapa"]),
        "data_chamado": first_existing_column(df, ["Data do chamado", "Data chamado"]),
        "ultima_atualizacao": first_existing_column(df, ["Última atualização", "Ultima atualização", "Ultima atualizacao"]),
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
def render_sidebar() -> str:
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

        page = st.radio(
            "Navegação",
            ["Visão Geral", "Propostas", "Pesos e Medidas"],
            label_visibility="collapsed",
            index=["Visão Geral", "Propostas", "Pesos e Medidas"].index(
                st.session_state.selected_page
            ),
        )

        st.session_state.selected_page = page

        render_html(
            """
            <div class="side-tip">
                <div class="side-tip-icon">🛡️</div>
                <div class="side-tip-text">Segurança, performance e inteligência para impulsionar seus resultados.</div>
            </div>
            """
        )

        if st.button("Sair", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.auth_error = ""
            st.rerun()

    return page


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

    badge_text = f"{len(display_df)} registro(s)"

    company_fields_html = "".join(
        [
            '<span class="latest-company-field">🏢 Empresa</span>',
            '<span class="latest-company-field">📞 Telefone</span>',
            '<span class="latest-company-field">✉️ E-mail</span>',
            '<span class="latest-company-field">🧾 CNPJ</span>',
            '<span class="latest-company-field">👤 Vendedor</span>',
            '<span class="latest-company-field">🗓️ Data</span>',
        ]
    )

    render_html(
        f"""
        <div class="latest-table-card">
            <div class="latest-table-card-inner">
                <div class="latest-table-head">
                    <div class="latest-table-title-wrap">
                        <div class="latest-table-icon">✦</div>
                        <div>
                            <div class="latest-table-title">Empresas em {html.escape(selected_status)}</div>
                            <div class="latest-table-subtitle">Informações comerciais detalhadas com atualização de status diretamente no dashboard.</div>
                        </div>
                    </div>
                    <div class="latest-table-badges">
                        <div class="latest-table-status-badge">{html.escape(selected_status)}</div>
                        <div class="latest-table-badge">{html.escape(badge_text)}</div>
                    </div>
                </div>
                <div class="latest-company-fields">{company_fields_html}</div>
            </div>
        </div>
        """
    )

    if display_df.empty:
        st.info("Nenhum chamado encontrado para este status no período selecionado.")
        return

    legend_html = "".join(
        f'<span class="latest-status-pill"><span class="latest-status-dot" style="background:{STATUS_COLORS[status][1]};"></span>{html.escape(status)}</span>'
        for status in STATUS_OPTIONS
    )

    render_html(
        f"""
        <div class="latest-editor-help">
            <div>
                <strong>Editar status:</strong> clique na coluna “Status” e escolha a nova etapa.
                A alteração será salva automaticamente no Google Sheets.
            </div>
            <div class="latest-sync-badge">● Sincronização automática</div>
        </div>
        <div class="latest-status-legend">{legend_html}</div>
        """
    )

    editor_df = display_df.copy()
    editor_df["_sheet_row"] = selected_df["_sheet_row"].astype(int).values

    editor_version_key = f"editor_version_{selected_status}"
    if editor_version_key not in st.session_state:
        st.session_state[editor_version_key] = 0

    editor_key = f"editor_ultimos_chamados_{selected_status}_{st.session_state[editor_version_key]}"

    edited_df = st.data_editor(
        editor_df,
        use_container_width=True,
        hide_index=True,
        height=360,
        row_height=42,
        disabled=["Empresa", "Telefone", "E-mail", "CNPJ", "Vendedor", "Data", "_sheet_row"],
        column_config={
            "Empresa": st.column_config.TextColumn("🏢 Empresa", width="large"),
            "Telefone": st.column_config.TextColumn("📞 Telefone", width="medium"),
            "E-mail": st.column_config.TextColumn("✉️ E-mail", width="large"),
            "CNPJ": st.column_config.TextColumn("🧾 CNPJ", width="medium"),
            "Status": st.column_config.SelectboxColumn(
                "✨ Status",
                help="Escolha uma das etapas comerciais. O salvamento é automático.",
                options=STATUS_OPTIONS,
                required=True,
                width="medium",
            ),
            "Vendedor": st.column_config.TextColumn("👤 Vendedor", width="medium"),
            "Data": st.column_config.TextColumn("🗓️ Data", width="small"),
            "_sheet_row": None,
        },
        key=editor_key,
    )

    original_status_by_row = {
        int(row["_sheet_row"]): normalize_text(row["Status"])
        for _, row in editor_df.iterrows()
    }

    changes = []
    for _, row in edited_df.iterrows():
        sheet_row = int(row["_sheet_row"])
        new_status = normalize_text(row["Status"])
        old_status = original_status_by_row.get(sheet_row, "")

        if new_status != old_status:
            changes.append({"sheet_row": sheet_row, "status": new_status})

    flash_message = st.session_state.pop("status_auto_save_success", None)
    if flash_message:
        st.success(flash_message)

    if changes:
        status_column_name = columns.get("status")

        if not status_column_name:
            st.error("Não encontrei a coluna Status na planilha.")
            return

        try:
            with st.spinner("Salvando alteração diretamente na planilha..."):
                update_statuses_in_sheet(
                    changes=changes,
                    status_column_name=status_column_name,
                    updated_at_column_name=columns.get("ultima_atualizacao"),
                )

            st.session_state[editor_version_key] += 1
            st.session_state["status_auto_save_success"] = (
                f"{len(changes)} status atualizado(s) automaticamente na planilha."
            )
            st.rerun()
        except Exception as error:
            st.error("Não consegui atualizar o status diretamente na planilha.")
            st.code(str(error))


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

    if normalize_text(search_term):
        term = normalize_search_text(search_term)

        filtered_df = filtered_df[
            filtered_df.apply(
                lambda row: term
                in normalize_search_text(
                    " | ".join(
                        [
                            normalize_text(row.get("_empresa", "")),
                            normalize_text(row.get("_telefone", "")),
                            normalize_text(row.get("_vendedor", "")),
                            normalize_text(row.get("_status_grupo", "")),
                        ]
                    )
                ),
                axis=1,
            )
        ].copy()

    return filtered_df


# =========================================================
# PÁGINA: VISÃO GERAL
# =========================================================
def render_overview_page(df: pd.DataFrame, columns: dict) -> None:
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
# PÁGINA: PROPOSTAS
# =========================================================
def render_proposals_page(df: pd.DataFrame, columns: dict) -> None:
    render_html(
        """
        <div class="page-title">Propostas</div>
        <div class="page-subtitle">Acompanhe as empresas com evolução comercial e propostas enviadas.</div>
        """
    )

    local_df = df.copy()

    render_html(
        """
        <div class="section-heading">Tabela de propostas</div>
        <div class="section-subtitle">Informações principais do pipeline comercial.</div>
        """
    )

    display_df = pd.DataFrame(
        {
            "Empresa": local_df["_empresa"],
            "CNPJ": safe_series(local_df, columns.get("cnpj")),
            "Telefone": local_df["_telefone"],
            "Email": safe_series(local_df, columns.get("email")),
            "Instagram": safe_series(local_df, columns.get("instagram")),
            "Vendedor": local_df["_vendedor"],
            "Status": local_df["_status_grupo"],
            "Última atualização": local_df["_ultima_atualizacao"].dt.strftime("%d/%m/%Y").fillna(""),
        }
    )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=560,
    )


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
    elif page == "Propostas":
        render_proposals_page(prepared_df, columns)
    elif page == "Pesos e Medidas":
        render_scoring_page(prepared_df, columns)


if __name__ == "__main__":
    main()
