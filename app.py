import html
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

import gspread
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from google.oauth2.service_account import Credentials
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound


# =========================================================
# CONFIGURAÇÃO PRINCIPAL
# =========================================================

st.set_page_config(
    page_title="Dashboard Oppi Comercial",
    page_icon="🟣",
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

STATUS_ORDER = [
    "Novo Lead",
    "Chamando",
    "Sem Interesse",
    "Não Responde",
    "Fechado",
]

STATUS_META = {
    "Novo Lead": {"icon": "✦", "class": "status-new", "color": "#8D78FF"},
    "Chamando": {"icon": "☎", "class": "status-call", "color": "#FF9B63"},
    "Sem Interesse": {"icon": "⊘", "class": "status-no", "color": "#55D3D8"},
    "Não Responde": {"icon": "⚑", "class": "status-wait", "color": "#FF668D"},
    "Fechado": {"icon": "✓", "class": "status-done", "color": "#7CD957"},
}


# =========================================================
# HTML E CSS
# =========================================================

def render_html(content: str) -> None:
    clean_content = " ".join(
        line.strip()
        for line in content.splitlines()
        if line.strip()
    )
    st.markdown(clean_content, unsafe_allow_html=True)


render_html(
    """
    <style>
        :root {
            --bg: #070711;
            --panel: #11111d;
            --panel-2: #171728;
            --panel-3: #1d1d31;
            --border: rgba(255,255,255,0.08);
            --text: #f7f7fb;
            --muted: #9696aa;
            --pink: #f63b9b;
            --purple: #8d24ff;
            --green: #74dc63;
        }

        html, body, [class*="css"] {
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }

        .stApp {
            background:
                radial-gradient(circle at 92% 4%, rgba(133, 82, 255, 0.12), transparent 25%),
                radial-gradient(circle at 82% 76%, rgba(246, 59, 155, 0.09), transparent 24%),
                linear-gradient(135deg, #070711 0%, #0a0915 52%, #0b0918 100%);
            color: var(--text);
        }

        .block-container {
            max-width: 1540px;
            padding-top: 1.25rem;
            padding-bottom: 2.5rem;
        }

        header[data-testid="stHeader"] {
            background: rgba(7,7,17,0.72);
            border-bottom: 1px solid rgba(255,255,255,0.04);
        }

        section[data-testid="stSidebar"] {
            background:
                radial-gradient(circle at 20% 84%, rgba(140,36,255,0.16), transparent 22%),
                linear-gradient(180deg, #070710 0%, #0a0a13 100%);
            border-right: 1px solid rgba(255,255,255,0.06);
            width: 252px !important;
        }

        section[data-testid="stSidebar"] > div {
            width: 252px !important;
        }

        section[data-testid="stSidebar"] * {
            color: #ffffff;
        }

        section[data-testid="stSidebar"] div[role="radiogroup"] label {
            background: transparent;
            border: 1px solid transparent;
            border-radius: 12px;
            padding: 10px 12px;
            margin: 3px 0;
            transition: all .18s ease;
        }

        section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
            background: rgba(255,255,255,0.05);
            border-color: rgba(255,255,255,0.07);
        }

        section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
            background: linear-gradient(90deg, rgba(246,59,155,.28), rgba(141,36,255,.62));
            border-color: rgba(188,94,255,.5);
            box-shadow: 0 9px 24px rgba(141,36,255,.13);
        }

        section[data-testid="stSidebar"] div[role="radiogroup"] label p {
            font-size: 0.9rem;
            font-weight: 750;
        }

        section[data-testid="stSidebar"] .stButton > button {
            width: 100%;
            border-radius: 11px;
            background: linear-gradient(90deg, #ef3d99, #8a22f8);
            border: 0;
            color: white;
            font-weight: 800;
            min-height: 42px;
        }

        h1, h2, h3, h4, p, label, span, div {
            color: var(--text);
        }

        h1 {
            font-size: 2rem !important;
            letter-spacing: -0.04em;
            font-weight: 900 !important;
        }

        .stCaption, [data-testid="stCaptionContainer"] {
            color: var(--muted) !important;
        }

        div[data-baseweb="select"] > div,
        div[data-testid="stDateInput"] > div > div,
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextInput"] > div > div {
            background: rgba(255,255,255,0.035) !important;
            border-color: rgba(255,255,255,0.08) !important;
            color: #f8f8fb !important;
            border-radius: 10px !important;
        }

        div[data-testid="stTextInput"] input::placeholder {
            color: #7f7f91;
        }

        div[data-testid="stDateInput"] input {
            color: #f7f7fb !important;
        }

        .oppi-logo {
            width: 58px;
            height: 58px;
            border-radius: 50% 50% 50% 16%;
            background: linear-gradient(145deg, #f23d9c 0%, #c523d7 52%, #7e1cff 100%);
            position: relative;
            box-shadow: 0 0 34px rgba(210,42,216,.35);
            transform: rotate(-18deg);
            margin-bottom: 18px;
        }

        .oppi-logo::after {
            content: "";
            position: absolute;
            width: 23px;
            height: 23px;
            border-radius: 50%;
            background: #090911;
            left: 18px;
            top: 17px;
        }

        .sidebar-brand {
            padding: 18px 8px 10px 8px;
        }

        .sidebar-brand h2 {
            font-size: 1.18rem;
            line-height: 1.08;
            margin: 0;
            color: #fff;
        }

        .gradient-title {
            background: linear-gradient(90deg, #f43e9b, #8d24ff);
            -webkit-background-clip: text;
            color: transparent !important;
        }

        .sidebar-brand p {
            color: #9a9aad;
            font-size: .76rem;
            margin-top: 7px;
        }

        .sidebar-accent {
            width: 42px;
            height: 3px;
            border-radius: 999px;
            background: linear-gradient(90deg, #f43e9b, #8d24ff);
            margin: 20px 0 12px 0;
        }

        .sidebar-section-label {
            color: #9d9db0;
            font-size: .64rem;
            letter-spacing: .22em;
            font-weight: 800;
            margin: 13px 0 6px 0;
        }

        .sidebar-security {
            border: 1px solid rgba(255,255,255,.06);
            border-radius: 12px;
            padding: 11px;
            margin-top: 28px;
            background: rgba(255,255,255,.025);
            color: #a5a5b6;
            font-size: .69rem;
            line-height: 1.45;
        }

        .sidebar-security .shield {
            display: inline-flex;
            width: 27px;
            height: 27px;
            align-items: center;
            justify-content: center;
            border: 1px solid rgba(199,77,255,.7);
            border-radius: 8px;
            color: #d46cff;
            margin-right: 8px;
        }

        .page-head {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
            margin-bottom: 12px;
        }

        .page-head h1 {
            margin: 0;
            color: #fff;
        }

        .page-head p {
            color: var(--muted);
            font-size: .82rem;
            margin: 4px 0 0 0;
        }

        .updated-pill {
            border: 1px solid rgba(255,255,255,.08);
            border-radius: 10px;
            padding: 10px 14px;
            background: rgba(255,255,255,.025);
            color: #b6b6c7;
            font-size: .74rem;
            white-space: nowrap;
        }

        .filter-panel {
            border: 1px solid rgba(255,255,255,.08);
            background: rgba(255,255,255,.032);
            border-radius: 16px;
            padding: 11px 14px 4px 14px;
            margin-bottom: 15px;
        }

        .kpi-card {
            min-height: 118px;
            border: 1px solid rgba(255,255,255,.075);
            border-radius: 16px;
            background:
                radial-gradient(circle at 88% 82%, rgba(141,36,255,.10), transparent 31%),
                linear-gradient(145deg, rgba(255,255,255,.045), rgba(255,255,255,.018));
            padding: 15px;
            display: flex;
            gap: 12px;
            align-items: flex-start;
            box-shadow: 0 12px 28px rgba(0,0,0,.13);
        }

        .kpi-icon {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.05rem;
            background: linear-gradient(145deg, rgba(246,59,155,.55), rgba(141,36,255,.55));
            border: 1px solid rgba(255,255,255,.12);
            flex: 0 0 auto;
        }

        .kpi-body {
            min-width: 0;
            flex: 1;
        }

        .kpi-label {
            color: #f7f7fb;
            font-size: .77rem;
            font-weight: 800;
        }

        .kpi-value {
            color: #fff;
            font-size: 1.65rem;
            line-height: 1.05;
            font-weight: 950;
            margin-top: 6px;
        }

        .kpi-sub {
            color: #8d8da1;
            font-size: .68rem;
            margin-top: 7px;
        }

        .kpi-sub strong {
            color: var(--green);
        }

        .dark-card {
            background: rgba(255,255,255,.035);
            border: 1px solid rgba(255,255,255,.075);
            border-radius: 16px;
            padding: 16px;
            box-shadow: 0 12px 28px rgba(0,0,0,.13);
        }

        .card-title {
            color: #fff;
            font-size: .94rem;
            font-weight: 900;
            margin-bottom: 5px;
        }

        .card-sub {
            color: #9696aa;
            font-size: .73rem;
        }

        .status-row {
            display: grid;
            grid-template-columns: 32px 1fr auto auto;
            gap: 8px;
            align-items: center;
            padding: 9px 9px;
            margin-top: 7px;
            border-radius: 10px;
            background: rgba(255,255,255,.028);
        }

        .status-icon {
            width: 27px;
            height: 27px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 8px;
            font-size: .82rem;
        }

        .status-name {
            color: #f4f4f7;
            font-size: .78rem;
            font-weight: 750;
        }

        .status-count {
            color: #fff;
            font-size: .78rem;
            font-weight: 850;
        }

        .status-percent {
            color: #b599ff;
            font-size: .74rem;
            min-width: 38px;
            text-align: right;
        }

        .status-new { background: rgba(114,142,255,.22); color: #93a7ff; }
        .status-call { background: rgba(255,155,99,.20); color: #ffad79; }
        .status-no { background: rgba(85,211,216,.18); color: #67d9dd; }
        .status-wait { background: rgba(255,102,141,.18); color: #ff7899; }
        .status-done { background: rgba(124,217,87,.18); color: #88e26a; }

        .table-card {
            background: rgba(255,255,255,.035);
            border: 1px solid rgba(255,255,255,.075);
            border-radius: 16px;
            padding: 14px 16px 6px 16px;
            margin-top: 14px;
            box-shadow: 0 12px 28px rgba(0,0,0,.13);
        }

        .table-card table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 7px;
        }

        .table-card th {
            text-align: left;
            color: #b1b1c1;
            font-size: .69rem;
            font-weight: 800;
            padding: 8px 5px;
            border-bottom: 1px solid rgba(255,255,255,.08);
        }

        .table-card td {
            color: #e8e8ef;
            font-size: .72rem;
            padding: 9px 5px;
            border-bottom: 1px dashed rgba(255,255,255,.06);
            vertical-align: middle;
        }

        .company-badge, .avatar-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 23px;
            height: 23px;
            border-radius: 50%;
            background: linear-gradient(145deg, #f23d9c, #8d24ff);
            color: #fff;
            font-weight: 900;
            font-size: .65rem;
            margin-right: 7px;
        }

        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 4px 7px;
            border-radius: 999px;
            border: 1px solid rgba(255,255,255,.12);
            font-size: .65rem;
            font-weight: 800;
        }

        .section-gap { height: 14px; }

        .login-wrapper {
            min-height: 84vh;
            display: grid;
            grid-template-columns: minmax(280px, 34%) 1fr;
            border-radius: 24px;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,.06);
            background: linear-gradient(135deg, #070710, #0d0a1a);
            box-shadow: 0 30px 80px rgba(0,0,0,.34);
        }

        .login-brand-panel {
            padding: 62px 54px;
            background:
                radial-gradient(circle at 25% 82%, rgba(141,36,255,.23), transparent 27%),
                linear-gradient(180deg, #070710, #0a0a13);
            position: relative;
        }

        .login-brand-panel h1 {
            margin-top: 56px;
            color: #fff;
            font-size: 2.35rem !important;
            line-height: 1.03;
        }

        .login-brand-panel p {
            color: #a2a2b1;
            font-size: 1rem;
        }

        .login-right {
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 44px;
            background:
                radial-gradient(circle at 35% 61%, rgba(246,59,155,.23), transparent 30%),
                radial-gradient(circle at 74% 35%, rgba(141,36,255,.20), transparent 33%),
                linear-gradient(135deg, #120f20, #090914);
        }

        .login-card {
            width: min(640px, 100%);
            background: #fbfbfd;
            border-radius: 22px;
            padding: 36px 38px 28px 38px;
            box-shadow: 0 24px 68px rgba(0,0,0,.32), 0 0 34px rgba(246,59,155,.18);
        }

        .login-shield {
            width: 52px;
            height: 52px;
            border-radius: 50%;
            background: rgba(141,36,255,.09);
            color: #9228f7;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 16px auto;
            font-size: 1.25rem;
        }

        .login-title {
            color: #151525;
            text-align: center;
            font-size: 1.35rem;
            font-weight: 900;
        }

        .login-sub {
            color: #77778a;
            text-align: center;
            font-size: .92rem;
            margin-top: 4px;
            margin-bottom: 19px;
        }

        .login-card label, .login-card p, .login-card span {
            color: #1a1a29 !important;
        }

        .login-card input {
            background: #ffffff !important;
            color: #151525 !important;
            border-radius: 10px !important;
        }

        .login-card .stButton > button {
            width: 100%;
            min-height: 46px;
            margin-top: 8px;
            border: 0;
            border-radius: 9px;
            color: #fff;
            font-size: .95rem;
            font-weight: 900;
            background: linear-gradient(90deg, #f33c96, #8a20f8);
            box-shadow: 0 10px 22px rgba(208,43,210,.22);
        }

        @media (max-width: 900px) {
            .login-wrapper { grid-template-columns: 1fr; }
            .login-brand-panel { display: none; }
            .login-right { padding: 22px; min-height: 88vh; }
            section[data-testid="stSidebar"] { width: 220px !important; }
            section[data-testid="stSidebar"] > div { width: 220px !important; }
        }
    </style>
    """
)


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

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


def format_integer(value) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except Exception:
        return "0"


def make_unique_headers(headers: list[str]) -> list[str]:
    result = []
    counter = {}
    for index, header in enumerate(headers):
        clean_header = normalize_text(header) or f"Coluna {index + 1}"
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
    return pd.Series([default_value] * len(df), index=df.index)


def parse_date(value) -> pd.Timestamp:
    text = normalize_text(value)
    if not text:
        return pd.NaT
    return pd.to_datetime(text, dayfirst=True, errors="coerce")


def status_group(value: str) -> str:
    status = normalize_search_text(value)
    if not status:
        return "Novo Lead"
    if any(word in status for word in ["fechado", "cliente", "ganho", "vendido", "contrato"]):
        return "Fechado"
    if any(word in status for word in ["nao responde", "não responde", "sem resposta", "nao respondeu", "não respondeu"]):
        return "Não Responde"
    if any(word in status for word in ["sem interesse", "perdido", "recusado"]):
        return "Sem Interesse"
    if any(word in status for word in ["chamando", "contato", "conversando", "negociacao", "negociação", "reuniao", "reunião", "andamento", "proposta"]):
        return "Chamando"
    if any(word in status for word in ["novo", "lead"]):
        return "Novo Lead"
    return "Novo Lead"


def status_badge(status: str) -> str:
    meta = STATUS_META.get(status, STATUS_META["Novo Lead"])
    return (
        f'<span class="status-pill {meta["class"]}">'
        f'{meta["icon"]} {html.escape(status)}'
        '</span>'
    )


def initials(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return "OP"
    parts = [part for part in re.split(r"\s+", text) if part]
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


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
    score += {
        "Fechado": 20,
        "Chamando": 12,
        "Novo Lead": 5,
        "Não Responde": 3,
        "Sem Interesse": 0,
    }.get(grouped_status, 0)
    return min(score, 100)


def score_classification(score: int) -> str:
    if score >= 70:
        return "Lead quente"
    if score >= 40:
        return "Lead morno"
    return "Lead frio"


# =========================================================
# CONEXÃO COM GOOGLE SHEETS
# =========================================================

@st.cache_resource
def get_gsheet_client():
    credentials_info = dict(st.secrets["gcp_service_account"])
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
    worksheet = (
        get_gsheet_client()
        .open_by_key(SHEET_ID)
        .worksheet(WORKSHEET_NAME)
    )
    values = worksheet.get_all_values()
    if not values:
        return pd.DataFrame()

    headers = make_unique_headers(values[0])
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    for column in df.columns:
        df[column] = df[column].astype(str).str.strip()

    return df[
        df.apply(
            lambda row: any(normalize_text(value) for value in row),
            axis=1,
        )
    ].reset_index(drop=True)


# =========================================================
# IDENTIFICAÇÃO E PREPARAÇÃO DAS COLUNAS
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
        "socio_1": first_existing_column(df, ["Sócio 1", "Socio 1"]),
        "instagram": first_existing_column(df, ["Instagram"]),
        "linkedin": first_existing_column(df, ["Linkedin", "LinkedIn"]),
        "vendedor": first_existing_column(df, ["Vendedor", "Responsável", "Responsavel"]),
        "status": first_existing_column(df, ["Status", "Etapa"]),
        "data_chamado": first_existing_column(df, ["Data do chamado", "Data chamado", "Data"]),
        "ultima_atualizacao": first_existing_column(df, ["Ultima atualização", "Última atualização", "Última atualizacao"]),
    }


def prepare_data(df: pd.DataFrame, columns: dict) -> pd.DataFrame:
    result = df.copy()
    result["_empresa"] = safe_series(result, columns.get("empresa"), "Empresa sem nome")
    result["_telefone"] = safe_series(result, columns.get("telefone_b2b"), "")
    result["_capital_num"] = safe_series(result, columns.get("capital"), "").apply(parse_money)
    result["_status"] = safe_series(result, columns.get("status"), "Novo Lead").apply(status_group)
    result["_vendedor"] = safe_series(result, columns.get("vendedor"), "Sem vendedor").replace("", "Sem vendedor")
    result["_data_chamado"] = safe_series(result, columns.get("data_chamado"), "").apply(parse_date)
    result["_ultima_atualizacao"] = safe_series(result, columns.get("ultima_atualizacao"), "").apply(parse_date)
    result["_pontuacao"] = result.apply(lambda row: calculate_score(row, columns), axis=1)
    result["_classificacao"] = result["_pontuacao"].apply(score_classification)
    return result


# =========================================================
# LOGIN
# =========================================================

def secret_value(name: str, default: str = "") -> str:
    try:
        return normalize_text(st.secrets.get(name, default))
    except Exception:
        return default


def render_logo() -> None:
    render_html('<div class="oppi-logo"></div>')


def show_login() -> None:
    if st.session_state.get("authenticated", False):
        return

    render_html('<div class="login-wrapper"><div class="login-brand-panel"><div class="oppi-logo"></div><h1>Dashboard<br><span class="gradient-title">Oppi Comercial</span></h1><p>Painel de gestão comercial</p><div class="sidebar-accent"></div><div class="sidebar-security"><span class="shield">♢</span> Segurança, performance e inteligência para impulsionar seus resultados.</div></div><div class="login-right"><div class="login-card"><div class="login-shield">♢</div><div class="login-title">Acesse o painel comercial da Oppi Tech</div><div class="login-sub">Faça login para continuar</div>')

    left, middle, right = st.columns([0.18, 1, 0.18])
    with middle:
        username = st.text_input("Usuário", placeholder="Digite seu usuário")
        password = st.text_input("Senha", type="password", placeholder="Digite sua senha")
        if st.button("Entrar", use_container_width=True):
            expected_username = secret_value("APP_USERNAME", "oppi")
            expected_password = secret_value("APP_PASSWORD", "Oppi@2026!")
            if username == expected_username and password == expected_password:
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("Usuário ou senha incorretos.")

    render_html('</div></div></div>')
    st.stop()


# =========================================================
# SIDEBAR
# =========================================================

def render_sidebar() -> str:
    with st.sidebar:
        render_html('<div class="sidebar-brand"><div class="oppi-logo"></div><h2>Dashboard<br><span class="gradient-title">Oppi Comercial</span></h2><p>Painel de gestão comercial</p><div class="sidebar-accent"></div><div class="sidebar-section-label">NAVEGAÇÃO</div></div>')

        selected_page = st.radio(
            "Menu principal",
            ["⌂  Visão Geral", "▤  Propostas", "⚖  Pesos e Medidas"],
            label_visibility="collapsed",
        )

        render_html('<div class="sidebar-security"><span class="shield">♢</span> Segurança, performance e inteligência para impulsionar seus resultados.</div>')

        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        if st.button("↻ Atualizar dados", use_container_width=True):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()

        if st.button("Sair da conta", use_container_width=True):
            st.session_state["authenticated"] = False
            st.rerun()

    return selected_page


# =========================================================
# FILTROS
# =========================================================

def render_filters(df: pd.DataFrame) -> tuple[pd.DataFrame, date, date]:
    today = date.today()
    default_start = today - timedelta(days=6)

    render_html('<div class="filter-panel">')
    col_1, col_2, col_3, col_4 = st.columns([1.05, 1.05, 1.05, 1.2])

    seller_options = sorted(df["_vendedor"].dropna().astype(str).unique().tolist())
    status_options = STATUS_ORDER

    with col_1:
        selected_sellers = st.multiselect("Vendedor", seller_options, placeholder="Todos os vendedores")
    with col_2:
        selected_statuses = st.multiselect("Status", status_options, placeholder="Todos os status")
    with col_3:
        selected_period = st.date_input(
            "Período",
            value=(default_start, today),
            format="DD/MM/YYYY",
        )
    with col_4:
        search_term = st.text_input("Buscar empresa ou telefone", placeholder="Digite para buscar...")
    render_html('</div>')

    if isinstance(selected_period, tuple) and len(selected_period) == 2:
        start_date, end_date = selected_period
    else:
        start_date = end_date = today

    filtered_df = df.copy()
    if selected_sellers:
        filtered_df = filtered_df[filtered_df["_vendedor"].isin(selected_sellers)]
    if selected_statuses:
        filtered_df = filtered_df[filtered_df["_status"].isin(selected_statuses)]
    if search_term.strip():
        needle = normalize_search_text(search_term)
        filtered_df = filtered_df[
            filtered_df.apply(
                lambda row: needle in normalize_search_text(" | ".join(row.astype(str).tolist())),
                axis=1,
            )
        ]

    return filtered_df.copy(), start_date, end_date


# =========================================================
# COMPONENTES VISUAIS
# =========================================================

def render_page_header(title: str, subtitle: str) -> None:
    now_text = datetime.now().strftime("%d/%m/%Y • %H:%M")
    render_html(
        f'<div class="page-head"><div><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div><div class="updated-pill">▣ Atualizado agora • {now_text}</div></div>'
    )


def kpi_card(icon: str, label: str, value: str, subtitle: str) -> None:
    render_html(
        f'<div class="kpi-card"><div class="kpi-icon">{icon}</div><div class="kpi-body"><div class="kpi-label">{html.escape(label)}</div><div class="kpi-value">{html.escape(value)}</div><div class="kpi-sub"><strong>▲</strong> {html.escape(subtitle)}</div></div></div>'
    )


def build_line_chart(df: pd.DataFrame, start_date: date, end_date: date) -> go.Figure:
    period_df = df[df["_data_chamado"].notna()].copy()
    if not period_df.empty:
        period_df = period_df[
            (period_df["_data_chamado"].dt.date >= start_date)
            & (period_df["_data_chamado"].dt.date <= end_date)
        ]

    dates = pd.date_range(start=start_date, end=end_date, freq="D")
    counts = period_df.groupby(period_df["_data_chamado"].dt.date).size().to_dict() if not period_df.empty else {}
    y_values = [int(counts.get(day.date(), 0)) for day in dates]
    x_labels = [day.strftime("%d/%m") for day in dates]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=y_values,
            mode="lines+markers+text",
            text=y_values,
            textposition="top center",
            line=dict(color="#f23d9c", width=4, shape="spline"),
            marker=dict(size=9, color="#ffffff", line=dict(color="#f23d9c", width=3)),
            fill="tozeroy",
            fillcolor="rgba(242,61,156,0.14)",
            hovertemplate="%{x}<br>%{y} chamado(s)<extra></extra>",
        )
    )
    fig.update_layout(
        height=285,
        margin=dict(l=12, r=12, t=20, b=5),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#b9b9c8", size=11),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.08)", zeroline=False, rangemode="tozero"),
        showlegend=False,
    )
    return fig


