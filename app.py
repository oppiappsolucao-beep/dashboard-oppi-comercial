import html
import re
import unicodedata
from datetime import date, datetime, timedelta
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
# ESTADO INICIAL
# =========================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "auth_error" not in st.session_state:
    st.session_state.auth_error = ""

if "selected_page" not in st.session_state:
    st.session_state.selected_page = "Visão Geral"


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================
def render_html(content: str):
    st.markdown(content, unsafe_allow_html=True)


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
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip()
    return text


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

    dt = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return pd.NaT
    return dt


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


def first_existing_column(df: pd.DataFrame, possible_names: list[str]) -> Optional[str]:
    normalized_columns = {normalize_search_text(column): column for column in df.columns}

    for name in possible_names:
        normalized_name = normalize_search_text(name)
        if normalized_name in normalized_columns:
            return normalized_columns[normalized_name]

    return None


def safe_series(df: pd.DataFrame, column: Optional[str], default_value="") -> pd.Series:
    if column and column in df.columns:
        return df[column]
    return pd.Series([default_value] * len(df), index=df.index)


def status_group(value: str) -> str:
    status = normalize_search_text(value)

    if not status:
        return "Novo Lead"

    if "fechado" in status or "ganho" in status or "cliente" in status:
        return "Fechado"

    if "nao responde" in status or "não responde" in status or "sem resposta" in status:
        return "Não Responde"

    if "sem interesse" in status or "nao tem interesse" in status or "não tem interesse" in status:
        return "Sem Interesse"

    if "proposta" in status:
        return "Proposta"

    if "chamando" in status or "contato" in status or "negoci" in status or "andamento" in status:
        return "Chamando"

    if "novo" in status or "lead" in status:
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


