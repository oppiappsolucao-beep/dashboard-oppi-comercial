import re
import unicodedata
from typing import Optional

import gspread
import pandas as pd
import plotly.express as px
import streamlit as st
from google.oauth2.service_account import Credentials
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound


# =========================================================
# CONFIGURAÇÃO PRINCIPAL
# =========================================================

st.set_page_config(
    page_title="Dashboard Oppi Comercial",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

SHEET_ID = "1GAbrca0NSIjFPXaSte1qGxXCsGkQPacoRsm0PVB51gE"
WORKSHEET_NAME = "Folha1"

CACHE_TTL_SECONDS = 120

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# =========================================================
# ESTILO VISUAL
# =========================================================

st.markdown(
    """
<style>
    .stApp {
        background: #F4F7FC;
    }

    .block-container {
        padding-top: 1.4rem;
        padding-bottom: 2rem;
        max-width: 1500px;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #061F4A 0%, #0A3471 100%);
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }

    section[data-testid="stSidebar"] * {
        color: #FFFFFF;
    }

    section[data-testid="stSidebar"] .stRadio label {
        background: transparent;
        border-radius: 12px;
        padding: 8px 9px;
        margin-bottom: 5px;
        transition: 0.2s;
    }

    section[data-testid="stSidebar"] .stRadio label:hover {
        background: rgba(255, 255, 255, 0.10);
    }

    h1, h2, h3 {
        color: #082A5C;
        letter-spacing: -0.02em;
    }

    .metric-card {
        background: #FFFFFF;
        border: 1px solid #E4EAF4;
        border-radius: 20px;
        padding: 18px;
        min-height: 132px;
        box-shadow: 0 10px 26px rgba(15, 23, 42, 0.05);
        display: flex;
        align-items: center;
        gap: 14px;
    }

    .metric-icon {
        width: 52px;
        height: 52px;
        flex-shrink: 0;
        border-radius: 16px;
        display: flex;
        justify-content: center;
        align-items: center;
        font-size: 1.45rem;
        color: #FFFFFF;
    }

    .metric-title {
        color: #64748B;
        font-size: 0.78rem;
        font-weight: 800;
        text-transform: uppercase;
        margin-bottom: 5px;
    }

    .metric-value {
        color: #082A5C;
        font-size: 1.75rem;
        font-weight: 950;
        line-height: 1;
    }

    .metric-subtitle {
        color: #7B879A;
        font-size: 0.72rem;
        margin-top: 7px;
    }

    .content-card {
        background: #FFFFFF;
        border: 1px solid #E4EAF4;
        border-radius: 20px;
        padding: 18px;
        box-shadow: 0 10px 26px rgba(15, 23, 42, 0.04);
        margin-bottom: 12px;
    }

    .content-title {
        color: #082A5C;
        font-size: 1.05rem;
        font-weight: 900;
        margin-bottom: 3px;
    }

    .content-subtitle {
        color: #748198;
        font-size: 0.80rem;
        margin-bottom: 4px;
    }

    div[data-testid="stDataFrame"] {
        border: 1px solid #E4EAF4;
        border-radius: 16px;
        overflow: hidden;
    }

    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div {
        border-radius: 12px;
    }

    .sidebar-logo-box {
        padding: 16px 6px 8px 6px;
        margin-bottom: 12px;
    }

    .sidebar-logo-icon {
        width: 58px;
        height: 58px;
        border-radius: 18px;
        background: #2F78D4;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.65rem;
        margin-bottom: 12px;
        border: 1px solid rgba(255, 255, 255, 0.18);
    }

    .sidebar-title {
        color: #FFFFFF;
        font-size: 1.05rem;
        font-weight: 950;
        line-height: 1.15;
    }

    .sidebar-subtitle {
        color: #C9D9F1;
        font-size: 0.66rem;
        letter-spacing: 0.16em;
        font-weight: 800;
        margin-top: 7px;
    }

    .sidebar-footer {
        margin-top: 34px;
        color: #BFD1EA;
        font-size: 0.69rem;
        line-height: 1.5;
    }

    .sidebar-divider {
        border: none;
        height: 1px;
        background: rgba(255, 255, 255, 0.18);
        margin: 12px 0 16px 0;
    }

    .stButton > button {
        border-radius: 999px;
        border: none;
        background: #246BD3;
        color: #FFFFFF;
        font-weight: 800;
        padding: 0.55rem 1rem;
    }

    .stButton > button:hover {
        background: #1859B4;
        color: #FFFFFF;
    }
</style>
    """,
    unsafe_allow_html=True,
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


def make_unique_headers(headers: list[str]) -> list[str]:
    """
    Evita erro quando a planilha possui colunas repetidas,
    como diferentes colunas chamadas CPF.
    """
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
    if df is None or df.empty:
        return None

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
        return "Sem status"

    if any(
        word in status
        for word in [
            "fechado",
            "cliente",
            "ganho",
            "vendido",
            "contrato",
        ]
    ):
        return "Fechado"

    if any(
        word in status
        for word in [
            "proposta",
            "orcamento",
        ]
    ):
        return "Proposta enviada"

    if any(
        word in status
        for word in [
            "negociacao",
            "reuniao",
        ]
    ):
        return "Em negociação"

    if any(
        word in status
        for word in [
            "sem interesse",
            "perdido",
            "recusado",
        ]
    ):
        return "Sem interesse"

    if any(
        word in status
        for word in [
            "sem resposta",
            "nao respondeu",
        ]
    ):
        return "Sem resposta"

    if any(
        word in status
        for word in [
            "contato",
            "chamado",
            "andamento",
        ]
    ):
        return "Em contato"

    if any(
        word in status
        for word in [
            "novo",
            "lead",
        ]
    ):
        return "Novo lead"

    return normalize_text(value)


def calculate_score(
    row: pd.Series,
    columns: dict,
) -> int:
    score = 0

    if normalize_text(
        row.get(columns.get("telefone_b2b", ""), "")
    ):
        score += 15

    if normalize_text(
        row.get(columns.get("email", ""), "")
    ):
        score += 10

    if normalize_text(
        row.get(columns.get("site", ""), "")
    ):
        score += 10

    if normalize_text(
        row.get(columns.get("instagram", ""), "")
    ):
        score += 10

    if normalize_text(
        row.get(columns.get("linkedin", ""), "")
    ):
        score += 5

    if normalize_text(
        row.get(columns.get("socio_1", ""), "")
    ):
        score += 10

    capital_value = parse_money(
        row.get(columns.get("capital", ""), "")
    )

    if capital_value >= 100000:
        score += 20

    elif capital_value >= 50000:
        score += 15

    elif capital_value > 0:
        score += 8

    grouped_status = status_group(
        row.get(columns.get("status", ""), "")
    )

    if grouped_status == "Fechado":
        score += 20

    elif grouped_status == "Proposta enviada":
        score += 18

    elif grouped_status == "Em negociação":
        score += 15

    elif grouped_status == "Em contato":
        score += 10

    elif grouped_status == "Novo lead":
        score += 5

    return min(score, 100)


def score_classification(score: int) -> str:
    if score >= 70:
        return "Lead quente"

    if score >= 40:
        return "Lead morno"

    return "Lead frio"


def metric_card(
    title: str,
    value: str,
    subtitle: str,
    emoji: str,
    color: str,
):
    st.markdown(
        f"""
<div class="metric-card">
    <div class="metric-icon" style="background:{color};">
        {emoji}
    </div>

    <div>
        <div class="metric-title">{title}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-subtitle">{subtitle}</div>
    </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def section_header(
    title: str,
    subtitle: str,
):
    st.markdown(
        f"""
<div class="content-card">
    <div class="content-title">{title}</div>
    <div class="content-subtitle">{subtitle}</div>
</div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# CONEXÃO COM GOOGLE SHEETS
# =========================================================

@st.cache_resource
def get_gsheet_client():
    """
    Lê os Secrets do Streamlit e corrige automaticamente
    quebras de linha da chave privada.
    """
    try:
        credentials_info = dict(
            st.secrets["gcp_service_account"]
        )

    except Exception as error:
        raise RuntimeError(
            "Não encontrei a seção [gcp_service_account] "
            "nos Secrets do Streamlit."
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
        if not normalize_text(
            credentials_info.get(field, "")
        )
    ]

    if missing_fields:
        raise RuntimeError(
            "Estão faltando campos nos Secrets: "
            + ", ".join(missing_fields)
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


@st.cache_data(
    show_spinner=False,
    ttl=CACHE_TTL_SECONDS,
)
def load_sheet_data() -> pd.DataFrame:
    client = get_gsheet_client()

    spreadsheet = client.open_by_key(
        SHEET_ID
    )

    worksheet = spreadsheet.worksheet(
        WORKSHEET_NAME
    )

    values = worksheet.get_all_values()

    if not values:
        return pd.DataFrame()

    headers = make_unique_headers(
        values[0]
    )

    rows = values[1:]

    df = pd.DataFrame(
        rows,
        columns=headers,
    )

    for column in df.columns:
        df[column] = (
            df[column]
            .astype(str)
            .str.strip()
        )

    df = df[
        df.apply(
            lambda row: any(
                normalize_text(value)
                for value in row
            ),
            axis=1,
        )
    ].copy()

    return df.reset_index(
        drop=True
    )


# =========================================================
# IDENTIFICAÇÃO DAS COLUNAS
# =========================================================

def identify_columns(
    df: pd.DataFrame,
) -> dict:
    return {
        "empresa": first_existing_column(
            df,
            [
                "Nome da empresa",
                "Empresa",
                "Nome Empresa",
            ],
        ),

        "data_abertura": first_existing_column(
            df,
            [
                "Data de abertura",
                "Data abertura",
            ],
        ),

        "capital": first_existing_column(
            df,
            [
                "Capital",
                "Capital social",
            ],
        ),

        "cnpj": first_existing_column(
            df,
            [
                "CNPJ",
            ],
        ),

        "endereco": first_existing_column(
            df,
            [
                "Endereço",
                "Endereco",
            ],
        ),

        "email": first_existing_column(
            df,
            [
                "Email",
                "E-mail",
            ],
        ),

        "site": first_existing_column(
            df,
            [
                "Site empresa",
                "Site",
                "Website",
            ],
        ),

        "telefone_b2b": first_existing_column(
            df,
            [
                "Telefone (b2b)",
                "Telefone b2b",
                "Telefone",
            ],
        ),

        "telefone_fixo": first_existing_column(
            df,
            [
                "Telefone fixo",
                "Fixo",
            ],
        ),

        "telefone_alternativo": first_existing_column(
            df,
            [
                "Telefone lemitt",
                "Telefone alternativo",
                "Outro telefone",
            ],
        ),

        "socio_1": first_existing_column(
            df,
            [
                "Sócio 1",
                "Socio 1",
            ],
        ),

        "socio_2": first_existing_column(
            df,
            [
                "Sócio 2",
                "Socio 2",
            ],
        ),

        "socio_3": first_existing_column(
            df,
            [
                "Sócio 3",
                "Socio 3",
            ],
        ),

        "instagram": first_existing_column(
            df,
            [
                "Instagram",
            ],
        ),

        "linkedin": first_existing_column(
            df,
            [
                "Linkedin",
                "LinkedIn",
            ],
        ),

        "vendedor": first_existing_column(
            df,
            [
                "Vendedor",
                "Responsável",
                "Responsavel",
            ],
        ),

        "status": first_existing_column(
            df,
            [
                "Status",
                "Etapa",
            ],
        ),

        "data_chamado": first_existing_column(
            df,
            [
                "Data do chamado",
                "Data chamado",
            ],
        ),

        "ultima_atualizacao": first_existing_column(
            df,
            [
                "Ultima atualização",
                "Última atualização",
                "Última atualizacao",
            ],
        ),
    }


def prepare_data(
    df: pd.DataFrame,
    columns: dict,
) -> pd.DataFrame:
    df_result = df.copy()

    empresa_col = columns.get(
        "empresa"
    )

    capital_col = columns.get(
        "capital"
    )

    status_col = columns.get(
        "status"
    )

    vendedor_col = columns.get(
        "vendedor"
    )

    df_result["_empresa"] = safe_series(
        df_result,
        empresa_col,
    )

    df_result["_capital_num"] = safe_series(
        df_result,
        capital_col,
    ).apply(
        parse_money
    )

    df_result["_status_original"] = safe_series(
        df_result,
        status_col,
    )

    df_result["_status_grupo"] = (
        df_result["_status_original"]
        .apply(
            status_group
        )
    )

    df_result["_vendedor"] = (
        safe_series(
            df_result,
            vendedor_col,
        )
        .replace(
            "",
            "Sem vendedor",
        )
    )

    df_result["_pontuacao"] = df_result.apply(
        lambda row: calculate_score(
            row,
            columns,
        ),
        axis=1,
    )

    df_result["_classificacao"] = (
        df_result["_pontuacao"]
        .apply(
            score_classification
        )
    )

    return df_result


# =========================================================
# MENU LATERAL
# =========================================================

def render_sidebar() -> str:
    with st.sidebar:
        st.markdown(
            """
<div class="sidebar-logo-box">
    <div class="sidebar-logo-icon">
        📊
    </div>

    <div class="sidebar-title">
        DASHBOARD OPPI
    </div>

    <div class="sidebar-subtitle">
        GESTÃO COMERCIAL
    </div>
</div>

<hr class="sidebar-divider">
            """,
            unsafe_allow_html=True,
        )

        selected_page = st.radio(
            "Menu principal",
            [
                "📌 Visão Geral",
                "📄 Propostas",
                "⚖️ Pesos e Medidas",
            ],
            label_visibility="collapsed",
        )

        st.markdown(
            """
<div class="sidebar-footer">
    Oppi Tech<br>
    Dashboard comercial
</div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            "<div style='height:18px'></div>",
            unsafe_allow_html=True,
        )

        if st.button(
            "🔄 Atualizar dados",
            use_container_width=True,
        ):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()

    return selected_page


# =========================================================
# FILTROS
# =========================================================

def render_filters(
    df: pd.DataFrame,
) -> pd.DataFrame:
    filter_col_1, filter_col_2, filter_col_3 = st.columns(
        [
            1.1,
            1.1,
            1.8,
        ]
    )

    seller_options = sorted(
        [
            seller
            for seller in (
                df["_vendedor"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )
            if normalize_text(
                seller
            )
        ]
    )

    status_options = sorted(
        [
            status
            for status in (
                df["_status_grupo"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )
            if normalize_text(
                status
            )
        ]
    )

    with filter_col_1:
        selected_sellers = st.multiselect(
            "Vendedor",
            seller_options,
            placeholder="Todos os vendedores",
        )

    with filter_col_2:
        selected_statuses = st.multiselect(
            "Status",
            status_options,
            placeholder="Todos os status",
        )

    with filter_col_3:
        search_term = st.text_input(
            "Pesquisar empresa",
            placeholder=(
                "Digite o nome da empresa, "
                "CNPJ ou telefone..."
            ),
        )

    filtered_df = df.copy()

    if selected_sellers:
        filtered_df = filtered_df[
            filtered_df["_vendedor"]
            .isin(
                selected_sellers
            )
        ].copy()

    if selected_statuses:
        filtered_df = filtered_df[
            filtered_df["_status_grupo"]
            .isin(
                selected_statuses
            )
        ].copy()

    if normalize_text(
        search_term
    ):
        normalized_term = normalize_search_text(
            search_term
        )

        search_mask = filtered_df.apply(
            lambda row: normalized_term
            in normalize_search_text(
                " | ".join(
                    row.astype(str)
                    .tolist()
                )
            ),
            axis=1,
        )

        filtered_df = filtered_df[
            search_mask
        ].copy()

    return filtered_df


# =========================================================
# PÁGINA: VISÃO GERAL
# =========================================================

def render_overview_page(
    df: pd.DataFrame,
    columns: dict,
):
    st.title(
        "Visão Geral"
    )

    st.caption(
        "Acompanhe os principais indicadores "
        "da operação comercial da Oppi."
    )

    filtered_df = render_filters(
        df
    )

    total_companies = len(
        filtered_df
    )

    new_leads = int(
        (
            filtered_df["_status_grupo"]
            == "Novo lead"
        ).sum()
    )

    in_progress = int(
        filtered_df["_status_grupo"]
        .isin(
            [
                "Em contato",
                "Em negociação",
            ]
        )
        .sum()
    )

    sent_proposals = int(
        (
            filtered_df["_status_grupo"]
            == "Proposta enviada"
        ).sum()
    )

    closed_clients = int(
        (
            filtered_df["_status_grupo"]
            == "Fechado"
        ).sum()
    )

    total_capital = float(
        filtered_df["_capital_num"]
        .sum()
    )

    card_col_1, card_col_2, card_col_3 = st.columns(
        3
    )

    with card_col_1:
        metric_card(
            "Empresas cadastradas",
            str(
                total_companies
            ),
            "Total conforme os filtros selecionados",
            "🏢",
            "#246BD3",
        )

    with card_col_2:
        metric_card(
            "Novos leads",
            str(
                new_leads
            ),
            "Empresas ainda no começo do atendimento",
            "✨",
            "#7C3AED",
        )

    with card_col_3:
        metric_card(
            "Em andamento",
            str(
                in_progress
            ),
            "Leads em contato ou negociação",
            "📞",
            "#0F9F81",
        )

    st.markdown(
        "<div style='height:12px'></div>",
        unsafe_allow_html=True,
    )

    card_col_4, card_col_5, card_col_6 = st.columns(
        3
    )

    with card_col_4:
        metric_card(
            "Propostas enviadas",
            str(
                sent_proposals
            ),
            "Empresas que chegaram até a proposta",
            "📄",
            "#E99124",
        )

    with card_col_5:
        metric_card(
            "Clientes fechados",
            str(
                closed_clients
            ),
            "Negociações concluídas com sucesso",
            "✅",
            "#16A34A",
        )

    with card_col_6:
        metric_card(
            "Capital mapeado",
            format_money(
                total_capital
            ),
            "Soma do capital social das empresas filtradas",
            "💰",
            "#C23B6D",
        )

    st.markdown(
        "<div style='height:18px'></div>",
        unsafe_allow_html=True,
    )

    chart_col_1, chart_col_2 = st.columns(
        2
    )

    with chart_col_1:
        section_header(
            "📊 Leads por status",
            "Distribuição atual das empresas dentro do fluxo comercial.",
        )

        status_chart_df = (
            filtered_df["_status_grupo"]
            .value_counts(
                dropna=False
            )
            .reset_index()
        )

        status_chart_df.columns = [
            "Status",
            "Quantidade",
        ]

        fig_status = px.bar(
            status_chart_df,
            x="Status",
            y="Quantidade",
            text="Quantidade",
        )

        fig_status.update_layout(
            height=330,
            margin=dict(
                l=20,
                r=20,
                t=20,
                b=20,
            ),
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            showlegend=False,
            xaxis_title="",
            yaxis_title="Quantidade",
        )

        fig_status.update_traces(
            marker_color="#246BD3",
            textposition="outside",
        )

        st.plotly_chart(
            fig_status,
            use_container_width=True,
        )

    with chart_col_2:
        section_header(
            "👤 Empresas por vendedor",
            "Quantidade de empresas atribuídas para cada responsável.",
        )

        seller_chart_df = (
            filtered_df["_vendedor"]
            .value_counts(
                dropna=False
            )
            .reset_index()
        )

        seller_chart_df.columns = [
            "Vendedor",
            "Quantidade",
        ]

        fig_seller = px.bar(
            seller_chart_df,
            x="Vendedor",
            y="Quantidade",
            text="Quantidade",
        )

        fig_seller.update_layout(
            height=330,
            margin=dict(
                l=20,
                r=20,
                t=20,
                b=20,
            ),
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            showlegend=False,
            xaxis_title="",
            yaxis_title="Quantidade",
        )

        fig_seller.update_traces(
            marker_color="#7C3AED",
            textposition="outside",
        )

        st.plotly_chart(
            fig_seller,
            use_container_width=True,
        )

    section_header(
        "📋 Empresas cadastradas",
        "Consulte os dados principais da base comercial.",
    )

    display_columns = [
        columns.get(
            "empresa"
        ),
        columns.get(
            "cnpj"
        ),
        columns.get(
            "capital"
        ),
        columns.get(
            "telefone_b2b"
        ),
        columns.get(
            "email"
        ),
        columns.get(
            "instagram"
        ),
        columns.get(
            "vendedor"
        ),
        columns.get(
            "status"
        ),
        columns.get(
            "ultima_atualizacao"
        ),
    ]

    display_columns = [
        column
        for column in display_columns
        if column
        and column in filtered_df.columns
    ]

    if display_columns:
        st.dataframe(
            filtered_df[
                display_columns
            ],
            use_container_width=True,
            hide_index=True,
            height=430,
        )

    else:
        st.info(
            "Não encontrei colunas compatíveis para exibir a tabela."
        )


# =========================================================
# PÁGINA: PROPOSTAS
# =========================================================

def render_proposals_page(
    df: pd.DataFrame,
    columns: dict,
):
    st.title(
        "Propostas"
    )

    st.caption(
        "Acompanhe os leads que avançaram no processo comercial."
    )

    filtered_df = render_filters(
        df
    )

    proposals_df = filtered_df[
        filtered_df["_status_grupo"]
        .isin(
            [
                "Proposta enviada",
                "Em negociação",
                "Fechado",
            ]
        )
    ].copy()

    total_proposals = len(
        proposals_df
    )

    sent_proposals = int(
        (
            proposals_df["_status_grupo"]
            == "Proposta enviada"
        ).sum()
    )

    in_negotiation = int(
        (
            proposals_df["_status_grupo"]
            == "Em negociação"
        ).sum()
    )

    closed_clients = int(
        (
            proposals_df["_status_grupo"]
            == "Fechado"
        ).sum()
    )

    card_col_1, card_col_2, card_col_3, card_col_4 = st.columns(
        4
    )

    with card_col_1:
        metric_card(
            "Pipeline de propostas",
            str(
                total_proposals
            ),
            "Leads considerados nesta página",
            "📂",
            "#246BD3",
        )

    with card_col_2:
        metric_card(
            "Propostas enviadas",
            str(
                sent_proposals
            ),
            "Aguardando retorno comercial",
            "📄",
            "#E99124",
        )

    with card_col_3:
        metric_card(
            "Em negociação",
            str(
                in_negotiation
            ),
            "Leads em fase avançada",
            "🤝",
            "#7C3AED",
        )

    with card_col_4:
        metric_card(
            "Fechados",
            str(
                closed_clients
            ),
            "Clientes conquistados",
            "✅",
            "#16A34A",
        )

    st.markdown(
        "<div style='height:18px'></div>",
        unsafe_allow_html=True,
    )

    section_header(
        "📄 Controle de propostas",
        "A tabela exibe somente empresas com evolução comercial registrada.",
    )

    if proposals_df.empty:
        st.info(
            "Ainda não existem empresas com status de proposta enviada, "
            "em negociação ou fechado."
        )

        return

    display_columns = [
        columns.get(
            "empresa"
        ),
        columns.get(
            "cnpj"
        ),
        columns.get(
            "telefone_b2b"
        ),
        columns.get(
            "telefone_fixo"
        ),
        columns.get(
            "telefone_alternativo"
        ),
        columns.get(
            "email"
        ),
        columns.get(
            "instagram"
        ),
        columns.get(
            "linkedin"
        ),
        columns.get(
            "vendedor"
        ),
        columns.get(
            "status"
        ),
        columns.get(
            "data_chamado"
        ),
        columns.get(
            "ultima_atualizacao"
        ),
    ]

    display_columns = [
        column
        for column in display_columns
        if column
        and column in proposals_df.columns
    ]

    st.dataframe(
        proposals_df[
            display_columns
        ],
        use_container_width=True,
        hide_index=True,
        height=600,
    )


# =========================================================
# PÁGINA: PESOS E MEDIDAS
# =========================================================

def render_scoring_page(
    df: pd.DataFrame,
    columns: dict,
):
    st.title(
        "Pesos e Medidas"
    )

    st.caption(
        "Classificação inicial dos leads conforme a qualidade "
        "do cadastro e o avanço comercial."
    )

    filtered_df = render_filters(
        df
    )

    hot_leads = int(
        (
            filtered_df["_classificacao"]
            == "Lead quente"
        ).sum()
    )

    warm_leads = int(
        (
            filtered_df["_classificacao"]
            == "Lead morno"
        ).sum()
    )

    cold_leads = int(
        (
            filtered_df["_classificacao"]
            == "Lead frio"
        ).sum()
    )

    if filtered_df.empty:
        average_score = 0

    else:
        average_score = int(
            round(
                filtered_df["_pontuacao"]
                .mean()
            )
        )

    card_col_1, card_col_2, card_col_3, card_col_4 = st.columns(
        4
    )

    with card_col_1:
        metric_card(
            "Leads quentes",
            str(
                hot_leads
            ),
            "Pontuação igual ou superior a 70",
            "🔥",
            "#D94A4A",
        )

    with card_col_2:
        metric_card(
            "Leads mornos",
            str(
                warm_leads
            ),
            "Pontuação entre 40 e 69",
            "🌤️",
            "#E99124",
        )

    with card_col_3:
        metric_card(
            "Leads frios",
            str(
                cold_leads
            ),
            "Pontuação inferior a 40",
            "❄️",
            "#246BD3",
        )

    with card_col_4:
        metric_card(
            "Pontuação média",
            str(
                average_score
            ),
            "Média dos leads filtrados",
            "⚖️",
            "#7C3AED",
        )

    st.markdown(
        "<div style='height:18px'></div>",
        unsafe_allow_html=True,
    )

    chart_col_1, chart_col_2 = st.columns(
        [
            1,
            1.2,
        ]
    )

    with chart_col_1:
        section_header(
            "🎯 Distribuição das classificações",
            "Quantidade de empresas em cada grupo.",
        )

        class_chart_df = (
            filtered_df["_classificacao"]
            .value_counts()
            .reset_index()
        )

        class_chart_df.columns = [
            "Classificação",
            "Quantidade",
        ]

        fig_classification = px.pie(
            class_chart_df,
            names="Classificação",
            values="Quantidade",
            hole=0.55,
        )

        fig_classification.update_layout(
            height=340,
            margin=dict(
                l=20,
                r=20,
                t=20,
                b=20,
            ),
            paper_bgcolor="#FFFFFF",
        )

        st.plotly_chart(
            fig_classification,
            use_container_width=True,
        )

    with chart_col_2:
        section_header(
            "📏 Regra inicial de pontuação",
            "Os pesos podem ser alterados conforme a estratégia da Oppi.",
        )

        rule_df = pd.DataFrame(
            [
                [
                    "Telefone B2B preenchido",
                    "15 pontos",
                ],
                [
                    "E-mail preenchido",
                    "10 pontos",
                ],
                [
                    "Site preenchido",
                    "10 pontos",
                ],
                [
                    "Instagram preenchido",
                    "10 pontos",
                ],
                [
                    "LinkedIn preenchido",
                    "5 pontos",
                ],
                [
                    "Sócio identificado",
                    "10 pontos",
                ],
                [
                    "Capital social informado",
                    "Até 20 pontos",
                ],
                [
                    "Evolução comercial",
                    "Até 20 pontos",
                ],
            ],
            columns=[
                "Critério",
                "Peso",
            ],
        )

        st.dataframe(
            rule_df,
            use_container_width=True,
            hide_index=True,
            height=340,
        )

    section_header(
        "🏢 Ranking de empresas",
        "Ordenação dos leads pela pontuação calculada automaticamente.",
    )

    ranking_df = filtered_df.sort_values(
        by="_pontuacao",
        ascending=False,
    ).copy()

    ranking_columns = [
        columns.get(
            "empresa"
        ),
        columns.get(
            "cnpj"
        ),
        columns.get(
            "capital"
        ),
        columns.get(
            "telefone_b2b"
        ),
        columns.get(
            "email"
        ),
        columns.get(
            "instagram"
        ),
        columns.get(
            "vendedor"
        ),
        columns.get(
            "status"
        ),
        "_pontuacao",
        "_classificacao",
    ]

    ranking_columns = [
        column
        for column in ranking_columns
        if column
        and column in ranking_df.columns
    ]

    ranking_df = ranking_df[
        ranking_columns
    ].rename(
        columns={
            "_pontuacao": "Pontuação",
            "_classificacao": "Classificação",
        }
    )

    st.dataframe(
        ranking_df,
        use_container_width=True,
        hide_index=True,
        height=540,
    )


# =========================================================
# APLICAÇÃO PRINCIPAL
# =========================================================

def render_connection_error(
    error: Exception,
):
    st.title(
        "Dashboard Oppi Comercial"
    )

    if isinstance(
        error,
        SpreadsheetNotFound,
    ):
        st.error(
            "A credencial foi aceita, mas a planilha não foi localizada. "
            "Confirme se o SHEET_ID está correto e se a planilha foi "
            "compartilhada diretamente com o e-mail da conta de serviço."
        )

        st.markdown(
            f"""
### Confira estes dados

**ID esperado da planilha:**

```text
{SHEET_ID}