def render_status_summary(df: pd.DataFrame) -> None:
    total = max(len(df), 1)
    rows = []
    for status in STATUS_ORDER:
        count = int((df["_status"] == status).sum())
        percent = round((count / total) * 100)
        meta = STATUS_META[status]
        rows.append(
            f'<div class="status-row"><div class="status-icon {meta["class"]}">{meta["icon"]}</div><div class="status-name">{html.escape(status)}</div><div class="status-count">{count}</div><div class="status-percent">{percent}%</div></div>'
        )
    render_html('<div class="dark-card"><div class="card-title">Resumo por status</div>' + "".join(rows) + '</div>')


def render_recent_calls(df: pd.DataFrame, limit: int = 7) -> None:
    ordered_df = df.copy()
    ordered_df["_sort_date"] = ordered_df["_data_chamado"].fillna(pd.Timestamp("1900-01-01"))
    ordered_df = ordered_df.sort_values("_sort_date", ascending=False).head(limit)

    rows = []
    for _, row in ordered_df.iterrows():
        company = normalize_text(row.get("_empresa", "Empresa sem nome")) or "Empresa sem nome"
        phone = normalize_text(row.get("_telefone", "")) or "—"
        status = normalize_text(row.get("_status", "Novo Lead"))
        seller = normalize_text(row.get("_vendedor", "Sem vendedor")) or "Sem vendedor"
        call_date = row.get("_data_chamado")
        date_text = call_date.strftime("%d/%m/%Y • %H:%M") if pd.notna(call_date) else "—"

        rows.append(
            '<tr>'
            f'<td><span class="company-badge">{html.escape(initials(company)[:1])}</span>{html.escape(company)}</td>'
            f'<td>{html.escape(phone)}</td>'
            f'<td>{status_badge(status)}</td>'
            f'<td><span class="avatar-badge">{html.escape(initials(seller))}</span>{html.escape(seller)}</td>'
            f'<td>{html.escape(date_text)}</td>'
            '</tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="5">Nenhum chamado encontrado para os filtros selecionados.</td></tr>')

    render_html(
        '<div class="table-card"><div class="card-title">Últimos chamados</div><table><thead><tr><th>Empresa</th><th>Telefone</th><th>Status</th><th>Vendedor</th><th>Data</th></tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table></div>'
    )