# =========================================================
# GOOGLE SHEETS
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
        field for field in required_fields
        if not normalize_text(credentials_info.get(field, ""))
    ]

    if missing_fields:
        raise RuntimeError(
            "Estão faltando campos nos Secrets: " + ", ".join(missing_fields)
        )

    credentials_info["private_key"] = (
        str(credentials_info["private_key"]).replace("\\n", "\n").strip() + "\n"
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
        df.apply(lambda row: any(normalize_text(value) for value in row), axis=1)
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
    df_result = df.copy()

    df_result["_empresa"] = safe_series(df_result, columns.get("empresa"))
    df_result["_capital_num"] = safe_series(df_result, columns.get("capital")).apply(parse_money)
    df_result["_status_original"] = safe_series(df_result, columns.get("status")).replace("", "Novo Lead")
    df_result["_status_grupo"] = df_result["_status_original"].apply(status_group)
    df_result["_vendedor"] = safe_series(df_result, columns.get("vendedor")).replace("", "Sem vendedor")
    df_result["_telefone"] = safe_series(df_result, columns.get("telefone_b2b"))
    df_result["_data_chamado"] = safe_series(df_result, columns.get("data_chamado")).apply(parse_date)
    df_result["_ultima_atualizacao"] = safe_series(df_result, columns.get("ultima_atualizacao")).apply(parse_date)
    df_result["_pontuacao"] = df_result.apply(lambda row: calculate_score(row, columns), axis=1)
    df_result["_classificacao"] = df_result["_pontuacao"].apply(score_classification)

    return df_result


# =========================================================
# ESTILOS - LOGIN
# =========================================================
def apply_login_css():
    render_html("""
    <style>
        .stApp {
            background:
                radial-gradient(circle at 72% 45%, rgba(200, 0, 255, 0.18), transparent 26%),
                radial-gradient(circle at 18% 80%, rgba(140, 0, 255, 0.10), transparent 20%),
                linear-gradient(90deg, #05050C 0%, #0A0820 34%, #140A2E 65%, #1A0A2D 100%);
        }

        header[data-testid="stHeader"] {
            background: transparent !important;
        }

        div[data-testid="stToolbar"] {
            right: 1rem;
        }

        section[data-testid="stSidebar"] {
            display: none !important;
        }

        [data-testid="collapsedControl"] {
            display: none !important;
        }

        .block-container {
            max-width: 1600px !important;
            padding-top: 2rem;
            padding-bottom: 2rem;
        }

        .login-shell {
            min-height: 84vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .login-left-wrap {
            min-height: 740px;
            border-radius: 34px;
            background:
                linear-gradient(180deg, rgba(0,0,0,0.78), rgba(2,2,14,0.94)),
                linear-gradient(145deg, #0A0A14, #06060E);
            border: 1px solid rgba(255,255,255,0.06);
            padding: 46px 44px;
            position: relative;
            overflow: hidden;
            box-shadow: 0 30px 90px rgba(0,0,0,0.35);
        }

        .login-left-wrap::before {
            content: "";
            position: absolute;
            left: -12%;
            bottom: -10%;
            width: 115%;
            height: 38%;
            background:
                radial-gradient(circle at 25% 60%, rgba(237, 64, 176, 0.20), transparent 28%),
                radial-gradient(circle at 55% 80%, rgba(135, 51, 255, 0.18), transparent 22%);
            pointer-events: none;
        }

        .login-left-wrap::after {
            content: "";
            position: absolute;
            left: -5%;
            right: -5%;
            bottom: 0;
            height: 180px;
            background:
                radial-gradient(circle at 15% 100%, rgba(255, 0, 140, 0.50) 0%, rgba(255,0,140,0.10) 16%, transparent 24%),
                radial-gradient(circle at 60% 100%, rgba(140, 0, 255, 0.35) 0%, rgba(140,0,255,0.08) 14%, transparent 22%);
            filter: blur(16px);
            opacity: 0.85;
            pointer-events: none;
        }

        .oppi-logo-bubble {
            width: 128px;
            height: 128px;
            border-radius: 50%;
            background: linear-gradient(145deg, #FF4BAA 12%, #9C19FF 88%);
            position: relative;
            margin-top: 28px;
            margin-bottom: 52px;
            box-shadow: 0 18px 50px rgba(200, 20, 255, 0.28);
        }

        .oppi-logo-bubble::before {
            content: "";
            position: absolute;
            width: 44px;
            height: 44px;
            background: #06060B;
            border-radius: 50%;
            top: 31px;
            left: 43px;
        }

        .oppi-logo-bubble::after {
            content: "";
            position: absolute;
            width: 46px;
            height: 46px;
            background: linear-gradient(145deg, #FF4BAA 12%, #9C19FF 88%);
            clip-path: polygon(0 100%, 22% 28%, 100% 0);
            left: 4px;
            bottom: 0px;
            border-bottom-left-radius: 20px;
            transform: rotate(-5deg);
        }

        .login-brand-title {
            font-size: 3.65rem;
            line-height: 1.05;
            font-weight: 900;
            color: #FFFFFF;
            letter-spacing: -0.04em;
            margin: 0;
        }

        .login-brand-highlight {
            display: block;
            background: linear-gradient(90deg, #FF4BAA 0%, #D339FF 48%, #8C2BFF 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .login-brand-subtitle {
            margin-top: 18px;
            font-size: 1.05rem;
            color: rgba(255,255,255,0.84);
        }

        .login-accent-line {
            width: 84px;
            height: 4px;
            background: linear-gradient(90deg, #FF4BAA, #A92BFF);
            border-radius: 999px;
            margin: 42px 0 54px 0;
        }

        .login-feature-box {
            display: flex;
            align-items: center;
            gap: 18px;
            max-width: 350px;
        }

        .login-feature-icon {
            width: 58px;
            height: 58px;
            min-width: 58px;
            border-radius: 18px;
            border: 2px solid rgba(179, 77, 255, 0.55);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #D44BFF;
            font-size: 1.4rem;
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04);
        }

        .login-feature-text {
            font-size: 0.98rem;
            line-height: 1.65;
            color: rgba(255,255,255,0.80);
        }

        .right-login-stage {
            min-height: 740px;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }

        .right-login-stage::before {
            content: "";
            position: absolute;
            width: 360px;
            height: 360px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(222, 64, 212, 0.34) 0%, rgba(145, 50, 255, 0.14) 32%, transparent 68%);
            left: 50%;
            top: 50%;
            transform: translate(-50%, -38%);
            filter: blur(22px);
            pointer-events: none;
        }

        .login-card-shell {
            background: #FFFFFF;
            border-radius: 30px;
            padding: 34px 46px 30px 46px;
            box-shadow: 0 35px 90px rgba(0,0,0,0.32);
            width: 100%;
            max-width: 700px;
            position: relative;
            z-index: 5;
        }

        .login-top-icon {
            width: 74px;
            height: 74px;
            border-radius: 50%;
            background: #F4EAFB;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 16px auto;
            color: #A640FF;
            font-size: 1.85rem;
            font-weight: 700;
        }

        .login-card-title {
            text-align: center;
            font-size: 1.95rem;
            font-weight: 800;
            color: #1E2230;
            margin-bottom: 6px;
            line-height: 1.25;
        }

        .login-card-subtitle {
            text-align: center;
            font-size: 1rem;
            color: #757B8A;
            margin-bottom: 26px;
        }

        .login-card-shell label {
            font-size: 0.98rem !important;
            font-weight: 700 !important;
            color: #1D2432 !important;
        }

        .login-card-shell .stTextInput {
            margin-bottom: 0.85rem;
        }

        .login-card-shell [data-baseweb="input"] {
            border: 1px solid #D6D9E2 !important;
            border-radius: 16px !important;
            min-height: 58px;
            background: #FFFFFF !important;
            box-shadow: none !important;
        }

        .login-card-shell [data-baseweb="input"] input {
            font-size: 1.02rem !important;
            color: #1F2330 !important;
        }

        .login-card-shell .stButton > button,
        .login-card-shell .stForm button {
            width: 100%;
            border: none !important;
            border-radius: 16px !important;
            min-height: 58px !important;
            font-size: 1.12rem !important;
            font-weight: 800 !important;
            color: #FFFFFF !important;
            background: linear-gradient(90deg, #FF4E97 0%, #A91CFF 100%) !important;
            box-shadow: 0 12px 28px rgba(204, 42, 255, 0.28) !important;
            margin-top: 0.6rem;
        }

        .login-card-shell .stButton > button:hover,
        .login-card-shell .stForm button:hover {
            filter: brightness(1.03);
        }

        .login-separator-row {
            display: grid;
            grid-template-columns: 1fr auto 1fr;
            gap: 18px;
            align-items: center;
            margin-top: 24px;
        }

        .login-separator-line {
            height: 1px;
            background: #E6E6EB;
        }

        .forgot-password-text {
            color: #9A35FF;
            font-size: 1rem;
            font-weight: 700;
            text-align: center;
        }

        .login-error-box {
            margin-top: 14px;
            background: #FFF2F3;
            border: 1px solid #FFC9D1;
            color: #A22B40;
            padding: 12px 14px;
            border-radius: 14px;
            font-size: 0.95rem;
            font-weight: 600;
        }

        @media (max-width: 1100px) {
            .login-left-wrap, .right-login-stage {
                min-height: auto;
            }

            .login-left-wrap {
                margin-bottom: 18px;
            }

            .login-brand-title {
                font-size: 2.6rem;
            }

            .login-card-shell {
                max-width: 100%;
            }
        }
    </style>
    """)


# =========================================================
# ESTILOS - DASHBOARD
# =========================================================
def apply_dashboard_css():
    render_html("""
    <style>
        .stApp {
            background:
                radial-gradient(circle at 74% 12%, rgba(205, 0, 255, 0.15), transparent 20%),
                radial-gradient(circle at 95% 50%, rgba(255, 0, 153, 0.08), transparent 15%),
                linear-gradient(120deg, #04040A 0%, #090915 32%, #140B2A 65%, #0A071A 100%);
        }

        header[data-testid="stHeader"] {
            background: transparent !important;
        }

        .block-container {
            max-width: 1600px !important;
            padding-top: 1.2rem;
            padding-bottom: 1.8rem;
        }

        section[data-testid="stSidebar"] {
            background:
                linear-gradient(180deg, rgba(0,0,0,0.88), rgba(4,4,15,0.96));
            border-right: 1px solid rgba(255,255,255,0.05);
            min-width: 300px !important;
            max-width: 300px !important;
        }

        section[data-testid="stSidebar"] * {
            color: #FFFFFF;
        }

        [data-testid="stSidebarNav"] {
            display: none !important;
        }

        .oppi-side-brand {
            padding-top: 16px;
            padding-bottom: 10px;
        }

        .oppi-small-logo {
            width: 76px;
            height: 76px;
            border-radius: 50%;
            background: linear-gradient(145deg, #FF4BAA 12%, #9C19FF 88%);
            position: relative;
            margin-bottom: 24px;
            box-shadow: 0 14px 45px rgba(200, 20, 255, 0.28);
        }

        .oppi-small-logo::before {
            content: "";
            position: absolute;
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: #05050D;
            top: 20px;
            left: 26px;
        }

        .oppi-small-logo::after {
            content: "";
            position: absolute;
            width: 28px;
            height: 28px;
            background: linear-gradient(145deg, #FF4BAA 12%, #9C19FF 88%);
            clip-path: polygon(0 100%, 22% 28%, 100% 0);
            left: 2px;
            bottom: 0px;
            transform: rotate(-4deg);
        }

        .side-title-main {
            color: #FFFFFF;
            font-size: 1.18rem;
            font-weight: 900;
            line-height: 1.15;
            margin-bottom: 6px;
        }

        .side-title-highlight {
            display: block;
            background: linear-gradient(90deg, #FF4BAA, #AE26FF);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .side-subtitle {
            color: rgba(255,255,255,0.75);
            font-size: 0.94rem;
            margin-bottom: 20px;
        }

        .side-accent-line {
            width: 74px;
            height: 4px;
            border-radius: 999px;
            background: linear-gradient(90deg, #FF4BAA, #AE26FF);
            margin-bottom: 28px;
        }

        .side-tip-box {
            display: flex;
            align-items: center;
            gap: 14px;
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 18px;
            padding: 16px 14px;
            margin-top: 18px;
            margin-bottom: 26px;
        }

        .side-tip-icon {
            width: 48px;
            height: 48px;
            min-width: 48px;
            border-radius: 15px;
            border: 1px solid rgba(178, 62, 255, 0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #D94BFF;
            font-size: 1.1rem;
        }

        .side-tip-text {
            color: rgba(255,255,255,0.78);
            font-size: 0.86rem;
            line-height: 1.5;
        }

        .side-user-box {
            position: relative;
            margin-top: 18px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 20px;
            padding: 14px;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .side-user-avatar {
            width: 46px;
            height: 46px;
            min-width: 46px;
            border-radius: 50%;
            background: linear-gradient(135deg, #B741FF, #FF4BAA);
            color: #FFFFFF;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
        }

        .side-user-name {
            color: #FFFFFF;
            font-size: 0.95rem;
            font-weight: 700;
            line-height: 1.2;
        }

        .side-user-role {
            color: rgba(255,255,255,0.68);
            font-size: 0.82rem;
        }

        .page-title {
            font-size: 2.65rem;
            color: #FFFFFF;
            font-weight: 900;
            line-height: 1.1;
            margin-bottom: 6px;
            letter-spacing: -0.03em;
        }

        .page-subtitle {
            color: rgba(255,255,255,0.74);
            font-size: 1rem;
            margin-bottom: 16px;
        }

        .refresh-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            border-radius: 14px;
            padding: 12px 16px;
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(255,255,255,0.03);
            color: rgba(255,255,255,0.88);
            font-size: 0.92rem;
            width: 100%;
            justify-content: center;
        }

        .glass-panel {
            background: linear-gradient(180deg, rgba(18, 16, 39, 0.88), rgba(8, 7, 23, 0.95));
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 24px;
            padding: 16px;
            box-shadow: 0 25px 60px rgba(0,0,0,0.24);
        }

        .metric-dark-card {
            background: linear-gradient(135deg, rgba(20, 20, 40, 0.98), rgba(10, 10, 24, 0.98));
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 22px;
            padding: 18px;
            min-height: 140px;
            box-shadow: 0 16px 45px rgba(0,0,0,0.24);
        }

        .metric-icon-wrap {
            width: 46px;
            height: 46px;
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #FFFFFF;
            font-size: 1.1rem;
            margin-bottom: 16px;
        }

        .metric-title-dark {
            color: rgba(255,255,255,0.82);
            font-size: 0.96rem;
            font-weight: 700;
            margin-bottom: 6px;
        }

        .metric-value-dark {
            color: #FFFFFF;
            font-size: 2rem;
            font-weight: 900;
            line-height: 1;
            margin-bottom: 8px;
        }

        .metric-delta-dark {
            color: #4BE47E;
            font-size: 0.92rem;
            font-weight: 700;
        }

        .metric-delta-dark.neutral {
            color: rgba(255,255,255,0.68);
        }

        .section-card-dark {
            background: linear-gradient(180deg, rgba(18, 16, 39, 0.94), rgba(8, 7, 23, 0.98));
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 24px;
            padding: 20px;
            box-shadow: 0 25px 60px rgba(0,0,0,0.24);
            height: 100%;
        }

        .section-title-dark {
            color: #FFFFFF;
            font-size: 1.65rem;
            font-weight: 900;
            margin-bottom: 4px;
        }

        .section-subtitle-dark {
            color: rgba(255,255,255,0.68);
            font-size: 0.96rem;
            margin-bottom: 16px;
        }

        .status-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .status-row {
            display: grid;
            grid-template-columns: 1fr auto auto;
            gap: 14px;
            align-items: center;
            padding: 12px 14px;
            border-radius: 16px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.04);
        }

        .status-left {
            display: flex;
            align-items: center;
            gap: 12px;
            color: #FFFFFF;
            font-weight: 700;
        }

        .status-badge {
            width: 34px;
            height: 34px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #FFFFFF;
            font-size: 0.95rem;
            font-weight: 800;
        }

        .status-count {
            color: #FFFFFF;
            font-weight: 800;
        }

        .status-percent {
            color: rgba(255,255,255,0.72);
            font-weight: 700;
        }

        .bottom-link {
            text-align: center;
            margin-top: 14px;
            color: #C145FF;
            font-weight: 700;
            font-size: 0.95rem;
        }

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div {
            background: rgba(255,255,255,0.03) !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            border-radius: 16px !important;
            min-height: 56px;
            color: #FFFFFF !important;
        }

        div[data-baseweb="select"] * {
            color: #FFFFFF !important;
        }

        .stDateInput > div > div {
            background: rgba(255,255,255,0.03) !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            border-radius: 16px !important;
            color: #FFFFFF !important;
        }

        label {
            color: rgba(255,255,255,0.84) !important;
            font-weight: 600 !important;
        }

        .stTextInput input {
            color: #FFFFFF !important;
        }

        .stTextInput input::placeholder {
            color: rgba(255,255,255,0.42) !important;
        }

        .stMultiSelect [data-baseweb="tag"] {
            background: rgba(255,255,255,0.08) !important;
            color: #FFFFFF !important;
        }

        .stButton > button {
            border-radius: 16px !important;
            border: none !important;
            background: linear-gradient(90deg, #FF4BAA 0%, #A91CFF 100%) !important;
            color: #FFFFFF !important;
            font-weight: 800 !important;
            min-height: 50px !important;
            box-shadow: 0 12px 28px rgba(204, 42, 255, 0.18) !important;
        }

        .stButton > button:hover {
            filter: brightness(1.03);
        }

        div[data-testid="stDataFrame"] {
            background: transparent !important;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 18px;
            overflow: hidden;
        }

        .stAlert {
            border-radius: 16px !important;
        }

        .sidebar-radio-label {
            color: rgba(255,255,255,0.68);
            font-size: 0.82rem;
            font-weight: 800;
            letter-spacing: 0.18em;
            margin-top: 10px;
            margin-bottom: 8px;
        }
    </style>
    """)


# =========================================================
# LOGIN
# =========================================================
def check_login(username: str, password: str) -> bool:
    secret_user = st.secrets.get("APP_USERNAME", "oppi")
    secret_pass = st.secrets.get("APP_PASSWORD", "Oppi@2026!")
    return username == secret_user and password == secret_pass


def render_login_page():
    apply_login_css()

    col_left, col_right = st.columns([0.88, 1.32], gap="large")

    with col_left:
        render_html("""
        <div class="login-shell">
            <div class="login-left-wrap">
                <div class="oppi-logo-bubble"></div>

                <div class="login-brand-title">
                    Dashboard
                    <span class="login-brand-highlight">Oppi Comercial</span>
                </div>

                <div class="login-brand-subtitle">
                    Painel de gestão comercial
                </div>

                <div class="login-accent-line"></div>

                <div class="login-feature-box">
                    <div class="login-feature-icon">🛡️</div>
                    <div class="login-feature-text">
                        Segurança, performance e inteligência para impulsionar seus resultados.
                    </div>
                </div>
            </div>
        </div>
        """)

    with col_right:
        render_html("""
        <div class="right-login-stage">
            <div class="login-card-shell">
                <div class="login-top-icon">🛡️</div>
                <div class="login-card-title">Acesse o painel comercial da Oppi Tech</div>
                <div class="login-card-subtitle">Faça login para continuar</div>
        """)

        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Usuário", placeholder="Digite seu usuário")
            password = st.text_input("Senha", type="password", placeholder="Digite sua senha")
            submitted = st.form_submit_button("Entrar", use_container_width=True)

        render_html("""
                <div class="login-separator-row">
                    <div class="login-separator-line"></div>
                    <div class="forgot-password-text">Esqueceu sua senha?</div>
                    <div class="login-separator-line"></div>
                </div>
            </div>
        </div>
        """)

        if submitted:
            if check_login(username, password):
                st.session_state.authenticated = True
                st.session_state.auth_error = ""
                st.rerun()
            else:
                st.session_state.auth_error = "Usuário ou senha inválidos."

        if st.session_state.auth_error:
            render_html(f"""
            <div class="login-error-box">
                {html.escape(st.session_state.auth_error)}
            </div>
            """)


# =========================================================
# SIDEBAR
# =========================================================
def render_sidebar():
    with st.sidebar:
        render_html("""
        <div class="oppi-side-brand">
            <div class="oppi-small-logo"></div>

            <div class="side-title-main">
                Dashboard
                <span class="side-title-highlight">Oppi Comercial</span>
            </div>

            <div class="side-subtitle">
                Painel de gestão comercial
            </div>

            <div class="side-accent-line"></div>
        </div>
        """)

        render_html('<div class="sidebar-radio-label">NAVEGAÇÃO</div>')

        page = st.radio(
            "Menu",
            ["Visão Geral", "Propostas", "Pesos e Medidas"],
            label_visibility="collapsed",
            index=["Visão Geral", "Propostas", "Pesos e Medidas"].index(st.session_state.selected_page),
        )
        st.session_state.selected_page = page

        render_html("""
        <div class="side-tip-box">
            <div class="side-tip-icon">🛡️</div>
            <div class="side-tip-text">
                Segurança, performance e inteligência para impulsionar seus resultados.
            </div>
        </div>
        """)

        render_html("""
        <div class="side-user-box">
            <div class="side-user-avatar">OT</div>
            <div>
                <div class="side-user-name">Oppi Tech</div>
                <div class="side-user-role">Painel comercial</div>
            </div>
        </div>
        """)

        st.write("")
        if st.button("Sair", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.auth_error = ""
            st.rerun()

    return page


# =========================================================
# FILTROS
# =========================================================
def apply_filters(df: pd.DataFrame):
    work_df = df.copy()

    date_min = None
    date_max = None
    valid_dates = work_df["_data_chamado"].dropna()

    if not valid_dates.empty:
        date_min = valid_dates.min().date()
        date_max = valid_dates.max().date()
    else:
        date_max = date.today()
        date_min = date_max - timedelta(days=30)

    top_left, top_right = st.columns([3.7, 1.0], gap="large")

    with top_left:
        render_html("""
        <div class="page-title">Visão Geral</div>
        <div class="page-subtitle">Acompanhe o desempenho da operação comercial em tempo real.</div>
        """)

    with top_right:
        render_html('<div style="height: 38px;"></div>')
        render_html('<div class="refresh-pill">🗓️ Atualizado agora há poucos instantes</div>')

    f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.1, 1.2], gap="medium")

    seller_options = sorted([
        seller for seller in work_df["_vendedor"].dropna().astype(str).unique().tolist()
        if normalize_text(seller)
    ])

    status_options = ["Novo Lead", "Chamando", "Sem Interesse", "Não Responde", "Fechado", "Proposta"]

    with f1:
        selected_seller = st.selectbox(
            "Vendedor",
            ["Todos os vendedores"] + seller_options,
            index=0
        )

    with f2:
        selected_status = st.selectbox(
            "Status",
            ["Todos os status"] + status_options,
            index=0
        )

    with f3:
        selected_range = st.date_input(
            "Período",
            value=(date_min, date_max),
            min_value=date_min,
            max_value=max(date_max, date.today())
        )

    with f4:
        search_term = st.text_input(
            "Buscar empresa ou telefone",
            placeholder="Digite para buscar..."
        )

    if selected_seller != "Todos os vendedores":
        work_df = work_df[work_df["_vendedor"] == selected_seller].copy()

    if selected_status != "Todos os status":
        work_df = work_df[work_df["_status_grupo"] == selected_status].copy()

    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        start_date, end_date = selected_range
        work_df = work_df[
            (work_df["_data_chamado"].isna()) |
            (
                (work_df["_data_chamado"].dt.date >= start_date) &
                (work_df["_data_chamado"].dt.date <= end_date)
            )
        ].copy()
    else:
        start_date = date_min
        end_date = date_max

    if normalize_text(search_term):
        term = normalize_search_text(search_term)

        def row_contains(row):
            values = [
                normalize_text(row.get("_empresa", "")),
                normalize_text(row.get("_telefone", "")),
                normalize_text(row.get("_vendedor", "")),
                normalize_text(row.get("_status_grupo", "")),
            ]
            blob = normalize_search_text(" | ".join(values))
            return term in blob

        work_df = work_df[work_df.apply(row_contains, axis=1)].copy()

    return work_df, start_date, end_date


