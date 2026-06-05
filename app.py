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

# Logo oficial da Oppi incorporada ao arquivo para não depender de imagens externas.
LOGO_DATA_URI = "data:image/png;base64,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"


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

        .oppi-logo-image {
            display: block;
            width: 58px;
            height: 58px;
            object-fit: contain;
            filter: drop-shadow(0 0 16px rgba(220, 45, 213, .28));
        }

        .sidebar-brand .oppi-logo-image {
            margin-bottom: 18px;
        }

        div[data-testid="stHorizontalBlock"]:has(.login-left-marker) {
            min-height: 84vh;
            gap: 0 !important;
            border-radius: 24px;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,.06);
            background: linear-gradient(135deg, #070710, #0d0a1a);
            box-shadow: 0 30px 80px rgba(0,0,0,.34);
        }

        div[data-testid="stHorizontalBlock"]:has(.login-left-marker) > div[data-testid="column"]:first-child {
            padding: 58px 38px 44px 38px;
            background:
                radial-gradient(circle at 22% 87%, rgba(141,36,255,.26), transparent 28%),
                linear-gradient(180deg, #070710, #0a0a13);
        }

        div[data-testid="stHorizontalBlock"]:has(.login-right-marker) > div[data-testid="column"]:last-child {
            display: flex;
            justify-content: center;
            flex-direction: column;
            padding: 42px 7%;
            background:
                radial-gradient(circle at 36% 65%, rgba(246,59,155,.23), transparent 32%),
                radial-gradient(circle at 74% 35%, rgba(141,36,255,.22), transparent 34%),
                linear-gradient(135deg, #120f20, #090914);
        }

        .login-left-marker,
        .login-right-marker {
            display: none;
        }

        .login-brand-content h1 {
            margin-top: 48px;
            color: #fff;
            font-size: 2.30rem !important;
            line-height: 1.03;
        }

        .login-brand-content p {
            color: #a2a2b1;
            font-size: .98rem;
            margin-bottom: 0;
        }

        .login-form-title {
            margin-top: 17px;
            margin-bottom: 5px;
            color: #f8f8fb;
            font-size: .82rem;
            font-weight: 850;
            letter-spacing: .02em;
        }

        div[data-testid="stHorizontalBlock"]:has(.login-left-marker)
        div[data-testid="stTextInput"] label p {
            color: #ffffff !important;
            font-size: .73rem !important;
            font-weight: 800 !important;
        }

        div[data-testid="stHorizontalBlock"]:has(.login-left-marker)
        div[data-testid="stTextInput"] input {
            min-height: 42px;
            background: #f8f8fc !important;
            color: #151525 !important;
            border: 1px solid rgba(255,255,255,.12) !important;
            border-radius: 8px !important;
        }

        div[data-testid="stHorizontalBlock"]:has(.login-left-marker)
        div[data-testid="stTextInput"] input::placeholder {
            color: #9898a8 !important;
        }

        div[data-testid="stHorizontalBlock"]:has(.login-left-marker)
        .stButton > button {
            width: 100%;
            min-height: 44px;
            margin-top: 6px;
            border: 0;
            border-radius: 8px;
            color: #fff;
            font-size: .86rem;
            font-weight: 900;
            background: linear-gradient(90deg, #f33c96, #8a20f8);
            box-shadow: 0 10px 22px rgba(208,43,210,.22);
        }

        .login-info-card {
            width: min(760px, 100%);
            min-height: 178px;
            display: grid;
            grid-template-columns: 120px 1fr;
            align-items: center;
            gap: 20px;
            padding: 30px 42px;
            border-radius: 22px;
            background: #fbfbfd;
            box-shadow: 0 24px 68px rgba(0,0,0,.32), 0 0 34px rgba(246,59,155,.18);
        }

        .login-info-logo-wrap {
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .login-info-logo {
            width: 104px;
            height: 104px;
            object-fit: contain;
            filter: drop-shadow(0 9px 16px rgba(125, 20, 157, .16));
        }

        .login-info-text {
            position: relative;
            padding-top: 13px;
        }

        .login-info-symbol {
            width: 42px;
            height: 42px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 0 11px 0;
            border-radius: 50%;
            background: rgba(141,36,255,.09);
            color: #9228f7;
            font-size: 1rem;
        }

        .login-info-title {
            color: #151525;
            font-size: 1.30rem;
            line-height: 1.2;
            font-weight: 900;
        }

        .login-info-sub {
            color: #77778a;
            font-size: .9rem;
            margin-top: 6px;
        }

        @media (max-width: 900px) {
            div[data-testid="stHorizontalBlock"]:has(.login-left-marker) {
                display: block;
                min-height: auto;
            }

            div[data-testid="stHorizontalBlock"]:has(.login-left-marker) > div[data-testid="column"]:first-child {
                padding: 34px 24px 30px 24px;
            }

            div[data-testid="stHorizontalBlock"]:has(.login-right-marker) > div[data-testid="column"]:last-child {
                padding: 24px;
            }

            .login-brand-content h1 {
                margin-top: 28px;
                font-size: 1.95rem !important;
            }

            .login-info-card {
                grid-template-columns: 74px 1fr;
                min-height: 130px;
                gap: 13px;
                padding: 22px;
            }

            .login-info-logo {
                width: 68px;
                height: 68px;
            }

            .login-info-symbol {
                width: 34px;
                height: 34px;
                font-size: .86rem;
                margin-bottom: 8px;
            }

            .login-info-title {
                font-size: 1.02rem;
            }

            .login-info-sub {
                font-size: .78rem;
            }

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


def logo_html(css_class: str = "oppi-logo-image") -> str:
    return (
        f'<img src="{LOGO_DATA_URI}" '
        f'class="{html.escape(css_class)}" '
        'alt="Logo Oppi Tech">'
    )


def render_logo() -> None:
    render_html(logo_html())


def show_login() -> None:
    if st.session_state.get("authenticated", False):
        return

    left_panel, right_panel = st.columns([0.34, 0.66], gap=None)

    with left_panel:
        render_html(
            '<div class="login-left-marker"></div>'
            '<div class="login-brand-content">'
            + logo_html()
            + '<h1>Dashboard<br><span class="gradient-title">Oppi Comercial</span></h1>'
            '<p>Painel de gestão comercial</p>'
            '<div class="sidebar-accent"></div>'
            '<div class="login-form-title">Entre para acessar o painel</div>'
            '</div>'
        )

        username = st.text_input(
            "Usuário",
            placeholder="Digite seu usuário",
            key="login_username",
        )
        password = st.text_input(
            "Senha",
            type="password",
            placeholder="Digite sua senha",
            key="login_password",
        )

        if st.button("Entrar", use_container_width=True, key="login_button"):
            expected_username = secret_value("APP_USERNAME", "oppi")
            expected_password = secret_value("APP_PASSWORD", "Oppi@2026!")

            if username == expected_username and password == expected_password:
                st.session_state["authenticated"] = True
                st.rerun()

            st.error("Usuário ou senha incorretos.")

    with right_panel:
        render_html(
            '<div class="login-right-marker"></div>'
            '<div class="login-info-card">'
            '<div class="login-info-logo-wrap">'
            + logo_html("login-info-logo")
            + '</div>'
            '<div class="login-info-text">'
            '<div class="login-info-symbol">♢</div>'
            '<div class="login-info-title">Acesse o painel comercial da Oppi Tech</div>'
            '<div class="login-info-sub">Faça login para continuar</div>'
            '</div>'
            '</div>'
        )

    st.stop()


# =========================================================
# SIDEBAR
# =========================================================

def render_sidebar() -> str:
    with st.sidebar:
        render_html('<div class="sidebar-brand">' + logo_html() + '<h2>Dashboard<br><span class="gradient-title">Oppi Comercial</span></h2><p>Painel de gestão comercial</p><div class="sidebar-accent"></div><div class="sidebar-section-label">NAVEGAÇÃO</div></div>')

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