# =========================================================
# PÁGINA: VISÃO GERAL
# =========================================================

def render_overview_page(df: pd.DataFrame) -> None:
    render_page_header(
        "Visão Geral",
        "Acompanhe o desempenho da operação comercial em tempo real.",
    )

    filtered_df, start_date, end_date = render_filters(df)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_count = int((filtered_df["_data_chamado"].dt.date == today).sum())
    week_count = int(
        filtered_df["_data_chamado"].notna()
        & (filtered_df["_data_chamado"].dt.date >= week_start)
        & (filtered_df["_data_chamado"].dt.date <= today)
    )
    week_count = int(week_count.sum()) if isinstance(week_count, pd.Series) else int(week_count)

    month_mask = (
        filtered_df["_data_chamado"].notna()
        & (filtered_df["_data_chamado"].dt.date >= month_start)
        & (filtered_df["_data_chamado"].dt.date <= today)
    )
    month_count = int(month_mask.sum())
    companies_contacted = int(filtered_df["_empresa"].replace("", pd.NA).dropna().nunique())

    card_1, card_2, card_3, card_4 = st.columns(4)
    with card_1:
        kpi_card("☎", "Chamados hoje", format_integer(today_count), "acompanhamento diário")
    with card_2:
        kpi_card("▣", "Chamados na semana", format_integer(week_count), "visão da semana atual")
    with card_3:
        kpi_card("▥", "Chamados no mês", format_integer(month_count), "consolidado mensal")
    with card_4:
        kpi_card("▦", "Empresas contatadas", format_integer(companies_contacted), "empresas nos filtros atuais")

    render_html('<div class="section-gap"></div>')

    chart_col, status_col = st.columns([1.72, 0.82])
    with chart_col:
        render_html('<div class="dark-card"><div class="card-title">Chamados por dia</div><div class="card-sub">Quantidade de contatos registrados no período selecionado.</div>')
        st.plotly_chart(build_line_chart(filtered_df, start_date, end_date), use_container_width=True, config={"displayModeBar": False})
        render_html('</div>')
    with status_col:
        render_status_summary(filtered_df)

    render_recent_calls(filtered_df)