# =========================================================
# COMPONENTES VISUAIS
# =========================================================
def render_metric_card(title, value, delta_text, icon, color):
    render_html(f"""
    <div class="metric-dark-card">
        <div class="metric-icon-wrap" style="background:{color};">{icon}</div>
        <div class="metric-title-dark">{html.escape(str(title))}</div>
        <div class="metric-value-dark">{html.escape(str(value))}</div>
        <div class="metric-delta-dark {'neutral' if not delta_text else ''}">
            {html.escape(delta_text if delta_text else 'Sem comparação disponível')}
        </div>
    </div>
    """)


def render_status_summary(filtered_df: pd.DataFrame):
    status_order = ["Novo Lead", "Chamando", "Sem Interesse", "Não Responde", "Fechado"]
    colors = {
        "Novo Lead": "#6A7BFF",
        "Chamando": "#B66B1E",
        "Sem Interesse": "#49B7C8",
        "Não Responde": "#E25577",
        "Fechado": "#73C956",
    }
    icons = {
        "Novo Lead": "✦",
        "Chamando": "☎",
        "Sem Interesse": "⊘",
        "Não Responde": "⚑",
        "Fechado": "✓",
    }

    total = max(len(filtered_df), 1)

    rows_html = ""
    for status in status_order:
        count = int((filtered_df["_status_grupo"] == status).sum())
        percent = round((count / total) * 100)
        rows_html += f"""
        <div class="status-row">
            <div class="status-left">
                <div class="status-badge" style="background:{colors[status]};">{icons[status]}</div>
                <div>{status}</div>
            </div>
            <div class="status-count">{count}</div>
            <div class="status-percent">{percent}%</div>
        </div>
        """

    render_html(f"""
    <div class="section-card-dark">
        <div class="section-title-dark" style="font-size:1.45rem;">Resumo por status</div>
        <div class="section-subtitle-dark">Distribuição atual dos leads no comercial.</div>
        <div class="status-list">
            {rows_html}
        </div>
        <div class="bottom-link">Ver todos os status</div>
    </div>
    """)


