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

    if any(word in status for word in ["fechado", "ganho", "cliente"]):
        return "Fechado"

    if any(word in status for word in ["nao responde", "sem resposta"]):
        return "Não Responde"

    if any(word in status for word in ["sem interesse", "nao tem interesse"]):
        return "Sem Interesse"

    if "proposta" in status:
        return "Proposta"

    if any(word in status for word in ["chamando", "contato", "negoci", "andamento"]):
        return "Chamando"

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
    elif grouped_status == "Chamando":
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

    for column in df.columns:
        df[column] = df[column].astype(str).str.strip()

    df = df[
        df.apply(
            lambda row: any(normalize_text(value) for value in row),
            axis=1,
        )
    ].copy()

    return df.reset_index(drop=True)


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
                background: #FFFFFF !important;
                border: none !important;
                border-radius: 30px !important;
                padding: 28px 32px 24px 32px !important;
                box-shadow: 0 34px 88px rgba(0,0,0,0.30) !important;
                max-width: 640px !important;
                margin: 0 auto !important;
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
                background: linear-gradient(90deg, #FF4E97 0%, #A91CFF 100%) !important;
                box-shadow: 0 14px 30px rgba(204, 42, 255, 0.25) !important;
                margin-top: 0.45rem !important;
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
                background: linear-gradient(180deg, rgba(0,0,0,0.90), rgba(4,4,15,0.97));
                border-right: 1px solid rgba(255,255,255,0.05);
            }

            section[data-testid="stSidebar"] * {
                color: #FFFFFF;
            }

            .side-logo {
                width: 78px;
                height: 78px;
                border-radius: 50%;
                background: linear-gradient(145deg, #FF4BAA 10%, #9C19FF 88%);
                position: relative;
                margin: 14px 0 26px 4px;
            }

            .side-logo::before {
                content: "";
                position: absolute;
                width: 27px;
                height: 27px;
                border-radius: 50%;
                background: #05050D;
                top: 20px;
                left: 26px;
            }

            .side-logo::after {
                content: "";
                position: absolute;
                width: 28px;
                height: 28px;
                left: 2px;
                bottom: 0;
                clip-path: polygon(0 100%, 25% 26%, 100% 0);
                background: linear-gradient(145deg, #FF4BAA 10%, #9C19FF 88%);
            }

            .side-title {
                color: #FFFFFF;
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
                color: rgba(255,255,255,0.72);
                margin-top: 8px;
                font-size: 0.90rem;
            }

            .side-line {
                width: 70px;
                height: 4px;
                border-radius: 999px;
                margin: 24px 0 22px 0;
                background: linear-gradient(90deg, #FF4BAA, #AE26FF);
            }

            .side-tip {
                display: flex;
                gap: 12px;
                align-items: center;
                margin: 18px 0 22px 0;
                padding: 14px;
                border-radius: 16px;
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
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
                color: rgba(255,255,255,0.76);
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
                min-height: 132px;
                padding: 17px;
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.06);
                background: linear-gradient(145deg, rgba(22,20,42,0.98), rgba(10,9,25,0.98));
                box-shadow: 0 18px 46px rgba(0,0,0,0.22);
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
                color: rgba(255,255,255,0.78);
                font-size: 0.94rem;
                font-weight: 750;
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

            div[data-baseweb="select"] > div,
            div[data-baseweb="input"] > div {
                min-height: 54px;
                border-radius: 15px !important;
                border: 1px solid rgba(255,255,255,0.08) !important;
                background: rgba(255,255,255,0.03) !important;
                color: #FFFFFF !important;
            }

            label {
                color: rgba(255,255,255,0.80) !important;
                font-weight: 650 !important;
            }

            .stTextInput input {
                color: #FFFFFF !important;
            }

            .stButton > button {
                min-height: 48px !important;
                border: none !important;
                border-radius: 15px !important;
                color: #FFFFFF !important;
                font-weight: 800 !important;
                background: linear-gradient(90deg, #FF4BAA 0%, #A91CFF 100%) !important;
            }

            div[data-testid="stDataFrame"] {
                overflow: hidden;
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.08);
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
        render_html(
            """
            <div class="side-logo"></div>
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
        ("Chamando", "#C67A25"),
        ("Sem Interesse", "#45B6C6"),
        ("Não Responde", "#DF5578"),
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


def prepare_filters(df: pd.DataFrame):
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

    filter_1, filter_2, filter_3, filter_4 = st.columns(
        [1.2, 1.2, 1.1, 1.2],
        gap="medium",
    )

    seller_options = sorted(
        [
            seller
            for seller in df["_vendedor"].dropna().astype(str).unique().tolist()
            if normalize_text(seller)
        ]
    )

    status_options = [
        "Novo Lead",
        "Chamando",
        "Sem Interesse",
        "Não Responde",
        "Fechado",
        "Proposta",
    ]

    with filter_1:
        selected_seller = st.selectbox(
            "Vendedor",
            ["Todos os vendedores"] + seller_options,
        )

    with filter_2:
        selected_status = st.selectbox(
            "Status",
            ["Todos os status"] + status_options,
        )

    with filter_3:
        selected_range = st.date_input(
            "Período",
            value=(date_min, date_max),
            min_value=date_min,
            max_value=max(date_max, date.today()),
        )

    with filter_4:
        search_term = st.text_input(
            "Buscar empresa ou telefone",
            placeholder="Digite para buscar...",
        )

    filtered_df = df.copy()

    if selected_seller != "Todos os vendedores":
        filtered_df = filtered_df[filtered_df["_vendedor"] == selected_seller].copy()

    if selected_status != "Todos os status":
        filtered_df = filtered_df[filtered_df["_status_grupo"] == selected_status].copy()

    if isinstance(selected_range, tuple) and len(selected_range) == 2:
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
        render_metric_card("Empresas contatadas", str(companies), "Base atual filtrada", "🏢", "linear-gradient(135deg,#8F2BFF,#C94AFF)")

    st.write("")

    chart_column, status_column = st.columns([2.1, 1.0], gap="large")

    with chart_column:
        render_html(
            """
            <div class="section-heading">Chamados por dia</div>
            <div class="section-subtitle">Volume de chamados conforme o período selecionado.</div>
            """
        )

        chart_df = filtered_df.dropna(subset=["_data_chamado"]).copy()

        if chart_df.empty:
            chart_df = pd.DataFrame(
                {
                    "Dia": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
                    "Quantidade": [0, 0, 0, 0, 0, 0, 0],
                }
            )
        else:
            chart_df["DiaReal"] = chart_df["_data_chamado"].dt.date
            chart_df = chart_df.groupby("DiaReal").size().reset_index(name="Quantidade")
            chart_df = chart_df.sort_values("DiaReal")
            chart_df["Dia"] = pd.to_datetime(chart_df["DiaReal"]).dt.strftime("%d/%m")

        figure = px.area(
            chart_df,
            x="Dia",
            y="Quantidade",
            markers=True,
        )

        figure.update_traces(
            line=dict(color="#E14BFF", width=4),
            marker=dict(
                size=9,
                color="#FFFFFF",
                line=dict(width=3, color="#D74BFF"),
            ),
            fillcolor="rgba(224,67,255,0.34)",
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

        figure.update_xaxes(showgrid=False)
        figure.update_yaxes(gridcolor="rgba(255,255,255,0.08)")

        st.plotly_chart(figure, use_container_width=True)

    with status_column:
        render_status_summary(filtered_df)

    st.write("")

    render_html(
        """
        <div class="section-heading">Últimos chamados</div>
        <div class="section-subtitle">Empresas registradas no comercial com status e responsável.</div>
        """
    )

    table_df = filtered_df.copy()

    display_df = pd.DataFrame(
        {
            "Empresa": table_df["_empresa"],
            "Telefone": table_df["_telefone"],
            "Status": table_df["_status_grupo"],
            "Vendedor": table_df["_vendedor"],
            "Data": table_df["_data_chamado"].dt.strftime("%d/%m/%Y").fillna(""),
        }
    )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=340,
    )


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