# =========================================================
# PÁGINA: PROPOSTAS
# =========================================================

def render_proposals_page(df: pd.DataFrame) -> None:
    render_page_header(
        "Propostas",
        "Acompanhe empresas em negociação e oportunidades comerciais.",
    )

    filtered_df, _, _ = render_filters(df)
    proposal_df = filtered_df[filtered_df["_status"].isin(["Chamando", "Fechado"])].copy()

    total = len(proposal_df)
    in_progress = int((proposal_df["_status"] == "Chamando").sum())
    closed = int((proposal_df["_status"] == "Fechado").sum())
    total_capital = float(proposal_df["_capital_num"].sum())

    col_1, col_2, col_3, col_4 = st.columns(4)
    with col_1:
        kpi_card("▤", "Pipeline comercial", format_integer(total), "oportunidades acompanhadas")
    with col_2:
        kpi_card("☎", "Em negociação", format_integer(in_progress), "contatos em andamento")
    with col_3:
        kpi_card("✓", "Fechados", format_integer(closed), "clientes conquistados")
    with col_4:
        kpi_card("$", "Capital mapeado", format_money(total_capital), "soma das empresas filtradas")

    render_html('<div class="section-gap"></div>')
    render_recent_calls(proposal_df, limit=20)


# =========================================================
# PÁGINA: PESOS E MEDIDAS
# =========================================================