# =========================================================
# PÁGINA: VISÃO GERAL
# =========================================================
def render_overview_page(df: pd.DataFrame, columns: dict):
    filtered_df, start_date, end_date = apply_filters(df)

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    called_today = int((filtered_df["_data_chamado"].dt.date == today).sum())
    called_week = int((filtered_df["_data_chamado"].dt.date >= week_start).sum())
    called_month = int((filtered_df["_data_chamado"].dt.date >= month_start).sum())
    companies_contacted = int(filtered_df["_empresa"].replace("", pd.NA).dropna().nunique())

    prev_day = today - timedelta(days=1)
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_start - timedelta(days=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    prev_today = int((df["_data_chamado"].dt.date == prev_day).sum())
    prev_week = int(((df["_data_chamado"].dt.date >= prev_week_start) & (df["_data_chamado"].dt.date <= prev_week_end)).sum())
    prev_month = int(((df["_data_chamado"].dt.date >= prev_month_start) & (df["_data_chamado"].dt.date <= prev_month_end)).sum())

    def delta_text(current, previous, label):
        if previous == 0:
            return f"Sem base anterior"
        pct = round(((current - previous) / previous) * 100)
        symbol = "▲" if pct >= 0 else "▼"
        return f"{symbol} {abs(pct)}% vs {label}"

    c1, c2, c3, c4 = st.columns(4, gap="medium")

    with c1:
        render_metric_card(
            "Chamados hoje",
            str(called_today),
            delta_text(called_today, prev_today, "ontem"),
            "📞",
            "linear-gradient(135deg, #FF4BAA, #C223FF)"
        )

    with c2:
        render_metric_card(
            "Chamados na semana",
            str(called_week),
            delta_text(called_week, prev_week, "semana anterior"),
            "🗓️",
            "linear-gradient(135deg, #AE4BFF, #6E23FF)"
        )

    with c3:
        render_metric_card(
            "Chamados no mês",
            str(called_month),
            delta_text(called_month, prev_month, "mês anterior"),
            "📊",
            "linear-gradient(135deg, #FF4BAA, #8F2BFF)"
        )

    with c4:
        render_metric_card(
            "Empresas contatadas",
            str(companies_contacted),
            "Base atual filtrada",
            "🏢",
            "linear-gradient(135deg, #8F2BFF, #C94AFF)"
        )

    st.write("")

    left_chart, right_summary = st.columns([2.2, 1.0], gap="medium")

    with left_chart:
        render_html("""
        <div class="section-card-dark">
            <div class="section-title-dark">Chamados por dia</div>
            <div class="section-subtitle-dark">Volume de chamados conforme o período selecionado.</div>
        """)
        valid_dates = filtered_df["_data_chamado"].dropna()

        if valid_dates.empty:
            chart_df = pd.DataFrame({
                "Dia": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
                "Quantidade": [0, 0, 0, 0, 0, 0, 0]
            })
        else:
            chart_df = filtered_df.dropna(subset=["_data_chamado"]).copy()
            chart_df["DiaReal"] = chart_df["_data_chamado"].dt.date
            chart_df = chart_df.groupby("DiaReal").size().reset_index(name="Quantidade")
            chart_df = chart_df.sort_values("DiaReal")
            chart_df["Dia"] = pd.to_datetime(chart_df["DiaReal"]).dt.strftime("%d/%m")

        fig = px.area(
            chart_df,
            x="Dia",
            y="Quantidade",
            markers=True,
        )

        fig.update_traces(
            line=dict(color="#E14BFF", width=4),
            marker=dict(size=9, color="#FFFFFF", line=dict(width=3, color="#D74BFF")),
            fillcolor="rgba(224, 67, 255, 0.35)",
        )

        fig.update_layout(
            height=380,
            margin=dict(l=20, r=20, t=10, b=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FFFFFF"),
            xaxis_title="",
            yaxis_title="",
        )
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(gridcolor="rgba(255,255,255,0.08)")

        st.plotly_chart(fig, use_container_width=True)
        render_html("</div>")

    with right_summary:
        render_status_summary(filtered_df)

    st.write("")

    render_html("""
    <div class="section-card-dark">
        <div class="section-title-dark">Últimos chamados</div>
        <div class="section-subtitle-dark">Empresas registradas no comercial com status e responsável.</div>
    """)
    table_df = filtered_df.copy()

    display_df = pd.DataFrame({
        "Empresa": table_df["_empresa"],
        "Telefone": table_df["_telefone"],
        "Status": table_df["_status_grupo"],
        "Vendedor": table_df["_vendedor"],
        "Data": table_df["_data_chamado"].dt.strftime("%d/%m/%Y").fillna(""),
    })

    display_df = display_df.replace("NaT", "").fillna("")
    display_df = display_df.sort_values(by="Data", ascending=False)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=340,
    )
    render_html("</div>")


# =========================================================
# PÁGINA: PROPOSTAS
# =========================================================
def render_proposals_page(df: pd.DataFrame, columns: dict):
    render_html("""
    <div class="page-title">Propostas</div>
    <div class="page-subtitle">Acompanhe as empresas com evolução comercial e propostas enviadas.</div>
    """)

    local_df = df.copy()

    col1, col2, col3 = st.columns([1.2, 1.2, 1.4], gap="medium")

    seller_options = sorted([
        seller for seller in local_df["_vendedor"].dropna().astype(str).unique().tolist()
        if normalize_text(seller)
    ])

    status_options = sorted([
        status for status in local_df["_status_grupo"].dropna().astype(str).unique().tolist()
        if normalize_text(status)
    ])

    with col1:
        selected_seller = st.selectbox("Vendedor", ["Todos os vendedores"] + seller_options, key="prop_seller")

    with col2:
        selected_status = st.selectbox("Status", ["Todos os status"] + status_options, key="prop_status")

    with col3:
        search_term = st.text_input("Buscar empresa", placeholder="Digite o nome da empresa...", key="prop_search")

    if selected_seller != "Todos os vendedores":
        local_df = local_df[local_df["_vendedor"] == selected_seller].copy()

    if selected_status != "Todos os status":
        local_df = local_df[local_df["_status_grupo"] == selected_status].copy()

    if normalize_text(search_term):
        term = normalize_search_text(search_term)
        local_df = local_df[
            local_df["_empresa"].apply(lambda x: term in normalize_search_text(x))
        ].copy()

    local_df = local_df[
        local_df["_status_grupo"].isin(["Proposta", "Chamando", "Fechado", "Novo Lead"])
    ].copy()

    p1, p2, p3, p4 = st.columns(4, gap="medium")

    with p1:
        render_metric_card("No pipeline", str(len(local_df)), "Base filtrada", "📂", "linear-gradient(135deg, #6E2BFF, #BD3BFF)")
    with p2:
        render_metric_card("Em proposta", str(int((local_df["_status_grupo"] == "Proposta").sum())), "Status Proposta", "📄", "linear-gradient(135deg, #FF4BAA, #D439FF)")
    with p3:
        render_metric_card("Chamando", str(int((local_df["_status_grupo"] == "Chamando").sum())), "Leads em contato", "☎", "linear-gradient(135deg, #B06A19, #FF9A2F)")
    with p4:
        render_metric_card("Fechados", str(int((local_df["_status_grupo"] == "Fechado").sum())), "Clientes conquistados", "✅", "linear-gradient(135deg, #45B95A, #80D55D)")

    st.write("")

    render_html("""
    <div class="section-card-dark">
        <div class="section-title-dark">Tabela de propostas</div>
        <div class="section-subtitle-dark">Visualize as informações principais do pipeline comercial.</div>
    """)
    display_df = pd.DataFrame({
        "Empresa": local_df["_empresa"],
        "CNPJ": safe_series(local_df, columns.get("cnpj")),
        "Telefone": local_df["_telefone"],
        "Email": safe_series(local_df, columns.get("email")),
        "Instagram": safe_series(local_df, columns.get("instagram")),
        "Vendedor": local_df["_vendedor"],
        "Status": local_df["_status_grupo"],
        "Última atualização": local_df["_ultima_atualizacao"].dt.strftime("%d/%m/%Y").fillna(""),
    })

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=520,
    )
    render_html("</div>")