def render_scoring_page(df: pd.DataFrame) -> None:
    render_page_header(
        "Pesos e Medidas",
        "Classifique os leads conforme a qualidade do cadastro e o potencial comercial.",
    )

    filtered_df, _, _ = render_filters(df)

    hot = int((filtered_df["_classificacao"] == "Lead quente").sum())
    warm = int((filtered_df["_classificacao"] == "Lead morno").sum())
    cold = int((filtered_df["_classificacao"] == "Lead frio").sum())
    average_score = int(round(filtered_df["_pontuacao"].mean())) if not filtered_df.empty else 0

    col_1, col_2, col_3, col_4 = st.columns(4)
    with col_1:
        kpi_card("🔥", "Leads quentes", format_integer(hot), "pontuação igual ou superior a 70")
    with col_2:
        kpi_card("◐", "Leads mornos", format_integer(warm), "pontuação entre 40 e 69")
    with col_3:
        kpi_card("❄", "Leads frios", format_integer(cold), "pontuação inferior a 40")
    with col_4:
        kpi_card("⚖", "Pontuação média", format_integer(average_score), "média dos leads filtrados")

    render_html('<div class="section-gap"></div>')

    left, right = st.columns([1.12, 0.88])
    with left:
        ranking_df = filtered_df.sort_values("_pontuacao", ascending=False)[
            ["_empresa", "_telefone", "_status", "_pontuacao", "_classificacao"]
        ].copy()
        ranking_df.columns = ["Empresa", "Telefone", "Status", "Pontuação", "Classificação"]
        render_html('<div class="dark-card"><div class="card-title">Ranking de empresas</div><div class="card-sub">Leads organizados pela pontuação calculada automaticamente.</div>')
        st.dataframe(ranking_df, use_container_width=True, hide_index=True, height=470)
        render_html('</div>')

    with right:
        rules = [
            ("Telefone B2B preenchido", "15 pontos"),
            ("E-mail preenchido", "10 pontos"),
            ("Site preenchido", "10 pontos"),
            ("Instagram preenchido", "10 pontos"),
            ("LinkedIn preenchido", "5 pontos"),
            ("Sócio identificado", "10 pontos"),
            ("Capital social informado", "até 20 pontos"),
            ("Evolução comercial", "até 20 pontos"),
        ]
        rows = "".join(
            f'<div class="status-row"><div class="status-icon status-new">✦</div><div class="status-name">{html.escape(label)}</div><div class="status-count">{html.escape(points)}</div><div></div></div>'
            for label, points in rules
        )
        render_html('<div class="dark-card"><div class="card-title">Regra inicial de pontuação</div><div class="card-sub">Os pesos podem ser ajustados conforme a estratégia da Oppi.</div>' + rows + '</div>')