# =========================================================
# PÁGINA: PESOS E MEDIDAS
# =========================================================
def render_scoring_page(df: pd.DataFrame, columns: dict):
    render_html("""
    <div class="page-title">Pesos e Medidas</div>
    <div class="page-subtitle">Pontuação dos leads com base na qualidade dos dados e avanço no comercial.</div>
    """)

    local_df = df.copy()

    hot = int((local_df["_classificacao"] == "Lead Quente").sum())
    warm = int((local_df["_classificacao"] == "Lead Morno").sum())
    cold = int((local_df["_classificacao"] == "Lead Frio").sum())
    avg = int(round(local_df["_pontuacao"].mean())) if not local_df.empty else 0

    s1, s2, s3, s4 = st.columns(4, gap="medium")

    with s1:
        render_metric_card("Leads Quentes", str(hot), "Pontuação acima de 70", "🔥", "linear-gradient(135deg, #FF4BAA, #D83BFF)")
    with s2:
        render_metric_card("Leads Mornos", str(warm), "Pontuação entre 40 e 69", "🌤️", "linear-gradient(135deg, #FF9C2D, #FFCC45)")
    with s3:
        render_metric_card("Leads Frios", str(cold), "Pontuação abaixo de 40", "❄️", "linear-gradient(135deg, #5F8BFF, #66C2FF)")
    with s4:
        render_metric_card("Pontuação Média", str(avg), "Média da base", "⚖️", "linear-gradient(135deg, #7A39FF, #D64AFF)")

    st.write("")

    left, right = st.columns([1.0, 1.2], gap="medium")

    with left:
        render_html("""
        <div class="section-card-dark">
            <div class="section-title-dark">Distribuição da classificação</div>
            <div class="section-subtitle-dark">Separação automática por temperatura do lead.</div>
        """)
        pie_df = local_df["_classificacao"].value_counts().reset_index()
        pie_df.columns = ["Classificação", "Quantidade"]

        if pie_df.empty:
            pie_df = pd.DataFrame({
                "Classificação": ["Lead Frio"],
                "Quantidade": [0]
            })

        fig = px.pie(
            pie_df,
            names="Classificação",
            values="Quantidade",
            hole=0.58,
        )
        fig.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FFFFFF"),
        )
        st.plotly_chart(fig, use_container_width=True)
        render_html("</div>")

    with right:
        render_html("""
        <div class="section-card-dark">
            <div class="section-title-dark">Critérios da pontuação</div>
            <div class="section-subtitle-dark">Regras usadas na classificação automática dos leads.</div>
        """)
        rules_df = pd.DataFrame({
            "Critério": [
                "Telefone preenchido",
                "E-mail preenchido",
                "Site preenchido",
                "Instagram preenchido",
                "LinkedIn preenchido",
                "Sócio identificado",
                "Capital informado",
                "Status comercial",
            ],
            "Peso": [
                "15 pontos",
                "10 pontos",
                "10 pontos",
                "10 pontos",
                "5 pontos",
                "10 pontos",
                "até 20 pontos",
                "até 20 pontos",
            ]
        })
        st.dataframe(
            rules_df,
            use_container_width=True,
            hide_index=True,
            height=360,
        )
        render_html("</div>")

    st.write("")

    render_html("""
    <div class="section-card-dark">
        <div class="section-title-dark">Ranking de empresas</div>
        <div class="section-subtitle-dark">Leads ordenados da maior para a menor pontuação.</div>
    """)

    ranking_df = local_df.sort_values(by="_pontuacao", ascending=False).copy()

    display_df = pd.DataFrame({
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
    })

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=520,
    )
    render_html("</div>")


# =========================================================
# ERROS
# =========================================================
def render_connection_error(error: Exception):
    apply_dashboard_css()
    render_html("""
    <div class="page-title">Dashboard Oppi Comercial</div>
    <div class="page-subtitle">Erro ao conectar com a planilha.</div>
    """)

    if isinstance(error, SpreadsheetNotFound):
        st.error(
            "A credencial foi aceita, mas a planilha não foi localizada. "
            "Confirme se o SHEET_ID está correto e se a planilha foi compartilhada diretamente com o e-mail da conta de serviço."
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
# MAIN
# =========================================================
def main():
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
        render_html("""
        <div class="page-title">Dashboard Oppi Comercial</div>
        <div class="page-subtitle">A conexão foi realizada, mas a planilha está vazia.</div>
        """)
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