# =========================================================
# ERROS E EXECUÇÃO
# =========================================================

def render_connection_error(error: Exception) -> None:
    st.title("Dashboard Oppi Comercial")
    if isinstance(error, SpreadsheetNotFound):
        st.error("A credencial foi aceita, mas a planilha não foi localizada. Confira o SHEET_ID e o compartilhamento com a conta de serviço.")
        st.code(SHEET_ID)
        return
    if isinstance(error, WorksheetNotFound):
        st.error(f"A planilha foi localizada, mas a aba {WORKSHEET_NAME} não foi encontrada.")
        return
    st.error("Não consegui carregar os dados da planilha.")
    st.code(str(error))


def main() -> None:
    show_login()
    selected_page = render_sidebar()

    try:
        raw_df = load_sheet_data()
    except Exception as error:
        render_connection_error(error)
        return

    if raw_df.empty:
        st.warning("A planilha foi conectada, mas ainda não possui registros preenchidos.")
        return

    columns = identify_columns(raw_df)
    df = prepare_data(raw_df, columns)

    if selected_page == "⌂  Visão Geral":
        render_overview_page(df)
    elif selected_page == "▤  Propostas":
        render_proposals_page(df)
    else:
        render_scoring_page(df)


if __name__ == "__main__":
    main()
