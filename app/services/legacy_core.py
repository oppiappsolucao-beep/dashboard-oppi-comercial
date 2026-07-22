"""Lógica de negócio do dashboard comercial."""
import base64
import html
import io
import json
import os
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound

from app.config import settings
from cachetools import TTLCache

_sheet_cache: TTLCache = TTLCache(maxsize=1, ttl=max(60, settings.cache_ttl_seconds))
_sheet_last_good = None
_sheet_last_good_values: list[list[str]] | None = None
_gsheet_client = None


def invalidate_sheet_cache() -> None:
    """Invalida o cache curto. Mantém a última cópia boa para fallback em 429."""
    global _gsheet_client
    _sheet_cache.clear()
    _gsheet_client = None
    try:
        from app.services.sheet_read_cache import invalidate_worksheet_cache

        invalidate_worksheet_cache()
    except Exception:
        pass


def get_last_good_sheet_data():
    """Última leitura bem-sucedida da planilha (pode estar desatualizada)."""
    global _sheet_last_good
    if _sheet_last_good is None:
        return None
    return _sheet_last_good.copy()


def get_last_good_sheet_values() -> list[list[str]] | None:
    """Valores brutos da última leitura bem-sucedida (cabeçalho + linhas)."""
    global _sheet_last_good_values
    if not _sheet_last_good_values:
        return None
    return [row[:] for row in _sheet_last_good_values]



SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Status usados no dashboard já organizados para a nova estrutura da planilha.
# A coluna T da planilha é o Status WhatsApp e a coluna V é o Status Ligação.
# Cada coluna tem sua própria lista, igual aos filtros configurados no Google Sheets.
STATUS_WHATSAPP_OPTIONS = [
    "Novo Lead",
    "Chamado Whats",
    "Conversando",
    "Reunião",
    "Proposta",
    "Sem interesse",
    "Fechado",
    "Sem Resposta",
    "Sem Whatsapp",
    "Retornar",
]

STATUS_LIGACAO_OPTIONS = [
    "Ligação - Conversando Whats",
    "Ligação não atende/cx",
    "Ligação Numero errado",
    "Ligação retornar",
    "Proposta",
    "Reunião",
    "Sem interesse",
]

STATUS_OPTIONS = list(dict.fromkeys(STATUS_WHATSAPP_OPTIONS + STATUS_LIGACAO_OPTIONS))
STATUS_WHATSAPP_SELECT_OPTIONS = ["Sem status"] + STATUS_WHATSAPP_OPTIONS
STATUS_LIGACAO_SELECT_OPTIONS = ["Sem status"] + STATUS_LIGACAO_OPTIONS
STATUS_SELECT_OPTIONS = ["Sem status"] + STATUS_OPTIONS

# Cards da Visão Geral seguindo exatamente os status do filtro único.
# Não agrupa os status de ligação e não soma categorias diferentes.
DASHBOARD_STATUS_OPTIONS = [
    "Novo Lead",
    "Chamado Whats",
    "Conversando",
    "Reunião",
    "Proposta",
    "Sem interesse",
    "Fechado",
    "Sem Resposta",
    "Sem Whatsapp",
    "Retornar",
    "Ligação - Conversando Whats",
    "Ligação não atende/cx",
    "Ligação Numero errado",
    "Ligação retornar",
]

STATUS_COLORS = {
    "Novo Lead": ("#E8F0FF", "#5C8BFF"),
    "Chamado Whats": ("#E8FFF0", "#00C853"),
    "Conversando": ("#F8EFE6", "#B37A2A"),
    "Sem interesse": ("#E9F8FA", "#2F9FB3"),
    "Não responde": ("#FBECEF", "#DA5C78"),
    "Sem Resposta": ("#FBECEF", "#DA5C78"),
    "Fechado": ("#EAF8EF", "#58B97A"),
    "Proposta": ("#EAF2FF", "#5C9DFF"),
    "Reunião": ("#F3EAFE", "#A65BDB"),
    "Ligação": ("#EAF8FF", "#3C92A8"),
    "Ligação - Conversando Whats": ("#E8FFF0", "#3C92A8"),
    "Ligação não atende/cx": ("#EAF8FF", "#1F6B7A"),
    "Ligação Numero errado": ("#FFE8E8", "#C40000"),
    "Ligação retornar": ("#EAF2FF", "#2F6BBA"),
    "Retornar": ("#EAF2FF", "#2F6BBA"),
    "Sem Whatsapp": ("#FFF3E6", "#8B4A00"),
}



# =========================================================
# UTILITÁRIOS
# =========================================================
def coerce_scalar(value):
    """Converte Series/DataFrame do pandas em valor escalar."""
    if isinstance(value, pd.Series):
        if value.empty:
            return None
        cleaned = value.dropna()
        if cleaned.empty:
            return None
        return cleaned.iloc[0]
    if isinstance(value, pd.DataFrame):
        if value.empty:
            return None
        return value.iloc[0, 0]
    return value


def normalize_text(value) -> str:
    value = coerce_scalar(value)
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def deal_value_from_row(row) -> float:
    """Valor comercial em negociação — usa valor da proposta, nunca capital social."""
    if row is None:
        return 0.0
    try:
        value = coerce_scalar(row.get("_valor_proposta_num", 0))
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_search_text(value) -> str:
    text = normalize_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", text).strip()


def flexible_search_match(search_value, target_value) -> bool:
    """
    Busca por empresa/telefone sem trazer empresas erradas.
    - Se digitar uma frase, precisa encontrar a frase inteira ou todos os termos.
    - Se digitar uma palavra só, encontra por essa palavra.
    - Se digitar telefone/CNPJ, compara pelos números.
    """
    term = normalize_search_text(search_value)
    target = normalize_search_text(target_value)

    if not term:
        return True

    if not target:
        return False

    if term in target:
        return True

    term_digits = normalize_digits(term)
    target_digits = normalize_digits(target)

    if term_digits and term_digits in target_digits:
        return True

    tokens = [
        token
        for token in re.split(r"\s+", term)
        if len(token) >= 3
    ]

    if not tokens:
        return False

    # Uma palavra só: pode encontrar por essa palavra.
    if len(tokens) == 1:
        return tokens[0] in target

    # Mais de uma palavra: precisa bater todos os termos digitados.
    # Isso evita que "Marmoraria Topazio" traga qualquer outra marmoraria.
    return all(token in target for token in tokens)


def infer_niche_from_company_name(value) -> str:
    """Identifica automaticamente o nicho usando palavras presentes no nome da empresa."""
    company_name = normalize_search_text(value)

    if not company_name:
        return "Não identificado"

    niche_keywords = [
        ("Marmoraria", [
            "marmoraria", "marmore", "marmores", "granito", "granitos",
            "pedra", "pedras", "revestimento", "revestimentos", "travertino",
        ]),
        ("Marcenaria", [
            "marcenaria", "marceneiro", "moveis", "movel", "planejados",
            "planejado", "armarios", "armario",
        ]),
        ("Academia", [
            "academia", "fitness", "gym", "crossfit", "jiu jitsu", "muay thai",
        ]),
        ("Clínica", [
            "clinica", "consultorio", "odontologia", "odontologica", "dental",
            "saude", "estetica",
        ]),
        ("Pet shop", [
            "pet shop", "petshop", "pet", "veterinaria", "veterinario",
        ]),
        ("Construção civil", [
            "construtora", "construcao", "engenharia", "arquitetura", "obra",
        ]),
        ("Restaurante", [
            "restaurante", "pizzaria", "lanchonete", "hamburgueria", "bar", "cafe",
        ]),
        ("Loja", [
            "loja", "comercio", "varejo", "store",
        ]),
        ("Serviços", [
            "servicos", "servico", "solucoes", "consultoria",
        ]),
    ]

    for niche_name, keywords in niche_keywords:
        if any(keyword in company_name for keyword in keywords):
            return niche_name

    return "Outros"


def infer_state_from_address(value) -> str:
    """Extrai a UF do endereço. Reconhece siglas e nomes completos dos estados brasileiros."""
    address_original = normalize_text(value)

    if not address_original:
        return "Não identificado"

    valid_ufs = [
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
        "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
        "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    ]

    upper_address = address_original.upper()
    uf_matches = re.findall(r"(?<![A-Z])(" + "|".join(valid_ufs) + r")(?![A-Z])", upper_address)

    if uf_matches:
        return uf_matches[-1]

    normalized_address = normalize_search_text(address_original)
    state_names = {
        "acre": "AC",
        "alagoas": "AL",
        "amapa": "AP",
        "amazonas": "AM",
        "bahia": "BA",
        "ceara": "CE",
        "distrito federal": "DF",
        "espirito santo": "ES",
        "goias": "GO",
        "maranhao": "MA",
        "mato grosso do sul": "MS",
        "mato grosso": "MT",
        "minas gerais": "MG",
        "para": "PA",
        "paraiba": "PB",
        "parana": "PR",
        "pernambuco": "PE",
        "piaui": "PI",
        "rio de janeiro": "RJ",
        "rio grande do norte": "RN",
        "rio grande do sul": "RS",
        "rondonia": "RO",
        "roraima": "RR",
        "santa catarina": "SC",
        "sao paulo": "SP",
        "sergipe": "SE",
        "tocantins": "TO",
    }

    for state_name, uf in sorted(state_names.items(), key=lambda item: len(item[0]), reverse=True):
        if state_name in normalized_address:
            return uf

    return "Não identificado"


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

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed) and text.isdigit() and len(text) == 8:
        parsed = pd.to_datetime(text, errors="coerce", format="%d%m%Y")
    if pd.isna(parsed) and text.isdigit() and len(text) in (6, 7):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)

    return parsed


def as_datetime_series(series: pd.Series) -> pd.Series:
    """Normaliza uma coluna para datetime, tolerando object/Timestamp/NaT misturados."""
    if series is None:
        return pd.Series(dtype="datetime64[ns]")

    normalized = pd.to_datetime(series, errors="coerce", dayfirst=True)
    return normalized


def as_python_date(value) -> date | None:
    """Converte valores da planilha para date, retornando None quando inválido/NaT."""
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def as_python_datetime(value) -> datetime | None:
    """Converte valores da planilha para datetime, retornando None quando inválido/NaT."""
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, datetime):
        return value

    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()
    except Exception:
        return None


def normalize_period_filter(value):
    """
    Normaliza o filtro de período recebido do formulário.
    Quando o usuário escolhe apenas um dia, pode vir um único date em vez de
    uma tupla. Nesse caso, filtramos exatamente aquele dia.
    """
    if isinstance(value, (tuple, list)):
        cleaned_dates = [item for item in value if item is not None]

        if len(cleaned_dates) >= 2:
            start_date = cleaned_dates[0]
            end_date = cleaned_dates[1]
        elif len(cleaned_dates) == 1:
            start_date = cleaned_dates[0]
            end_date = cleaned_dates[0]
        else:
            return None, None
    elif isinstance(value, date):
        start_date = value
        end_date = value
    else:
        return None, None

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    return start_date, end_date


def apply_period_filter(df: pd.DataFrame, date_column: str, period_value) -> pd.DataFrame:
    """
    Aplica o filtro por período respeitando a coluna de referência.
    Linhas sem data e cadastros locais pendentes permanecem visíveis.
    """
    start_date, end_date = normalize_period_filter(period_value)

    if start_date is None or end_date is None or date_column not in df.columns:
        return df.copy()

    dates = as_datetime_series(df[date_column])
    valid_dates = dates.notna()
    dated_rows = df[
        valid_dates
        & (dates.dt.date >= start_date)
        & (dates.dt.date <= end_date)
    ].copy()
    undated_rows = df[~valid_dates].copy()
    pending_rows = pd.DataFrame()
    if "_pending_local" in df.columns:
        pending_mask = df["_pending_local"].fillna(False).astype(bool)
        pending_rows = df[pending_mask].copy()

    parts = [part for part in (dated_rows, undated_rows, pending_rows) if not part.empty]
    if not parts:
        return df.iloc[0:0].copy()
    combined = pd.concat(parts, ignore_index=True)
    if "_sheet_row" in combined.columns:
        combined = combined.drop_duplicates(subset=["_sheet_row"], keep="first")
    return combined.reset_index(drop=True)


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



def existing_column_by_occurrence(
    df: pd.DataFrame,
    possible_names: list[str],
    occurrence: int = 1,
) -> Optional[str]:
    """
    Encontra uma coluna considerando cabeçalhos repetidos da planilha.
    Exemplo: se a planilha tiver várias colunas chamadas "Telefone",
    o pandas recebe "Telefone", "Telefone_2", "Telefone_3".
    """
    normalized_aliases = {normalize_search_text(name) for name in possible_names}
    found = 0

    for column in df.columns:
        normalized_column = normalize_search_text(column)
        normalized_column_base = re.sub(r"_\d+$", "", normalized_column)

        if normalized_column in normalized_aliases or normalized_column_base in normalized_aliases:
            found += 1

            if found == occurrence:
                return column

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
    """Agrupa os status da nova planilha nos cards principais do dashboard."""
    status = normalize_search_text(value)

    if not status:
        return "Novo Lead"

    if any(word in status for word in ["chamado whats", "chamado whatsapp", "chamando whats", "chamando whatsapp"]):
        return "Chamado Whats"

    if any(word in status for word in ["sem whatsapp", "sem whats", "sem whats app"]):
        return "Sem Whatsapp"

    if any(word in status for word in ["reuniao", "reuniao marcada", "reuniao agendada"]):
        return "Reunião"

    if "proposta" in status:
        return "Proposta"

    if any(word in status for word in ["fechado", "ganho", "cliente"]):
        return "Fechado"

    if any(word in status for word in ["sem resposta", "nao responde", "nao respondeu", "nao atendeu", "nao atende"]):
        return "Não responde"

    if any(word in status for word in ["sem interesse", "nao tem interesse", "não tem interesse"]):
        return "Sem interesse"

    if status == "retornar" or "ligacao retornar" in status or "retornar" in status:
        return "Retornar"

    if any(word in status for word in ["ligacao", "ligando", "telefonema", "telefone"]):
        return "Ligação"

    if any(word in status for word in ["conversando", "contato", "negoci", "andamento"]):
        return "Conversando"

    if any(word in status for word in ["novo", "lead"]):
        return "Novo Lead"

    return normalize_text(value)


STATUS_BADGE_CLASSES = {
    "Novo Lead": "novo-lead",
    "Chamado Whats": "qualificacao",
    "Conversando": "qualificacao",
    "Reunião": "reuniao",
    "Proposta": "proposta",
    "Fechado": "fechado",
    "Sem Resposta": "qualificacao",
    "Sem Whatsapp": "qualificacao",
    "Retornar": "qualificacao",
    "Sem interesse": "qualificacao",
    "Ligação - Conversando Whats": "qualificacao",
    "Ligação não atende/cx": "qualificacao",
    "Ligação Numero errado": "qualificacao",
    "Ligação retornar": "qualificacao",
}


def status_badge_class(status: str) -> str:
    return STATUS_BADGE_CLASSES.get(normalize_text(status), "qualificacao")


def resolve_company_status(row, fallback: str = "Novo Lead") -> str:
    current_status = status_group(row.get("_status_original", row.get("_status_grupo", fallback)))
    if current_status == "Não responde" and "Sem Resposta" in STATUS_OPTIONS:
        current_status = "Sem Resposta"
    if current_status == "Ligação":
        ligacao_status = normalize_text(row.get("_status_ligacao_original", ""))
        if ligacao_status in STATUS_OPTIONS:
            return ligacao_status
        current_status = "Ligação - Conversando Whats"
    if current_status not in STATUS_OPTIONS:
        return fallback
    return current_status


def update_company_status_in_sheet(sheet_row: int, new_status: str, columns: dict) -> None:
    status_column = columns.get("status_whatsapp") or columns.get("status")
    if not status_column:
        raise RuntimeError("Coluna de status não encontrada na planilha.")

    update_statuses_in_sheet(
        [{"sheet_row": sheet_row, "status": new_status}],
        status_column,
        columns.get("ultima_atualizacao"),
    )


def sync_pipeline_stage_to_sheet(sheet_row: int, stage: str) -> None:
    """Persiste a etapa do pipeline na coluna Status WhatsApp da planilha principal."""
    from config.crm_options import PIPELINE_STAGE_SHEET_STATUSES
    from app.services.crm_validation_service import normalize_legacy_stage

    stage = normalize_legacy_stage(stage)
    if not sheet_row or int(sheet_row) < 2 or not stage:
        return

    statuses = PIPELINE_STAGE_SHEET_STATUSES.get(stage)
    if not statuses:
        return

    df = load_sheet_data()
    if df.empty:
        return

    # Evita gravar etapa em linha fantasma (ex.: SheetRow=99 sem empresa na Folha1).
    match = df[df["_sheet_row"] == int(sheet_row)] if "_sheet_row" in df.columns else df.iloc[0:0]
    if match.empty:
        return
    empresa = normalize_text(match.iloc[0].get("_empresa", ""))
    if not empresa:
        return

    columns = identify_columns(df)
    update_company_status_in_sheet(sheet_row, statuses[0], columns)


def dashboard_status_from_rows(status_whatsapp: str, status_ligacao: str) -> str:
    """
    Define o status principal do dashboard usando as duas colunas novas:
    1. Status WhatsApp, quando preenchido.
    2. Status Ligação, quando o WhatsApp ainda está vazio.
    3. Novo Lead, quando ambos estão vazios.
    """
    whatsapp_text = normalize_text(status_whatsapp)
    ligacao_text = normalize_text(status_ligacao)

    if whatsapp_text:
        return status_group(whatsapp_text)

    if ligacao_text:
        return status_group(ligacao_text)

    return "Novo Lead"


def row_matches_status_filter(row, selected_status: str) -> bool:
    """
    Filtro único de Status: procura o status escolhido nas duas colunas da planilha,
    sem somar e sem agrupar.

    Exemplo: se escolher "Proposta", retorna linhas com:
    - Status WhatsApp = Proposta
    OU
    - Status Ligação = Proposta
    """
    status_value = normalize_text(selected_status)

    if not status_value or status_value == "Todos os status":
        return True

    normalized_filter = normalize_search_text(status_value)
    whatsapp_status = normalize_search_text(row.get("_status_whatsapp_original", ""))
    ligacao_status = normalize_search_text(row.get("_status_ligacao_original", ""))

    return whatsapp_status == normalized_filter or ligacao_status == normalized_filter




def row_matches_dashboard_card(row, selected_status: str) -> bool:
    """
    Cards da Visão Geral: usa o mesmo filtro único das duas colunas.
    A única exceção é Novo Lead, que também considera linhas sem nenhum status preenchido.
    """
    status_value = normalize_text(selected_status)

    if not status_value:
        return True

    if row_matches_status_filter(row, status_value):
        return True

    if normalize_search_text(status_value) == "novo lead":
        whatsapp_status = normalize_text(row.get("_status_whatsapp_original", ""))
        ligacao_status = normalize_text(row.get("_status_ligacao_original", ""))
        return not whatsapp_status and not ligacao_status

    return False


def count_dashboard_status(df: pd.DataFrame, status_name: str) -> int:
    if df.empty:
        return 0

    return int(df.apply(lambda row: row_matches_dashboard_card(row, status_name), axis=1).sum())


def calculate_score(row: pd.Series, columns: dict) -> int:
    score = 0

    def cell_value(column_key: str) -> str:
        column_name = columns.get(column_key)
        if not column_name:
            return ""
        return normalize_text(row.get(column_name, ""))

    if cell_value("telefone_b2b"):
        score += 15

    if cell_value("email"):
        score += 10

    if cell_value("site"):
        score += 10

    if cell_value("instagram"):
        score += 10

    if cell_value("linkedin"):
        score += 5

    if cell_value("socio_1"):
        score += 10

    capital_value = parse_money(cell_value("capital"))

    if capital_value >= 100000:
        score += 20
    elif capital_value >= 50000:
        score += 15
    elif capital_value > 0:
        score += 8

    grouped_status = status_group(
        cell_value("status_whatsapp") or cell_value("status")
    )

    if grouped_status == "Fechado":
        score += 20
    elif grouped_status == "Proposta":
        score += 16
    elif grouped_status == "Reunião":
        score += 14
    elif grouped_status == "Conversando":
        score += 12
    elif grouped_status == "Ligação":
        score += 10
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
    project_root = Path(__file__).resolve().parent.parent.parent
    possible_paths = [
        project_root / "logo_oppi.png",
        project_root / "logo.png",
        project_root / "assets" / "logo_oppi.png",
        project_root / "assets" / "logo.png",
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
def get_runtime_setting(name: str, default: str = "") -> str:
    """Busca variável de ambiente do EasyPanel."""
    environment_value = os.getenv(name)

    if normalize_text(environment_value):
        return str(environment_value)

    return default


def _decode_service_account_b64(raw_value: str) -> dict:
    """Converte o JSON da conta de serviço armazenado em Base64 no EasyPanel."""
    value = normalize_text(raw_value)

    if value.startswith("GCP_SERVICE_ACCOUNT_B64="):
        value = value.split("=", 1)[1].strip()

    if value.startswith("GOOGLE_SERVICE_ACCOUNT_B64="):
        value = value.split("=", 1)[1].strip()

    if not value:
        raise RuntimeError("A variável Base64 da conta de serviço está vazia.")

    try:
        decoded_json = base64.b64decode(value).decode("utf-8")
        credentials_info = json.loads(decoded_json)
    except Exception as error:
        raise RuntimeError(
            "Não consegui converter a variável GCP_SERVICE_ACCOUNT_B64 em JSON. "
            "Gere novamente o Base64 usando o arquivo JSON original da conta de serviço."
        ) from error

    if not isinstance(credentials_info, dict):
        raise RuntimeError("O conteúdo decodificado da conta de serviço não é um JSON válido.")

    return credentials_info


def _normalize_google_private_key(value: str) -> str:
    """Normaliza quebras de linha da chave privada sem alterar seu conteúdo."""
    private_key = str(value or "").strip()

    # Quando o valor foi salvo com aspas no painel, remove somente as aspas externas.
    if (private_key.startswith('"') and private_key.endswith('"')) or (
        private_key.startswith("'") and private_key.endswith("'")
    ):
        private_key = private_key[1:-1].strip()

    private_key = private_key.replace("\\n", "\n")

    if private_key and not private_key.endswith("\n"):
        private_key += "\n"

    return private_key


def _load_google_credentials_info() -> dict:
    """
    Prioridade de leitura:
    1. JSON completo em Base64 no EasyPanel;
    2. variáveis GOOGLE_* separadas.
    """
    b64_credentials = (
        os.getenv("GCP_SERVICE_ACCOUNT_B64", "").strip()
        or os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "").strip()
    )

    if b64_credentials:
        credentials_info = _decode_service_account_b64(b64_credentials)
    else:
        credentials_info = {
            "type": os.getenv("GOOGLE_TYPE", ""),
            "project_id": os.getenv("GOOGLE_PROJECT_ID", ""),
            "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID", ""),
            "private_key": os.getenv("GOOGLE_PRIVATE_KEY", ""),
            "client_email": os.getenv("GOOGLE_CLIENT_EMAIL", ""),
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "auth_uri": os.getenv("GOOGLE_AUTH_URI", ""),
            "token_uri": os.getenv("GOOGLE_TOKEN_URI", ""),
            "auth_provider_x509_cert_url": os.getenv(
                "GOOGLE_AUTH_PROVIDER_X509_CERT_URL",
                "",
            ),
            "client_x509_cert_url": (
                os.getenv("GOOGLE_CLIENT_X509_CERT_URL", "")
                or os.getenv("_CLIENT_X509_CERT_URL", "")
            ),
            "universe_domain": os.getenv("GOOGLE_UNIVERSE_DOMAIN", "googleapis.com"),
        }

        required_env_fields = [
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

        has_all_separate_env_values = all(
            normalize_text(credentials_info.get(field, ""))
            for field in required_env_fields
        )

        if not has_all_separate_env_values:
            raise RuntimeError(
                "Não encontrei credenciais completas do Google. No EasyPanel, "
                "configure preferencialmente uma única variável chamada "
                "GCP_SERVICE_ACCOUNT_B64 com o JSON completo convertido em Base64."
            )

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
            "A credencial do Google está incompleta. Campos ausentes: "
            + ", ".join(missing_fields)
        )

    credentials_info["private_key"] = _normalize_google_private_key(
        credentials_info.get("private_key", "")
    )

    return credentials_info


def get_gsheet_client():
    global _gsheet_client
    if _gsheet_client is not None:
        return _gsheet_client
    """Conecta ao Google Sheets usando uma única credencial consistente."""
    credentials_info = _load_google_credentials_info()

    try:
        credentials = Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES,
        )

        _gsheet_client = gspread.authorize(credentials)
        return _gsheet_client
    except Exception as error:
        raise RuntimeError(
            "Não consegui preparar a credencial do Google. Gere uma nova chave JSON "
            "da conta de serviço e atualize a variável GCP_SERVICE_ACCOUNT_B64 no EasyPanel."
        ) from error


def _folha1_snapshot_path():
    from app.services.storage_paths import get_storage_dir

    return get_storage_dir() / "folha1_snapshot.json"


def _save_folha1_snapshot(values: list[list[str]]) -> None:
    import json

    try:
        path = _folha1_snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"values": values}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_folha1_snapshot_values() -> list[list[str]] | None:
    import json

    path = _folha1_snapshot_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, list) or not values:
        return None
    return values


def _dataframe_from_sheet_values(values: list[list[str]]) -> pd.DataFrame:
    if not values:
        return pd.DataFrame()

    headers = make_unique_headers(values[0])
    rows = values[1:]
    header_len = len(headers)
    normalized_rows = []
    for row in rows:
        row_values = list(row[:header_len])
        if len(row_values) < header_len:
            row_values.extend([""] * (header_len - len(row_values)))
        normalized_rows.append(row_values)

    df = pd.DataFrame(normalized_rows, columns=headers)
    df["_sheet_row"] = list(range(2, len(normalized_rows) + 2))

    for column in df.columns:
        if column != "_sheet_row":
            df[column] = df[column].fillna("").astype(str).str.strip()

    data_columns = [column for column in df.columns if column != "_sheet_row"]
    if data_columns:
        df = df[
            df[data_columns].apply(
                lambda row: any(normalize_text(value) for value in row),
                axis=1,
            )
        ].copy()

    empresa_column = first_existing_column(
        df,
        ["Nome Empresas", "Nome da empresa", "Empresa", "Nome Empresa", "Nome empresas", "Nome Empresa(s)"],
    )
    if empresa_column:
        df = df[df[empresa_column].apply(lambda value: normalize_text(value) != "")].copy()

    return df.reset_index(drop=True)


def hydrate_sheet_cache_from_disk() -> bool:
    """Recarrega a última Folha1 salva em disco (sobrevive a rebuild)."""
    global _sheet_last_good, _sheet_last_good_values
    values = _load_folha1_snapshot_values()
    if not values:
        return False
    result = _dataframe_from_sheet_values(values)
    _sheet_last_good_values = [row[:] for row in values]
    _sheet_last_good = result.copy()
    _sheet_cache["sheet_data"] = result.copy()
    try:
        from app.services.pending_companies import remember_sheet_headers

        remember_sheet_headers(list(values[0]))
    except Exception:
        pass
    return not result.empty


def load_sheet_data() -> pd.DataFrame:
    global _sheet_last_good, _sheet_last_good_values
    cache_key = "sheet_data"
    if cache_key in _sheet_cache:
        return _sheet_cache[cache_key].copy()

    # Após rebuild, a memória vem vazia — recupera snapshot do disco antes da API.
    if _sheet_last_good is None:
        hydrate_sheet_cache_from_disk()
        if cache_key in _sheet_cache:
            return _sheet_cache[cache_key].copy()

    try:
        client = get_gsheet_client()
        try:
            spreadsheet = client.open_by_key(settings.sheet_id)
        except SpreadsheetNotFound as error:
            raise RuntimeError(
                f"Planilha não encontrada (ID: {settings.sheet_id}). "
                "Verifique se a conta de serviço tem acesso à planilha."
            ) from error

        worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
        values = worksheet.get_all_values()

        if not values:
            if _sheet_last_good is not None:
                return _sheet_last_good.copy()
            empty = pd.DataFrame()
            _sheet_cache[cache_key] = empty.copy()
            return empty

        result = _dataframe_from_sheet_values(values)
        _sheet_cache[cache_key] = result.copy()
        _sheet_last_good = result.copy()
        _sheet_last_good_values = [row[:] for row in values]
        _save_folha1_snapshot(values)
        try:
            from app.services.pending_companies import remember_sheet_headers

            remember_sheet_headers(list(values[0]))
        except Exception:
            pass
        return result
    except Exception as error:
        message = str(error)
        if _sheet_last_good is not None:
            return _sheet_last_good.copy()
        if hydrate_sheet_cache_from_disk() and _sheet_last_good is not None:
            return _sheet_last_good.copy()
        if "429" in message or "Quota exceeded" in message.lower():
            return pd.DataFrame()
        raise


def _open_worksheet(spreadsheet, worksheet_name: str):
    """Abre a aba configurada; se não existir, usa a primeira aba da planilha."""
    candidates = [worksheet_name]
    for fallback_name in ("Folha1", "Sheet1", "Página1", "Pagina1"):
        if fallback_name not in candidates:
            candidates.append(fallback_name)

    for name in candidates:
        if not normalize_text(name):
            continue
        try:
            return spreadsheet.worksheet(name)
        except WorksheetNotFound:
            continue

    return spreadsheet.sheet1


def update_statuses_in_sheet(
    changes: list[dict],
    status_column_name: str,
    updated_at_column_name: Optional[str] = None,
) -> None:
    """Atualiza os status editados diretamente na planilha do Google Sheets."""
    if not changes:
        return

    client = get_gsheet_client()
    spreadsheet = client.open_by_key(settings.sheet_id)
    worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
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

        if new_status == "Sem status":
            new_status = ""

        if new_status and new_status not in STATUS_OPTIONS and new_status not in DASHBOARD_STATUS_OPTIONS:
            raise RuntimeError(f"Status inválido: {new_status}")

        cells.append(gspread.Cell(sheet_row, status_column_index, new_status))

        if updated_at_column_index:
            cells.append(gspread.Cell(sheet_row, updated_at_column_index, now_text))

    worksheet.update_cells(cells, value_input_option="USER_ENTERED")
    invalidate_sheet_cache()


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


SHEET_SERVICO_ALIASES = [
    "Serviços fechados",
    "Servicos fechados",
    "Servico fechado",
    "Serviço fechado",
    "Serviço",
    "Servico",
    "Serviço escolhido",
    "Solução",
    "Solucao",
    "Produto Oppi",
]

SHEET_VALOR_SERVICO_ALIASES = [
    "Valor do serviço",
    "Valor do servico",
    "Valor serviço",
    "Valor servico",
    "Valor da proposta",
    "Valor proposta",
    "Valor Proposta",
]

REGISTRATION_OPTIONAL_COLUMNS: list[tuple[str, list[str]]] = [
    ("Serviços fechados", SHEET_SERVICO_ALIASES),
    ("Valor do serviço", SHEET_VALOR_SERVICO_ALIASES),
    ("Número", ["Número", "Numero", "Nº", "No"]),
    ("Complemento", ["Complemento", "Compl"]),
    ("CEP", ["CEP"]),
    ("Bairro", ["Bairro", "Bairro/Distrito", "Bairro Distrito", "Distrito"]),
    ("Município", ["Município", "Municipio", "Cidade"]),
    ("UF", ["UF", "Estado"]),
    ("Endereço completo", ["Endereço completo", "Endereco completo", "Endereço Completo"]),
]


def _worksheet_has_header(headers: list[str], aliases: list[str]) -> bool:
    normalized_headers = {normalize_search_text(header) for header in headers if normalize_text(header)}
    return any(normalize_search_text(alias) in normalized_headers for alias in aliases)


def ensure_registration_sheet_columns(worksheet) -> list[str]:
    """Garante colunas de serviços, valores e endereço detalhado na planilha."""
    headers = worksheet.row_values(1)
    if not headers:
        raise RuntimeError("A primeira linha da planilha precisa conter os cabeçalhos.")

    cells: list[gspread.Cell] = []
    next_col = len(headers) + 1
    for column_name, aliases in REGISTRATION_OPTIONAL_COLUMNS:
        if _worksheet_has_header(headers, aliases):
            continue
        cells.append(gspread.Cell(1, next_col, column_name))
        headers.append(column_name)
        next_col += 1

    if cells:
        worksheet.update_cells(cells, value_input_option="USER_ENTERED")
        invalidate_sheet_cache()

    return headers


def _apply_commercial_fields(row_values: list[str], headers: list[str], payload: dict) -> None:
    _set_sheet_value_by_header(row_values, headers, SHEET_SERVICO_ALIASES, payload.get("servico"))
    _set_sheet_value_by_header(row_values, headers, SHEET_VALOR_SERVICO_ALIASES, payload.get("valor_proposta"))
    _set_sheet_value_by_header(
        row_values,
        headers,
        ["Colaboradores", "Qtd colaboradores", "Quantidade de colaboradores", "Qtd. colaboradores"],
        payload.get("colaboradores"),
    )


def update_company_registration_fields(sheet_row: int, payload: dict) -> None:
    """Atualiza campos de endereço e serviços de uma linha sem sobrescrever o cadastro inteiro."""
    client = get_gsheet_client()
    spreadsheet = client.open_by_key(settings.sheet_id)
    worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
    headers = ensure_registration_sheet_columns(worksheet)

    current_row = worksheet.row_values(int(sheet_row))
    row_values = list(current_row) + [""] * max(0, len(headers) - len(current_row))
    row_values = row_values[:len(headers)]

    if any(payload.get(key) for key in ("endereco", "endereco_numero", "endereco_complemento", "cep", "bairro", "municipio", "uf")):
        _apply_address_fields(row_values, headers, payload)

    commercial_payload = {
        key: payload.get(key)
        for key in ("servico", "valor_proposta", "colaboradores")
        if key in payload
    }
    if commercial_payload:
        _apply_commercial_fields(row_values, headers, commercial_payload)

    changed_cells: list[gspread.Cell] = []
    for column_index, new_value in enumerate(row_values, start=1):
        old_value = current_row[column_index - 1] if column_index - 1 < len(current_row) else ""
        if normalize_text(old_value) != normalize_text(new_value):
            changed_cells.append(gspread.Cell(int(sheet_row), column_index, normalize_text(new_value)))

    if changed_cells:
        worksheet.update_cells(changed_cells, value_input_option="USER_ENTERED")
        invalidate_sheet_cache()


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


def validate_unique_company_registration(
    payload: dict,
    worksheet=None,
    ignore_sheet_row: Optional[int] = None,
    values: Optional[list[list[str]]] = None,
) -> None:
    """
    Bloqueia cadastro ou edição quando qualquer telefone, CPF ou CNPJ informado já existe
    em outra linha da planilha. Prefere o cache local para não estourar a cota de leituras.
    """
    if values is None:
        values = get_last_good_sheet_values()

    if values is None and worksheet is not None:
        try:
            values = worksheet.get_all_values()
        except Exception as error:
            message = str(error)
            # Sem cache e com cota estourada: não impede o salvamento.
            if "429" in message or "Quota exceeded" in message.lower():
                return
            raise

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
            "telefone_socio_2",
            "telefone_socio_3",
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

    for row_offset, row in enumerate(rows, start=2):
        if ignore_sheet_row is not None and int(row_offset) == int(ignore_sheet_row):
            continue

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
        "Não foi possível salvar porque já existe outro cadastro com os mesmos dados. " + " | ".join(messages)
    )



def compose_endereco(payload: dict) -> str:
    logradouro = normalize_text(payload.get("endereco"))
    numero = normalize_text(payload.get("endereco_numero"))
    complemento = normalize_text(payload.get("endereco_complemento"))
    bairro = normalize_text(payload.get("bairro"))
    municipio = normalize_text(payload.get("municipio"))
    uf = normalize_text(payload.get("uf"))
    cep = normalize_text(payload.get("cep"))

    if not any([logradouro, numero, complemento, bairro, municipio, uf, cep]):
        return ""

    street_parts = [logradouro]
    if numero:
        street_parts.append(numero)
    if complemento:
        street_parts.append(complemento)
    line = ", ".join(part for part in street_parts if part)

    location_parts = []
    if bairro:
        location_parts.append(bairro)
    city_state = "/".join(part for part in [municipio, uf] if part)
    if city_state:
        location_parts.append(city_state)
    if cep:
        location_parts.append(f"CEP {cep}")

    if location_parts:
        return f"{line} - {', '.join(location_parts)}" if line else ", ".join(location_parts)
    return line


def parse_composed_endereco(text: str) -> dict[str, str]:
    """Reverte endereço composto salvo em uma única célula para campos do formulário."""
    raw = normalize_text(text)
    if not raw:
        return {}

    result = {
        "endereco": "",
        "endereco_numero": "",
        "endereco_complemento": "",
        "cep": "",
        "bairro": "",
        "municipio": "",
        "uf": "",
    }

    street_part = raw
    location_part = ""
    if " - " in raw:
        street_part, location_part = raw.split(" - ", 1)
        street_part = street_part.strip()
        location_part = location_part.strip()

    search_text = location_part or raw
    cep_match = re.search(r"\bCEP\s*(\d{5}-?\d{3})\b", search_text, flags=re.IGNORECASE)
    if not cep_match:
        cep_match = re.search(r"\b(\d{5}-?\d{3})\b", search_text)
    if cep_match:
        result["cep"] = cep_match.group(1)
        location_part = location_part.replace(cep_match.group(0), "").strip(" ,")

    if location_part:
        loc_segments = [segment.strip() for segment in location_part.split(",") if segment.strip()]
        if not loc_segments:
            loc_segments = [location_part]

        if len(loc_segments) == 1:
            segment = loc_segments[0]
            if "/" in segment:
                city, state = segment.split("/", 1)
                result["municipio"] = normalize_text(city)
                result["uf"] = normalize_text(state)
            else:
                result["municipio"] = segment
        else:
            for segment in loc_segments:
                if "/" in segment:
                    city, state = segment.split("/", 1)
                    result["municipio"] = normalize_text(city)
                    result["uf"] = normalize_text(state)
                    continue
                if not result["bairro"]:
                    result["bairro"] = segment
                elif not result["municipio"]:
                    result["municipio"] = segment
                elif not result["uf"] and len(segment) == 2:
                    result["uf"] = segment.upper()

    street_segments = [segment.strip() for segment in street_part.split(",") if segment.strip()]
    if not street_segments:
        return result

    result["endereco"] = street_segments[0]
    if len(street_segments) == 1:
        return result

    second = street_segments[1]
    if re.match(r"^\d+\w?$", second):
        result["endereco_numero"] = second
        if len(street_segments) >= 3:
            result["endereco_complemento"] = ", ".join(street_segments[2:])
    else:
        result["endereco_complemento"] = ", ".join(street_segments[1:])

    return result


def resolve_address_form_values(row, columns: dict) -> dict[str, str]:
    """Lê endereço da planilha e devolve campos separados para o formulário."""
    keys = ("endereco", "endereco_numero", "endereco_complemento", "cep", "bairro", "municipio", "uf")
    values = {key: row_field_value(row, columns, key) for key in keys}
    endereco = values["endereco"]
    if not endereco:
        return values

    composed_from_parts = compose_endereco(values)
    missing_street_parts = not any(values[key] for key in ("endereco_numero", "endereco_complemento"))
    missing_location_parts = not any(values[key] for key in ("bairro", "municipio", "cep"))
    looks_composed = " - " in endereco or endereco.count(",") >= 1

    should_parse = looks_composed and (
        missing_street_parts
        or missing_location_parts
        or endereco == composed_from_parts
    )
    if not should_parse:
        return values

    parsed = parse_composed_endereco(endereco)
    for key in keys:
        if parsed.get(key):
            values[key] = parsed[key]
    return values


def format_endereco_for_display(row, columns: dict) -> str:
    """Monta endereço completo para PDFs e telas de resumo."""
    parts = resolve_address_form_values(row, columns)
    composed = compose_endereco(parts)
    return composed or normalize_text(parts.get("endereco")) or "Não informado"


def _apply_address_fields(row_values: list, headers: list, payload: dict) -> None:
    composed = compose_endereco(payload)
    logradouro = normalize_text(payload.get("endereco"))

    _set_sheet_value_by_header(row_values, headers, ["Endereço", "Endereco"], logradouro)
    _set_sheet_value_by_header(row_values, headers, ["Logradouro", "Rua", "Endereço logradouro"], logradouro)
    _set_sheet_value_by_header(
        row_values,
        headers,
        ["Endereço completo", "Endereco completo", "Endereço Completo"],
        composed,
    )
    _set_sheet_value_by_header(row_values, headers, ["Número", "Numero", "Nº", "No"], payload.get("endereco_numero"))
    _set_sheet_value_by_header(row_values, headers, ["Complemento", "Compl"], payload.get("endereco_complemento"))
    _set_sheet_value_by_header(row_values, headers, ["CEP"], payload.get("cep"))
    _set_sheet_value_by_header(row_values, headers, ["Bairro", "Bairro/Distrito", "Bairro Distrito", "Distrito"], payload.get("bairro"))
    _set_sheet_value_by_header(row_values, headers, ["Município", "Municipio", "Cidade"], payload.get("municipio"))
    _set_sheet_value_by_header(row_values, headers, ["UF", "Estado"], payload.get("uf"))


def _folha1_last_used_row(values: list[list[str]] | None) -> int:
    """Última linha com algum conteúdo (1 = cabeçalho)."""
    if not values:
        return 1
    last = 1
    for index, row in enumerate(values, start=1):
        if any(normalize_text(cell) for cell in (row or [])):
            last = index
    return last


def _folha1_next_row(worksheet, cached_values: list[list[str]] | None = None) -> int:
    """Próxima linha livre na Folha1 — não usa worksheet.row_count (tamanho da grade)."""
    if cached_values:
        return _folha1_last_used_row(cached_values) + 1
    try:
        col_a = worksheet.col_values(1)
        while col_a and not normalize_text(col_a[-1]):
            col_a.pop()
        return max(len(col_a) + 1, 2)
    except Exception:
        return _folha1_last_used_row(get_last_good_sheet_values()) + 1


def _write_folha1_row(worksheet, row_number: int, row_values: list[str]) -> int:
    """Grava uma linha na Folha1 por update explícito (mais confiável que append_row + row_count)."""
    row_number = max(int(row_number), 2)
    if row_number > int(worksheet.row_count or 0):
        try:
            worksheet.add_rows(row_number - int(worksheet.row_count) + 20)
        except Exception:
            pass
    # Garante largura mínima das colunas
    needed_cols = len(row_values)
    if needed_cols > int(worksheet.col_count or 0):
        try:
            worksheet.add_cols(needed_cols - int(worksheet.col_count) + 2)
        except Exception:
            pass
    worksheet.update(
        f"A{row_number}",
        [list(row_values)],
        value_input_option="USER_ENTERED",
    )
    return row_number


def append_company_to_sheet(payload: dict) -> int:
    """Adiciona empresa na planilha. Se a API falhar, salva local e sincroniza depois."""
    global _sheet_last_good, _sheet_last_good_values
    import time

    from app.services.pending_companies import (
        queue_company_registration,
        remember_sheet_headers,
        resolve_registration_headers,
        sync_pending_companies_to_sheet,
    )

    cached_values = get_last_good_sheet_values()
    headers = resolve_registration_headers(cached_values)

    worksheet = None
    if not headers:
        try:
            client = get_gsheet_client()
            spreadsheet = client.open_by_key(settings.sheet_id)
            worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
            headers = ensure_registration_sheet_columns(worksheet)
            remember_sheet_headers(headers)
        except Exception as error:
            message = str(error)
            if "429" in message or "Quota exceeded" in message.lower():
                # Sem cabeçalhos e sem cota: usa um conjunto mínimo para não perder o cadastro.
                headers = [
                    "Nome Empresas", "CNPJ", "Data de abertura", "Capital",
                    "Endereço", "Número", "Complemento", "CEP", "Bairro", "Município", "UF",
                    "Email", "Site empresa",
                    "Celular WhatsApp", "Telefone fixo", "Telefone lemitt",
                    "Sócio 1", "CPF", "E-mail Sócio 1", "Telefone",
                    "Sócio 2", "Telefone sócio 2", "CPF_2",
                    "Sócio 3", "Telefone sócio 3", "CPF_3",
                    "Instagram", "Linkedin", "Vendedor",
                    "Status WhatsApp", "Data do chamado", "Última atualização", "Observações",
                    "Serviços fechados", "Valor do serviço", "Colaboradores",
                ]
            else:
                raise

    validate_unique_company_registration(payload, values=cached_values)

    if not headers:
        raise RuntimeError("A primeira linha da planilha precisa conter os cabeçalhos.")

    row_values = [""] * len(headers)

    _set_sheet_value_by_header(row_values, headers, ["Nome Empresas", "Nome da empresa", "Empresa", "Nome Empresa", "Nome empresas", "Nome Empresa(s)"], payload.get("empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Data de abertura", "Data abertura"], payload.get("data_abertura"))
    _set_sheet_value_by_header(row_values, headers, ["Capital", "Capital social"], payload.get("capital"))
    _set_sheet_value_by_header(row_values, headers, ["CNPJ"], payload.get("cnpj"))
    _apply_address_fields(row_values, headers, payload)
    _set_sheet_value_by_header(row_values, headers, ["Email", "E-mail"], payload.get("email_empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Site empresa", "Site", "Website"], payload.get("site"))

    _set_sheet_value_by_header(row_values, headers, ["Celular WhatsApp", "Telefone (b2b)", "Telefone b2b"], payload.get("telefone_b2b"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone fixo", "Fixo"], payload.get("telefone_fixo"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone lemitt", "Telefone alternativo", "Outro telefone"], payload.get("telefone_alternativo"))

    _set_sheet_value_by_header(row_values, headers, ["Sócio 1", "Socio 1", "Sócio1", "Socio1"], payload.get("socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_1"), occurrence=1)
    _set_sheet_value_by_header(row_values, headers, ["E-mail Sócio 1", "Email Sócio 1", "E-mail Socio 1", "Email Socio 1"], payload.get("email_socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone"], payload.get("telefone_socio_1"), occurrence=1)

    _set_sheet_value_by_header(row_values, headers, ["Sócio 2", "Socio 2", "Sócio2", "Socio2"], payload.get("socio_2"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone sócio 2", "Telefone socio 2", "Telefone do sócio 2", "Telefone do socio 2", "Telefone"], payload.get("telefone_socio_2"), occurrence=2)
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_2"), occurrence=2)

    _set_sheet_value_by_header(row_values, headers, ["Sócio 3", "Socio 3", "Sócio3", "Socio3"], payload.get("socio_3"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone sócio 3", "Telefone socio 3", "Telefone do sócio 3", "Telefone do socio 3", "Telefone"], payload.get("telefone_socio_3"), occurrence=3)
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_3"), occurrence=3)

    _set_sheet_value_by_header(row_values, headers, ["Instagram"], payload.get("instagram"))
    _set_sheet_value_by_header(row_values, headers, ["Linkedin", "LinkedIn"], payload.get("linkedin"))
    _set_sheet_value_by_header(row_values, headers, ["Vendedor", "Responsável", "Responsavel"], payload.get("vendedor"))
    _set_sheet_value_by_header(row_values, headers, ["Status WhatsApp", "Status", "Etapa"], payload.get("status"))
    _set_sheet_value_by_header(row_values, headers, ["Data do chamado", "Data chamado"], payload.get("data_chamado"))
    _set_sheet_value_by_header(row_values, headers, ["Última atualização", "Ultima atualização", "Ultima atualizacao"], payload.get("ultima_atualizacao"))
    _set_sheet_value_by_header(row_values, headers, ["Observações", "Observacoes", "Observação", "Observacao"], payload.get("observacoes"))
    _apply_commercial_fields(row_values, headers, payload)

    last_error = ""
    for attempt in range(5):
        try:
            if worksheet is None:
                client = get_gsheet_client()
                spreadsheet = client.open_by_key(settings.sheet_id)
                worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
            try:
                live_headers = worksheet.row_values(1)
                if live_headers and any(normalize_text(h) for h in live_headers):
                    headers = live_headers
                    remember_sheet_headers(headers)
                    row_values = [""] * len(headers)
                    _set_sheet_value_by_header(row_values, headers, ["Nome Empresas", "Nome da empresa", "Empresa", "Nome Empresa", "Nome empresas", "Nome Empresa(s)"], payload.get("empresa"))
                    _set_sheet_value_by_header(row_values, headers, ["Data de abertura", "Data abertura"], payload.get("data_abertura"))
                    _set_sheet_value_by_header(row_values, headers, ["Capital", "Capital social"], payload.get("capital"))
                    _set_sheet_value_by_header(row_values, headers, ["CNPJ"], payload.get("cnpj"))
                    _apply_address_fields(row_values, headers, payload)
                    _set_sheet_value_by_header(row_values, headers, ["Email", "E-mail"], payload.get("email_empresa"))
                    _set_sheet_value_by_header(row_values, headers, ["Site empresa", "Site", "Website"], payload.get("site"))
                    _set_sheet_value_by_header(row_values, headers, ["Celular WhatsApp", "Telefone (b2b)", "Telefone b2b"], payload.get("telefone_b2b"))
                    _set_sheet_value_by_header(row_values, headers, ["Telefone fixo", "Fixo"], payload.get("telefone_fixo"))
                    _set_sheet_value_by_header(row_values, headers, ["Telefone lemitt", "Telefone alternativo", "Outro telefone"], payload.get("telefone_alternativo"))
                    _set_sheet_value_by_header(row_values, headers, ["Sócio 1", "Socio 1", "Sócio1", "Socio1"], payload.get("socio_1"))
                    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_1"), occurrence=1)
                    _set_sheet_value_by_header(row_values, headers, ["E-mail Sócio 1", "Email Sócio 1", "E-mail Socio 1", "Email Socio 1"], payload.get("email_socio_1"))
                    _set_sheet_value_by_header(row_values, headers, ["Telefone"], payload.get("telefone_socio_1"), occurrence=1)
                    _set_sheet_value_by_header(row_values, headers, ["Sócio 2", "Socio 2", "Sócio2", "Socio2"], payload.get("socio_2"))
                    _set_sheet_value_by_header(row_values, headers, ["Telefone sócio 2", "Telefone socio 2", "Telefone do sócio 2", "Telefone do socio 2", "Telefone"], payload.get("telefone_socio_2"), occurrence=2)
                    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_2"), occurrence=2)
                    _set_sheet_value_by_header(row_values, headers, ["Sócio 3", "Socio 3", "Sócio3", "Socio3"], payload.get("socio_3"))
                    _set_sheet_value_by_header(row_values, headers, ["Telefone sócio 3", "Telefone socio 3", "Telefone do sócio 3", "Telefone do socio 3", "Telefone"], payload.get("telefone_socio_3"), occurrence=3)
                    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_3"), occurrence=3)
                    _set_sheet_value_by_header(row_values, headers, ["Instagram"], payload.get("instagram"))
                    _set_sheet_value_by_header(row_values, headers, ["Linkedin", "LinkedIn"], payload.get("linkedin"))
                    _set_sheet_value_by_header(row_values, headers, ["Vendedor", "Responsável", "Responsavel"], payload.get("vendedor"))
                    _set_sheet_value_by_header(row_values, headers, ["Status WhatsApp", "Status", "Etapa"], payload.get("status"))
                    _set_sheet_value_by_header(row_values, headers, ["Data do chamado", "Data chamado"], payload.get("data_chamado"))
                    _set_sheet_value_by_header(row_values, headers, ["Última atualização", "Ultima atualização", "Ultima atualizacao"], payload.get("ultima_atualizacao"))
                    _set_sheet_value_by_header(row_values, headers, ["Observações", "Observacoes", "Observação", "Observacao"], payload.get("observacoes"))
                    _apply_commercial_fields(row_values, headers, payload)
            except Exception:
                pass
            next_row = _folha1_next_row(worksheet, None)
            if next_row < 2:
                next_row = _folha1_next_row(worksheet, cached_values or _sheet_last_good_values)
            sheet_row = _write_folha1_row(worksheet, next_row, row_values)
            if _sheet_last_good_values:
                _sheet_last_good_values = [row[:] for row in _sheet_last_good_values]
                if _sheet_last_good_values and list(_sheet_last_good_values[0]) != list(headers):
                    _sheet_last_good_values[0] = list(headers)
                while len(_sheet_last_good_values) < sheet_row:
                    _sheet_last_good_values.append([""] * len(headers))
                _sheet_last_good_values[sheet_row - 1] = list(row_values)
            else:
                padding = [[""] * len(headers) for _ in range(max(sheet_row - 2, 0))]
                _sheet_last_good_values = [list(headers), *padding, list(row_values)]
            remember_sheet_headers(headers)
            try:
                _save_folha1_snapshot(_sheet_last_good_values)
                _sheet_last_good = _dataframe_from_sheet_values(_sheet_last_good_values)
                _sheet_cache["sheet_data"] = _sheet_last_good.copy()
            except Exception:
                pass
            invalidate_sheet_cache()
            try:
                if _sheet_last_good is not None:
                    _sheet_cache["sheet_data"] = _sheet_last_good.copy()
            except Exception:
                pass
            return int(sheet_row)
        except Exception as error:
            last_error = str(error)
            message = last_error.lower()
            if "429" in last_error or "quota exceeded" in message:
                time.sleep(3 + attempt * 2)
                worksheet = None
                cached_values = get_last_good_sheet_values()
                continue
            break

    # Não perde o cadastro: fica local e aparece no site enquanto sincroniza.
    pending_row = queue_company_registration(
        payload=payload,
        headers=headers,
        row_values=row_values,
        last_error=last_error or "Falha ao gravar na planilha",
    )
    try:
        sync_pending_companies_to_sheet(max_items=5)
    except Exception:
        pass
    return pending_row


def update_company_in_sheet(sheet_row: int, payload: dict) -> None:
    """Atualiza uma empresa diretamente na planilha, preservando as demais colunas da linha."""
    client = get_gsheet_client()
    spreadsheet = client.open_by_key(settings.sheet_id)
    worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
    headers = ensure_registration_sheet_columns(worksheet)

    if not headers:
        raise RuntimeError("A primeira linha da planilha precisa conter os cabeçalhos.")

    validate_unique_company_registration(
        payload,
        worksheet,
        ignore_sheet_row=int(sheet_row),
    )

    current_row = worksheet.row_values(int(sheet_row))
    row_values = list(current_row) + [""] * max(0, len(headers) - len(current_row))
    row_values = row_values[:len(headers)]

    _set_sheet_value_by_header(row_values, headers, ["Nome Empresas", "Nome da empresa", "Empresa", "Nome Empresa", "Nome empresas", "Nome Empresa(s)"], payload.get("empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Data de abertura", "Data abertura"], payload.get("data_abertura"))
    _set_sheet_value_by_header(row_values, headers, ["Capital", "Capital social"], payload.get("capital"))
    _set_sheet_value_by_header(row_values, headers, ["CNPJ"], payload.get("cnpj"))
    _apply_address_fields(row_values, headers, payload)
    _set_sheet_value_by_header(row_values, headers, ["Email", "E-mail"], payload.get("email_empresa"))
    _set_sheet_value_by_header(row_values, headers, ["Site empresa", "Site", "Website"], payload.get("site"))

    _set_sheet_value_by_header(row_values, headers, ["Celular WhatsApp", "Telefone (b2b)", "Telefone b2b"], payload.get("telefone_b2b"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone fixo", "Fixo"], payload.get("telefone_fixo"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone lemitt", "Telefone alternativo", "Outro telefone"], payload.get("telefone_alternativo"))

    _set_sheet_value_by_header(row_values, headers, ["Sócio 1", "Socio 1", "Sócio1", "Socio1"], payload.get("socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_1"), occurrence=1)
    _set_sheet_value_by_header(row_values, headers, ["E-mail Sócio 1", "Email Sócio 1", "E-mail Socio 1", "Email Socio 1"], payload.get("email_socio_1"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone"], payload.get("telefone_socio_1"), occurrence=1)

    _set_sheet_value_by_header(row_values, headers, ["Sócio 2", "Socio 2", "Sócio2", "Socio2"], payload.get("socio_2"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone sócio 2", "Telefone socio 2", "Telefone do sócio 2", "Telefone do socio 2", "Telefone"], payload.get("telefone_socio_2"), occurrence=2)
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_2"), occurrence=2)

    _set_sheet_value_by_header(row_values, headers, ["Sócio 3", "Socio 3", "Sócio3", "Socio3"], payload.get("socio_3"))
    _set_sheet_value_by_header(row_values, headers, ["Telefone sócio 3", "Telefone socio 3", "Telefone do sócio 3", "Telefone do socio 3", "Telefone"], payload.get("telefone_socio_3"), occurrence=3)
    _set_sheet_value_by_header(row_values, headers, ["CPF"], payload.get("cpf_socio_3"), occurrence=3)

    _set_sheet_value_by_header(row_values, headers, ["Instagram"], payload.get("instagram"))
    _set_sheet_value_by_header(row_values, headers, ["Linkedin", "LinkedIn"], payload.get("linkedin"))
    _set_sheet_value_by_header(row_values, headers, ["Vendedor", "Responsável", "Responsavel"], payload.get("vendedor"))
    _set_sheet_value_by_header(row_values, headers, ["Status", "Etapa"], payload.get("status"))
    _set_sheet_value_by_header(row_values, headers, ["Data do chamado", "Data chamado"], payload.get("data_chamado"))
    _set_sheet_value_by_header(row_values, headers, ["Última atualização", "Ultima atualização", "Ultima atualizacao"], payload.get("ultima_atualizacao"))
    _set_sheet_value_by_header(row_values, headers, ["Observações", "Observacoes", "Observação", "Observacao"], payload.get("observacoes"))
    _apply_commercial_fields(row_values, headers, payload)

    changed_cells = []

    for column_index, new_value in enumerate(row_values, start=1):
        old_value = current_row[column_index - 1] if column_index - 1 < len(current_row) else ""

        if normalize_text(old_value) != normalize_text(new_value):
            changed_cells.append(gspread.Cell(int(sheet_row), column_index, normalize_text(new_value)))

    if changed_cells:
        worksheet.update_cells(changed_cells, value_input_option="USER_ENTERED")

    invalidate_sheet_cache()


def delete_company_from_sheet(sheet_row: int) -> None:
    """Remove a linha do cadastro comercial na planilha."""
    row_number = int(sheet_row)
    if row_number < 2:
        raise ValueError("Linha inválida para exclusão.")

    client = get_gsheet_client()
    spreadsheet = client.open_by_key(settings.sheet_id)
    worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)

    if row_number > worksheet.row_count:
        raise ValueError("Cadastro não encontrado na planilha.")

    worksheet.delete_rows(row_number)
    invalidate_sheet_cache()


# =========================================================
# IDENTIFICAÇÃO DAS COLUNAS
# =========================================================
def identify_columns(df: pd.DataFrame) -> dict:
    return {
        "empresa": first_existing_column(df, ["Nome Empresas", "Nome da empresa", "Empresa", "Nome Empresa", "Nome empresas", "Nome Empresa(s)"]),
        "data_abertura": first_existing_column(df, ["Data de abertura", "Data abertura"]),
        "capital": first_existing_column(df, ["Capital", "Capital social"]),
        "cnpj": first_existing_column(df, ["CNPJ"]),
        "endereco": first_existing_column(df, ["Endereço", "Endereco", "Logradouro", "Rua"]),
        "endereco_numero": first_existing_column(df, ["Número", "Numero", "Nº", "No"]),
        "endereco_complemento": first_existing_column(df, ["Complemento", "Compl"]),
        "cep": first_existing_column(df, ["CEP"]),
        "bairro": first_existing_column(df, ["Bairro", "Bairro/Distrito", "Bairro Distrito", "Distrito"]),
        "municipio": first_existing_column(df, ["Município", "Municipio", "Cidade"]),
        "uf": first_existing_column(df, ["UF", "Estado"]),
        "email": first_existing_column(df, ["Email", "E-mail", "Email empresa", "E-mail empresa", "email_empresa"]),
        "site": first_existing_column(df, ["Site empresa", "Site", "Website"]),
        "telefone_b2b": first_existing_column(df, ["Celular WhatsApp", "Telefone (b2b)", "Telefone b2b", "Telefone"]),
        "telefone_fixo": first_existing_column(df, ["Telefone fixo", "Fixo"]),
        "telefone_alternativo": first_existing_column(df, ["Telefone lemitt", "Telefone alternativo", "Outro telefone"]),
        "socio_1": first_existing_column(df, ["Sócio 1", "Socio 1", "Sócio1", "Socio1"]),
        "cpf_socio_1": first_existing_column(df, ["CPF"]),
        "email_socio_1": first_existing_column(df, ["E-mail Sócio 1", "Email Sócio 1", "E-mail Socio 1", "Email Socio 1"]),
        "telefone_socio_1": (
            first_existing_column(df, ["Telefone sócio 1", "Telefone socio 1", "Telefone cliente"])
            or existing_column_by_occurrence(df, ["Telefone"], occurrence=1)
        ),
        "socio_2": first_existing_column(df, ["Sócio 2", "Socio 2", "Sócio2", "Socio2"]),
        "telefone_socio_2": (
            first_existing_column(df, ["Telefone sócio 2", "Telefone socio 2", "Telefone do sócio 2", "Telefone do socio 2"])
            or existing_column_by_occurrence(df, ["Telefone"], occurrence=2)
        ),
        "cpf_socio_2": first_existing_column(df, ["CPF_2"]),
        "socio_3": first_existing_column(df, ["Sócio 3", "Socio 3", "Sócio3", "Socio3"]),
        "telefone_socio_3": (
            first_existing_column(df, ["Telefone sócio 3", "Telefone socio 3", "Telefone do sócio 3", "Telefone do socio 3"])
            or existing_column_by_occurrence(df, ["Telefone"], occurrence=3)
        ),
        "cpf_socio_3": first_existing_column(df, ["CPF_3"]),
        "instagram": first_existing_column(df, ["Instagram"]),
        "linkedin": first_existing_column(df, ["Linkedin", "LinkedIn"]),
        "vendedor": first_existing_column(df, ["Vendedor", "Responsável", "Responsavel"]),
        "status_whatsapp": first_existing_column(df, ["Status WhatsApp", "Status Whatsapp", "Status Whats", "Status Whats App"]),
        "status_ligacao": first_existing_column(df, ["Status Ligação", "Status Ligacao", "Status da Ligação", "Status da Ligacao"]),
        "status": first_existing_column(df, ["Status WhatsApp", "Status Whatsapp", "Status Whats", "Status Whats App", "Status", "Etapa"]),
        "data_chamado": first_existing_column(df, ["Data do chamado", "Data chamado"]),
        "ultima_atualizacao": first_existing_column(df, ["Última atualização", "Ultima atualização", "Ultima atualizacao"]),
        "observacoes": first_existing_column(df, ["Observações", "Observacoes", "Observação", "Observacao"]),
        "servico": first_existing_column(
            df,
            SHEET_SERVICO_ALIASES,
        ),
        "valor_proposta": first_existing_column(
            df,
            SHEET_VALOR_SERVICO_ALIASES,
        ),
        "colaboradores": first_existing_column(
            df,
            ["Colaboradores", "Qtd colaboradores", "Quantidade de colaboradores", "Qtd. colaboradores"],
        ),
    }


def prepare_data(df: pd.DataFrame, columns: dict) -> pd.DataFrame:
    result = df.copy()

    empresa_column = columns.get("empresa") or first_existing_column(result, ["Nome Empresas", "Nome da empresa", "Empresa", "Nome Empresa"])
    result["_empresa"] = safe_series(result, empresa_column)
    result["_capital_num"] = safe_series(result, columns.get("capital")).apply(parse_money)
    result["_valor_proposta_num"] = safe_series(result, columns.get("valor_proposta")).apply(parse_money)
    result["_status_whatsapp_original"] = safe_series(result, columns.get("status_whatsapp") or columns.get("status"))
    result["_status_ligacao_original"] = safe_series(result, columns.get("status_ligacao"))
    result["_status_original"] = result["_status_whatsapp_original"].replace("", "Novo Lead")
    result["_status_grupo"] = result.apply(
        lambda row: dashboard_status_from_rows(
            row.get("_status_whatsapp_original", ""),
            row.get("_status_ligacao_original", ""),
        ),
        axis=1,
    )
    result["_vendedor"] = safe_series(result, columns.get("vendedor")).replace("", "Sem vendedor")
    result["_telefone"] = safe_series(result, columns.get("telefone_b2b"))
    result["_nicho"] = result["_empresa"].apply(infer_niche_from_company_name)
    if "_sheet_row" in result.columns:
        try:
            from app.services.lead_actions_storage import DEFAULT_TENANT_ID, get_all_lead_actions

            stored_actions = get_all_lead_actions(DEFAULT_TENANT_ID)

            def _resolve_nicho(row):
                sheet_row = int(row.get("_sheet_row", 0) or 0)
                stored = stored_actions.get(str(sheet_row)) or {}
                nicho = normalize_text(stored.get("nicho"))
                if nicho:
                    return nicho
                return infer_niche_from_company_name(row.get("_empresa", ""))

            result["_nicho"] = result.apply(_resolve_nicho, axis=1)
        except Exception:
            pass
    result["_estado"] = safe_series(result, columns.get("endereco")).apply(infer_state_from_address)
    result["_data_abertura"] = safe_series(result, columns.get("data_abertura")).apply(parse_date)
    result["_ultima_atualizacao"] = safe_series(result, columns.get("ultima_atualizacao")).apply(parse_date)
    result["_data_chamado"] = safe_series(result, columns.get("data_chamado")).apply(parse_date)
    result["_data_chamado"] = result.apply(
        lambda row: _coalesce_dates(
            row.get("_data_chamado"),
            row.get("_data_abertura"),
            row.get("_ultima_atualizacao"),
        ),
        axis=1,
    )
    for date_column in ("_data_abertura", "_ultima_atualizacao", "_data_chamado"):
        result[date_column] = as_datetime_series(result[date_column])
    result["_pontuacao"] = result.apply(lambda row: calculate_score(row, columns), axis=1)
    result["_classificacao"] = result["_pontuacao"].apply(score_classification)

    return result


def _coalesce_dates(*values):
    for raw in values:
        value = coerce_scalar(raw)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        return value
    return pd.NaT




# --- Pricing session store ---
class PricingSessionStore:
    def __init__(self, data: dict | None = None):
        self.data = data or {"threads": {}, "progress": {}, "answers": {}}

    def get_threads(self) -> dict:
        return self.data.setdefault("threads", {})

    def get_progress(self) -> dict:
        return self.data.setdefault("progress", {})

    def get_answers(self) -> dict:
        return self.data.setdefault("answers", {})


_pricing_store: PricingSessionStore | None = None


def set_pricing_store(store: PricingSessionStore) -> None:
    global _pricing_store
    _pricing_store = store


def _get_pricing_store() -> PricingSessionStore:
    if _pricing_store is None:
        return PricingSessionStore()
    return _pricing_store


def _diagnostic_get_threads() -> dict:
    return _get_pricing_store().get_threads()


def _diagnostic_get_progress() -> dict:
    return _get_pricing_store().get_progress()


def _diagnostic_get_answers() -> dict:
    return _get_pricing_store().get_answers()


PRICING_SCRIPT_VERSION = "pricing_v4_pdf_diagnostico"

OPPI_PRICING_INTRO = (
    "Olá! Vou ajudar você, vendedor, a elaborar a faixa de preço para este cliente. "
    "As perguntas são sempre as mesmas. Nas perguntas de peso, responda somente com o número da opção correspondente. "
    "No final, descreva brevemente o cliente, os pontos discutidos na reunião, os serviços desejados e os problemas apresentados."
)

OPPI_PRICING_STEPS = [
    {
        "id": "colaboradores",
        "title": "🔵 1. Quantidade de colaboradores do cliente",
        "question": "Quantos colaboradores o cliente possui atualmente?",
        "options": [
            "1 — Pequena: 1 a 5 colaboradores",
            "2 — Média: 6 a 15 colaboradores",
            "3 — Estruturada: 16 a 30 colaboradores",
            "4 — Operação grande: acima de 30 colaboradores",
        ],
        "example": "Responda somente com: 1, 2, 3 ou 4.",
        "weighted": True,
    },
    {
        "id": "setores",
        "title": "🟣 2. Setores que o cliente deseja organizar",
        "question": "Quantos setores ou áreas o cliente deseja organizar?",
        "options": [
            "1 — Simples: apenas um fluxo ou setor",
            "2 — Média: comercial + atendimento",
            "3 — Alta: comercial + operação + pós-venda",
            "4 — Complexa: múltiplas equipes, unidades ou setores",
        ],
        "example": "Responda somente com: 1, 2, 3 ou 4.",
        "weighted": True,
    },
    {
        "id": "processos",
        "title": "🔴 3. Quantidade de processos",
        "question": "Quantos processos precisam ser organizados ou integrados?",
        "options": [
            "1 — Um processo: apenas pipeline",
            "2 — Dois processos: pipeline + propostas",
            "3 — Três processos: pipeline + operação + acompanhamento",
            "4 — Quatro ou mais processos: fluxos completos integrados",
        ],
        "example": "Responda somente com: 1, 2, 3 ou 4.",
        "weighted": True,
    },
    {
        "id": "personalizacao",
        "title": "🟢 4. Nível de personalização",
        "question": "Qual é o nível de personalização necessário para atender o cliente?",
        "options": [
            "1 — Baixa: apenas identidade visual",
            "2 — Média: ajustes de etapas e campos",
            "3 — Alta: regras específicas",
            "4 — Muito alta: fluxos únicos ou complexos",
        ],
        "example": "Responda somente com: 1, 2, 3 ou 4.",
        "weighted": True,
    },
    {
        "id": "volume",
        "title": "🟠 5. Volume operacional",
        "question": "Qual é o volume operacional do cliente?",
        "options": [
            "1 — Baixo: poucos atendimentos ou pedidos",
            "2 — Médio: fluxo diário constante",
            "3 — Alto: grande quantidade diária",
            "4 — Muito alto: operação intensa ou múltiplas equipes",
        ],
        "example": "Responda somente com: 1, 2, 3 ou 4.",
        "weighted": True,
    },
    {
        "id": "impacto",
        "title": "⚫ 6. Impacto do caos operacional",
        "question": "Qual é o impacto atual da desorganização na empresa? Esta é a pergunta mais importante.",
        "options": [
            "1 — Baixo: pequena desorganização",
            "2 — Médio: perda ocasional de clientes",
            "3 — Alto: leads ou pedidos perdidos",
            "4 — Crítico: a empresa perdeu o controle da operação",
        ],
        "example": "Responda somente com: 1, 2, 3 ou 4.",
        "weighted": True,
    },
    {
        "id": "faturamento",
        "title": "💰 7. Faturamento do cliente",
        "question": "Você sabe o faturamento aproximado do cliente?",
        "options": [
            "Informe o faturamento aproximado mensal ou anual, caso saiba.",
            "Caso ainda não tenha essa informação, responda: não informado.",
        ],
        "example": "Exemplos de resposta: R$ 80 mil/mês; R$ 1 milhão/ano; não informado.",
        "weighted": False,
    },
    {
        "id": "resumo_cliente",
        "title": "📝 8. Resumo do cliente e da reunião",
        "question": "Comente sobre o cliente: me envie um resumo da ata de reunião, informe os serviços desejados e descreva os principais problemas apresentados.",
        "options": [
            "Inclua os pontos mais importantes identificados durante a conversa.",
            "Esse resumo será utilizado para indicar a solução Oppi mais adequada e gerar o PDF do diagnóstico.",
        ],
        "example": "Exemplos: precisa organizar o comercial; deseja acompanhar a operação; perde informações entre setores; quer automatizar propostas.",
        "weighted": False,
    },
]

OPPI_PRODUCT_PRICE_TABLE = {
    "Oppi Vision": {
        "pequena": ("R$ 3.000", "R$ 5.000", "1 mês"),
        "media": ("R$ 5.000", "R$ 8.000", "1 mês"),
        "estruturada": ("R$ 8.000", "R$ 15.000", "1 mês"),
    },
    "Oppi Flow": {
        "pequena": ("R$ 4.000", "R$ 7.000", "1 mês"),
        "media": ("R$ 7.000", "R$ 12.000", "1 mês"),
        "estruturada": ("R$ 12.000", "R$ 20.000", "1 mês"),
    },
    "Oppi Track": {
        "pequena": ("R$ 5.000", "R$ 8.000", "1 mês"),
        "media": ("R$ 8.000", "R$ 15.000", "1 mês"),
        "estruturada": ("R$ 15.000", "Sob consulta", "1 mês"),
    },
}

OPPI_ADDITIONAL_SERVICES_TABLE = {
    "Pequeno": {
        "contrato": "Equipe 20 - contratos digitais: R$ 791,00 à vista ou R$ 184,78/mês em até 6x",
        "disparos": "Essencial WhatsApp - até 100 envios: R$ 149,00/mês",
    },
    "Médio": {
        "contrato": "Equipe 80 - contratos digitais: R$ 3.175,00 à vista ou R$ 645,54/mês em até 6x",
        "disparos": "Crescimento WhatsApp - até 200 envios: R$ 249,00/mês",
    },
    "Premium": {
        "contrato": "Equipe 150 - contratos digitais: R$ 3.960,00 à vista ou R$ 771,84/mês em até 6x",
        "disparos": "Profissional WhatsApp - até 300 envios: R$ 349,00/mês",
    },
    "Enterprise": {
        "contrato": "Equipe 150+ - contratos digitais: sob consulta conforme volume",
        "disparos": "Profissional WhatsApp ou pacote personalizado: sob consulta conforme volume",
    },
}


def _pricing_additional_services(profile: str) -> str:
    services = OPPI_ADDITIONAL_SERVICES_TABLE.get(profile, OPPI_ADDITIONAL_SERVICES_TABLE["Médio"])
    return f"{services['contrato']} | {services['disparos']}"


def _pricing_question_text(step: dict) -> str:
    options_text = "\n".join(step["options"])
    return (
        f"{step['title']}\n\n"
        f"{step['question']}\n\n"
        f"{options_text}\n\n"
        f"{step['example']}"
    )

def _diagnostic_initials(company_name: str) -> str:
    words = [word for word in normalize_text(company_name).split() if word]

    if not words:
        return "OP"

    if len(words) == 1:
        return words[0][:2].upper()

    return (words[0][0] + words[1][0]).upper()


def _diagnostic_now() -> str:
    return pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%H:%M")


def _pricing_safe_key_fragment(value: str) -> str:
    """Cria um fragmento de key único e estável."""
    clean = normalize_search_text(value)
    clean = re.sub(r"[^a-z0-9]+", "_", clean).strip("_") or "empresa"
    unique = uuid.uuid5(uuid.NAMESPACE_DNS, normalize_text(value)).hex[:10]
    return f"{clean[:42]}_{unique}"


def _pricing_extract_explicit_option(answer: str) -> Optional[int]:
    normalized = normalize_search_text(answer)
    patterns = [
        r"^(?:opcao|opção|peso)?\s*([1-4])(?:\s|$|[-—:])",
        r"\b(?:opcao|opção|peso)\s*([1-4])\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, normalized)

        if match:
            return int(match.group(1))

    return None


def _pricing_extract_numbers(answer: str) -> list[int]:
    return [int(value) for value in re.findall(r"\d+", normalize_text(answer))]


def _pricing_weight_from_answer(step_id: str, answer: str) -> Optional[int]:
    explicit_option = _pricing_extract_explicit_option(answer)

    if explicit_option:
        return explicit_option

    normalized = normalize_search_text(answer)
    numbers = _pricing_extract_numbers(answer)

    if step_id == "colaboradores" and numbers:
        collaborators = numbers[0]

        if collaborators <= 5:
            return 1
        if collaborators <= 15:
            return 2
        if collaborators <= 30:
            return 3
        return 4

    if step_id == "setores":
        if any(term in normalized for term in ["multi", "unidade", "varios setores", "vários setores", "complexa"]):
            return 4
        if "operacao" in normalized or "pos-venda" in normalized or "pós-venda" in normalized:
            return 3
        if "atendimento" in normalized and "comercial" in normalized:
            return 2
        if any(term in normalized for term in ["um setor", "1 setor", "apenas um", "simples"]):
            return 1

    if step_id == "processos":
        if numbers:
            quantity = numbers[0]
            return 4 if quantity >= 4 else max(1, quantity)
        if any(term in normalized for term in ["fluxos completos", "integrados", "quatro", "4+"]):
            return 4
        if "acompanhamento" in normalized or "operacao" in normalized:
            return 3
        if "proposta" in normalized:
            return 2
        if "pipeline" in normalized:
            return 1

    if step_id == "personalizacao":
        if "muito alta" in normalized or "complex" in normalized or "unico" in normalized or "único" in normalized:
            return 4
        if "alta" in normalized or "regra" in normalized:
            return 3
        if "media" in normalized or "média" in normalized or "etapa" in normalized or "campo" in normalized:
            return 2
        if "baixa" in normalized or "visual" in normalized:
            return 1

    if step_id == "volume":
        if "muito alto" in normalized or "intensa" in normalized or "multi" in normalized:
            return 4
        if "alto" in normalized or "grande" in normalized:
            return 3
        if "medio" in normalized or "médio" in normalized or "constante" in normalized:
            return 2
        if "baixo" in normalized or "pouco" in normalized:
            return 1

    if step_id == "impacto":
        if "critico" in normalized or "crítico" in normalized or "perdeu controle" in normalized:
            return 4
        if "alto" in normalized or "lead" in normalized or "pedido" in normalized:
            return 3
        if "medio" in normalized or "médio" in normalized or "ocasional" in normalized:
            return 2
        if "baixo" in normalized or "pequena" in normalized:
            return 1

    return None


def _pricing_profile(total_score: int) -> str:
    if total_score <= 10:
        return "Pequeno"
    if total_score <= 15:
        return "Médio"
    if total_score <= 20:
        return "Premium"
    return "Enterprise"


def _pricing_product(answer_map: dict) -> str:
    combined_text = normalize_search_text(
        " | ".join(normalize_text(item.get("answer")) for item in answer_map.values())
    )
    sectors_weight = int(answer_map.get("setores", {}).get("weight") or 0)
    processes_weight = int(answer_map.get("processos", {}).get("weight") or 0)
    volume_weight = int(answer_map.get("volume", {}).get("weight") or 0)

    operational_terms = [
        "operacao",
        "operacional",
        "pos-venda",
        "pós-venda",
        "pedido",
        "acompanhamento",
        "multi equipe",
        "multiequipe",
        "unidade",
    ]

    if any(term in combined_text for term in operational_terms) or sectors_weight >= 3 or processes_weight >= 3 or volume_weight >= 4:
        return "Oppi Track"

    commercial_terms = ["comercial", "pipeline", "proposta", "atendimento", "lead"]

    if any(term in combined_text for term in commercial_terms) or sectors_weight >= 2 or processes_weight >= 2:
        return "Oppi Flow"

    return "Oppi Vision"


def _pricing_company_size(answer_map: dict) -> str:
    collaborators_weight = int(answer_map.get("colaboradores", {}).get("weight") or 1)

    if collaborators_weight <= 1:
        return "pequena"
    if collaborators_weight == 2:
        return "media"
    return "estruturada"


def _pricing_result_message(company_name: str) -> str:
    answer_map = _diagnostic_get_answers().get(company_name, {})
    weights = [
        int(answer_map.get(step["id"], {}).get("weight") or 0)
        for step in OPPI_PRICING_STEPS
        if step["weighted"]
    ]
    total_score = sum(weights)
    profile = _pricing_profile(total_score)
    product = _pricing_product(answer_map)
    company_size = _pricing_company_size(answer_map)
    price_from, price_to, ideal_term = OPPI_PRODUCT_PRICE_TABLE[product][company_size]
    ideal_term = "1 mês"
    additional_services = _pricing_additional_services(profile)
    revenue = normalize_text(answer_map.get("faturamento", {}).get("answer")) or "Não informado"
    meeting_summary = normalize_text(answer_map.get("resumo_cliente", {}).get("answer")) or "Não informado"

    if price_to == "Sob consulta":
        pricing_text = f"a partir de {price_from}"
    else:
        pricing_text = f"entre {price_from} e {price_to}"

    weights_text = " + ".join(str(weight) for weight in weights)

    return (
        "✅ Diagnóstico concluído.\n\n"
        f"Soma dos pesos: {weights_text} = {total_score}.\n"
        f"Perfil do projeto: {profile}.\n"
        f"Solução sugerida: {product}.\n"
        f"Prazo ideal: {ideal_term}.\n"
        f"Faturamento informado: {revenue}.\n"
        f"Serviços adicionais sugeridos: {additional_services}.\n\n"
        f"Resumo registrado: {meeting_summary}\n\n"
        f"Pelo que vi aqui, o valor ficaria {pricing_text}. Quanto você deseja gerar a proposta?\n\n"
        "Exemplos de resposta: R$ 8.500; R$ 10.000; R$ 12.000; sob consulta. "
        "Depois de confirmar o valor, utilize o botão Gerar PDF do diagnóstico."
    )



def _pricing_pdf_safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_text(value))
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized).strip("_")
    return normalized or "cliente"


def _pricing_get_confirmed_value(company_name: str) -> str:
    answer_map = _diagnostic_get_answers().get(company_name, {})
    return normalize_text(answer_map.get("valor_proposta", {}).get("answer"))


def _pricing_report_summary(company_name: str) -> dict:
    answer_map = _diagnostic_get_answers().get(company_name, {})
    weights = [
        int(answer_map.get(step["id"], {}).get("weight") or 0)
        for step in OPPI_PRICING_STEPS
        if step["weighted"]
    ]
    total_score = sum(weights)
    profile = _pricing_profile(total_score)
    product = _pricing_product(answer_map)
    company_size = _pricing_company_size(answer_map)
    price_from, price_to, ideal_term = OPPI_PRODUCT_PRICE_TABLE[product][company_size]
    ideal_term = "1 mês"

    if price_to == "Sob consulta":
        suggested_price = f"A partir de {price_from}"
    else:
        suggested_price = f"{price_from} a {price_to}"

    return {
        "answer_map": answer_map,
        "weights": weights,
        "total_score": total_score,
        "profile": profile,
        "product": product,
        "company_size": company_size,
        "suggested_price": suggested_price,
        "ideal_term": ideal_term,
        "additional_services": _pricing_additional_services(profile),
        "confirmed_value": _pricing_get_confirmed_value(company_name) or "Não informado",
    }


def _pricing_company_registration_data(df: pd.DataFrame, columns: dict, company_name: str) -> list[tuple[str, str]]:
    selected_rows = df[df["_empresa"].astype(str) == normalize_text(company_name)].copy()

    if selected_rows.empty:
        return [
            ("Nome da empresa", normalize_text(company_name) or "Não informado"),
            ("Telefone", "Não informado"),
            ("CNPJ", "Não informado"),
            ("Endereço", "Não informado"),
        ]

    if "_sheet_row" in selected_rows.columns:
        selected_rows = selected_rows.sort_values("_sheet_row", ascending=False)

    row = selected_rows.iloc[0]

    def value(column_key: str, fallback: str = "Não informado") -> str:
        column_name = columns.get(column_key)
        raw_value = normalize_text(row.get(column_name, "")) if column_name else ""
        return raw_value or fallback

    return [
        ("Nome da empresa", value("empresa", normalize_text(company_name) or "Não informado")),
        ("Telefone", value("telefone_b2b")),
        ("CNPJ", value("cnpj")),
        ("Endereço", value("endereco")),
    ]

def _pricing_answer_label_for_pdf(step: dict, answer_data: dict) -> str:
    """Mostra no PDF a opção completa escolhida, não apenas o número digitado."""
    answer = normalize_text(answer_data.get("answer"))

    if not step.get("weighted"):
        return answer or "Não informado"

    weight = answer_data.get("weight")

    try:
        weight_int = int(weight)
    except Exception:
        weight_int = _pricing_weight_from_answer(step.get("id", ""), answer) or 0

    if weight_int:
        for option in step.get("options", []):
            option_text = normalize_text(option)
            if re.match(rf"^\s*{weight_int}\s*[—-]", option_text):
                return option_text

        return f"Peso {weight_int}"

    return answer or "Não informado"


COMMERCIAL_SERVICE_OPTIONS = ["Oppi Vision", "Oppi Flow", "Oppi Track"]


def get_colaborador_options() -> list[str]:
    step = next(item for item in OPPI_PRICING_STEPS if item["id"] == "colaboradores")
    return list(step.get("options", []))


def format_colaboradores_label(answer_data: dict | str) -> str:
    if isinstance(answer_data, str):
        answer_data = {"answer": answer_data, "weight": None}

    step = next(item for item in OPPI_PRICING_STEPS if item["id"] == "colaboradores")
    return _pricing_answer_label_for_pdf(step, answer_data or {})


def format_proposal_value_display(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return "Não informado"
    if "consulta" in normalize_search_text(text):
        return text
    if text.upper().startswith("R$"):
        return text
    amount = parse_money(text)
    if amount > 0:
        return format_money(amount)
    return text


def row_get(row, key, default=""):
    if row is None:
        return default
    try:
        if key not in row.index:
            return default
    except Exception:
        return default
    return coerce_scalar(row.get(key, default))


def normalize_company_row(row):
    if row is None:
        return None
    if not isinstance(row, pd.Series):
        return row
    if row.index.is_unique:
        return row
    data = {}
    for key in row.index:
        if key not in data:
            data[key] = coerce_scalar(row[key])
    return pd.Series(data, dtype=object)


def row_field_value(row, columns: dict, key: str) -> str:
    if row is None:
        return ""
    column_name = columns.get(key)
    if not column_name:
        return ""
    return normalize_text(row_get(row, column_name, ""))


def row_contact_email(row, columns: dict) -> str:
    for key in ("email", "email_socio_1"):
        value = row_field_value(row, columns, key)
        if normalize_text(value):
            return value
    return ""


def row_contact_phone(row, columns: dict) -> str:
    for key in ("telefone_b2b", "telefone_socio_1", "telefone_fixo", "telefone_alternativo"):
        value = row_field_value(row, columns, key)
        if normalize_text(value):
            return value
    return ""


def build_client_commercial_summary(row, columns: dict, pricing_answers: dict | None = None) -> dict:
    def sheet_value(key: str) -> str:
        return row_field_value(row, columns, key)

    servico = normalize_text(sheet_value("servico"))
    valor = normalize_text(sheet_value("valor_proposta"))
    colaboradores = normalize_text(sheet_value("colaboradores"))

    if pricing_answers:
        weighted_steps = [step for step in OPPI_PRICING_STEPS if step["weighted"]]
        if not servico and all(pricing_answers.get(step["id"], {}).get("weight") for step in weighted_steps):
            servico = _pricing_product(pricing_answers)
        if not colaboradores and pricing_answers.get("colaboradores"):
            colaboradores = format_colaboradores_label(pricing_answers["colaboradores"])
        if not valor and pricing_answers.get("valor_proposta"):
            valor = normalize_text(pricing_answers["valor_proposta"].get("answer"))

    return {
        "servico": servico or "Não informado",
        "valor_proposta": format_proposal_value_display(valor),
        "valor_proposta_num": parse_money(valor),
        "colaboradores": colaboradores or "Não informado",
        "has_data": bool(servico or valor or colaboradores),
    }


def find_prepared_company_row(company_name: str, df: pd.DataFrame):
    clean = normalize_text(company_name)
    if df.empty or not clean or "_empresa" not in df.columns:
        return None

    normalized_names = df["_empresa"].astype(str).apply(normalize_text)
    exact = df[normalized_names == clean].copy()
    if not exact.empty:
        if "_sheet_row" in exact.columns:
            exact = exact.sort_values("_sheet_row", ascending=False)
        return normalize_company_row(exact.iloc[0])

    matches = []
    for _, row in df.iterrows():
        company_text = normalize_text(row_get(row, "_empresa", ""))
        if not company_text:
            continue
        if flexible_search_match(clean, company_text):
            matches.append(row)

    if not matches:
        return None

    matches_df = pd.DataFrame(matches)
    if "_sheet_row" in matches_df.columns:
        matches_df = matches_df.sort_values("_sheet_row", ascending=False)
    return normalize_company_row(matches_df.iloc[0])


def resolve_company_name(company_name: str, df: pd.DataFrame) -> str:
    row = find_prepared_company_row(company_name, df)
    if row is not None:
        return normalize_text(row_get(row, "_empresa", company_name)) or normalize_text(company_name)
    return normalize_text(company_name)


def find_company_sheet_row(company_name: str) -> int | None:
    df = load_sheet_data()
    if df.empty:
        return None

    columns = identify_columns(df)
    prepared = prepare_data(df, columns)
    row = find_prepared_company_row(company_name, prepared)

    if row is None:
        return None

    return int(row["_sheet_row"])


def update_company_commercial_fields(sheet_row: int, payload: dict) -> None:
    client = get_gsheet_client()
    spreadsheet = client.open_by_key(settings.sheet_id)
    worksheet = _open_worksheet(spreadsheet, settings.worksheet_name)
    headers = ensure_registration_sheet_columns(worksheet)

    if not headers:
        raise RuntimeError("A primeira linha da planilha precisa conter os cabeçalhos.")

    current_row = worksheet.row_values(int(sheet_row))
    row_values = list(current_row) + [""] * max(0, len(headers) - len(current_row))
    row_values = row_values[:len(headers)]

    _apply_commercial_fields(row_values, headers, payload)

    changed_cells = []
    for column_index, new_value in enumerate(row_values, start=1):
        old_value = current_row[column_index - 1] if column_index - 1 < len(current_row) else ""
        if normalize_text(old_value) != normalize_text(new_value):
            changed_cells.append(gspread.Cell(int(sheet_row), column_index, normalize_text(new_value)))

    if changed_cells:
        worksheet.update_cells(changed_cells, value_input_option="USER_ENTERED")
        invalidate_sheet_cache()


def sync_pricing_answers_to_sheet(company_name: str, answer_map: dict) -> None:
    if not answer_map:
        return

    sheet_row = find_company_sheet_row(company_name)
    if sheet_row is None:
        return

    payload: dict[str, str] = {}

    if answer_map.get("colaboradores"):
        payload["colaboradores"] = format_colaboradores_label(answer_map["colaboradores"])

    weighted_steps = [step for step in OPPI_PRICING_STEPS if step["weighted"]]
    if all(answer_map.get(step["id"], {}).get("weight") for step in weighted_steps):
        payload["servico"] = _pricing_product(answer_map)

    valor_answer = normalize_text(answer_map.get("valor_proposta", {}).get("answer"))
    if valor_answer:
        payload["valor_proposta"] = valor_answer

    if not payload:
        return

    try:
        update_company_commercial_fields(sheet_row, payload)
    except Exception:
        return


def _pricing_generate_pdf(company_name: str, df: pd.DataFrame, columns: dict) -> bytes:
    """Gera um PDF de diagnóstico com os dados cadastrais e todas as respostas do vendedor."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception as error:
        raise RuntimeError(
            "A biblioteca reportlab não está instalada. Adicione a linha reportlab no requirements.txt, salve e faça o deploy novamente."
        ) from error

    report = _pricing_report_summary(company_name)
    answer_map = report["answer_map"]
    registration_rows = _pricing_company_registration_data(df, columns, company_name)
    proposal_number = f"OPPI-{pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
    buffer = io.BytesIO()

    page_width, page_height = A4
    margin = 16 * mm

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=f"Diagnóstico comercial - {company_name}",
        author="Oppi Comercial",
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="OppiTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#FFFFFF"),
        alignment=TA_LEFT,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="OppiSubtitle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#E8DDF4"),
    ))
    styles.add(ParagraphStyle(
        name="OppiSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#FFFFFF"),
    ))
    styles.add(ParagraphStyle(
        name="OppiLabel",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#271B35"),
    ))
    styles.add(ParagraphStyle(
        name="OppiValue",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#2B2237"),
    ))
    styles.add(ParagraphStyle(
        name="OppiProposalText",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.4,
        leading=12,
        textColor=colors.HexColor("#2B2237"),
        spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="OppiProposalBold",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8.8,
        leading=12,
        textColor=colors.HexColor("#271B35"),
        spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="OppiSmall",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#5D5368"),
    ))
    styles.add(ParagraphStyle(
        name="OppiCenter",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#FFFFFF"),
    ))

    story = []

    header = Table([
        [Paragraph("OPPI COMERCIAL", styles["OppiSubtitle"])],
        [Paragraph("Diagnóstico de precificação", styles["OppiTitle"])],
        [Paragraph(f"Gerado em: {pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')}", styles["OppiSubtitle"])],
        [Paragraph(f"Número da proposta: {html.escape(proposal_number)}", styles["OppiSubtitle"])],
    ], colWidths=[page_width - (2 * margin) - 10])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#160C2D")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#FF4BAA")),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, 0), 11),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 11),
    ]))
    story.append(header)
    story.append(Spacer(1, 10))

    registration_header = Table([[Paragraph("DADOS CADASTRAIS DA EMPRESA", styles["OppiSection"]) ]], colWidths=[176 * mm])
    registration_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#3B174D")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#FF4BAA")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(registration_header)

    registration_data = []
    for label, value in registration_rows:
        registration_data.append([
            Paragraph(html.escape(normalize_text(label)), styles["OppiLabel"]),
            Paragraph(html.escape(normalize_text(value)), styles["OppiValue"]),
        ])

    registration_table = Table(registration_data, colWidths=[48 * mm, 128 * mm], repeatRows=0)
    registration_table.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F4F1F8")]),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#E24AA8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8CBE6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([registration_table, Spacer(1, 10)])

    story.append(Spacer(1, 10))

    def _proposal_header(title: str):
        section = Table([[Paragraph(html.escape(title), styles["OppiSection"])]], colWidths=[176 * mm])
        section.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#3B174D")),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#FF4BAA")),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        return section

    def _proposal_box(paragraphs: list[str]):
        data = [[Paragraph(paragraph, styles["OppiProposalText"])] for paragraph in paragraphs if normalize_text(paragraph)]
        box = Table(data, colWidths=[176 * mm])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#E24AA8")),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return box

    def _proposal_section(title: str, paragraphs: list[str]):
        story.append(_proposal_header(title))
        story.append(_proposal_box(paragraphs))
        story.append(Spacer(1, 8))

    selected_product = normalize_text(report["product"]) or "Oppi"
    selected_profile = normalize_text(report["profile"]) or "Não informado"
    selected_price = normalize_text(report["confirmed_value"]) or "Não informado"
    selected_range = normalize_text(report["suggested_price"]) or "Não informado"
    selected_term = "1 mês"
    selected_additional_services = normalize_text(report["additional_services"]) or "Contratos digitais e disparos pelo WhatsApp, conforme necessidade."

    solution_focus = {
        "Oppi Vision": "acompanhamento estratégico da operação, gestão da equipe e dashboards de performance.",
        "Oppi Flow": "organização do fluxo comercial, pipeline, propostas e acompanhamento das etapas de atendimento.",
        "Oppi Track": "acompanhamento operacional, execução dos processos internos e controle das etapas da operação.",
    }.get(selected_product, "organização operacional, automação de processos e acompanhamento visual da operação.")

    _proposal_section("1. CONTEXTO IDENTIFICADO", [
        "Após análise inicial da operação da empresa, identificamos oportunidades relacionadas à organização operacional, acompanhamento da equipe e centralização das informações internas.",
        "Atualmente, muitos processos ainda podem depender de controles manuais, dificultando o acompanhamento em tempo real da operação e reduzindo a previsibilidade dos resultados.",
        "Nosso objetivo é estruturar uma operação mais organizada, acompanhável e automatizada, proporcionando maior controle operacional e melhor acompanhamento dos processos internos.",
        f"Com base nas respostas registradas, o perfil identificado foi <b>{html.escape(selected_profile)}</b> e a solução mais adequada inicialmente é <b>{html.escape(selected_product)}</b>, com foco em {html.escape(solution_focus)}",
    ])

    _proposal_section("2. PRINCIPAIS DESAFIOS IDENTIFICADOS", [
        "Durante a análise inicial, foram considerados desafios como:",
        "- processos realizados manualmente;",
        "- informações descentralizadas;",
        "- dificuldade no acompanhamento operacional;",
        "- falta de visibilidade da equipe;",
        "- ausência de fluxo estruturado;",
        "- retrabalho operacional;",
        "- perda de acompanhamento de clientes e processos.",
        f"<b>Resumo registrado pelo vendedor:</b> {html.escape(normalize_text(answer_map.get('resumo_cliente', {}).get('answer')) or 'Não informado')}",
    ])

    _proposal_section("3. SOLUÇÃO PROPOSTA — ECOSSISTEMA OPPI", [
        "A OPPI desenvolve soluções voltadas à organização operacional, automação de processos e acompanhamento estratégico da operação.",
        "Conforme a necessidade da empresa, a solução poderá envolver:",
        "<b>A) OPPI VISION — Gestão & Performance</b><br/>Sistema voltado ao acompanhamento estratégico da operação e gestão da equipe.<br/>Funcionalidades: dashboards estratégicos; indicadores operacionais; acompanhamento da equipe; análise de produtividade; centralização de informações; gestão visual da operação.",
        "<b>B) OPPI FLOW — Pipeline & Propostas</b><br/>Sistema voltado ao gerenciamento comercial e fluxo operacional de atendimento.<br/>Funcionalidades: pipeline operacional; geração de propostas; acompanhamento de etapas; histórico operacional; gestão visual do fluxo; acompanhamento comercial.",
        "<b>C) OPPI TRACK — Operação & Execução</b><br/>Sistema voltado ao acompanhamento operacional e execução dos processos internos.<br/>Funcionalidades: acompanhamento operacional; organização de etapas; gestão de responsáveis; controle de execução; acompanhamento de status; centralização operacional.",
        f"<b>Solução indicada para este diagnóstico:</b> {html.escape(selected_product)}.",
    ])

    _proposal_section("4. COMO FUNCIONA A OPERAÇÃO", [
        "Fluxo operacional simplificado:",
        "<b>Cliente → Atendimento → Processo → Automação → Dashboard → Acompanhamento → Gestão Operacional</b>",
        "A proposta da OPPI não é apenas implementar tecnologia, mas estruturar um fluxo operacional mais inteligente, organizado e acompanhável para a empresa.",
    ])

    _proposal_section("5. BENEFÍCIOS OPERACIONAIS", [
        "Com a implantação da solução OPPI, a empresa terá:",
        "- maior organização operacional;",
        "- centralização das informações;",
        "- redução de retrabalho;",
        "- acompanhamento da equipe em tempo real;",
        "- redução de perda de informações;",
        "- maior previsibilidade operacional;",
        "- acompanhamento estratégico da operação;",
        "- melhoria no fluxo interno de trabalho.",
    ])

    _proposal_section("6. EXEMPLO DE TRANSFORMAÇÃO OPERACIONAL", [
        "<b>Antes da OPPI</b><br/>- processos espalhados;<br/>- equipe sem acompanhamento visual;<br/>- controles manuais;<br/>- dificuldade na gestão operacional;<br/>- falta de visibilidade dos processos.",
        "<b>Depois da OPPI</b><br/>- operação centralizada;<br/>- acompanhamento em tempo real;<br/>- dashboards operacionais;<br/>- automações integradas;<br/>- maior controle e previsibilidade.",
    ])

    _proposal_section("7. IMPLANTAÇÃO", [
        "A implantação contempla:",
        "- análise operacional inicial;",
        "- estruturação do fluxo;",
        "- configuração da solução;",
        "- parametrização das etapas;",
        "- automações operacionais;",
        "- treinamento inicial da equipe;",
        "- acompanhamento de implantação;",
        "- suporte operacional inicial.",
    ])

    _proposal_section("8. PRAZO ESTIMADO", [
        f"Prazo ideal estimado: <b>{html.escape(selected_term)}</b>.",
        "O prazo poderá variar conforme: complexidade da operação; quantidade de usuários; quantidade de setores envolvidos; disponibilidade das informações; e nível de personalização definido no projeto.",
    ])

    _proposal_section("9. INVESTIMENTO", [
        f"Implantação sugerida pelo diagnóstico: <b>{html.escape(selected_range)}</b>.",
        "Forma de pagamento: a definir em proposta comercial ou contrato.",
        f"Serviços adicionais sugeridos: <b>{html.escape(selected_additional_services)}</b>.",
        "Os serviços adicionais podem envolver contratos digitais, propostas, documentos operacionais e pacotes de disparos pelo WhatsApp, conforme o cenário identificado.",
    ])

    _proposal_section("10. SUPORTE E ACOMPANHAMENTO", [
        "O suporte contempla:",
        "- acompanhamento operacional inicial;",
        "- suporte remoto;",
        "- ajustes simples;",
        "- orientações de utilização;",
        "- acompanhamento estratégico inicial.",
        "Horário de atendimento: Segunda a sexta-feira — 08h às 18h.",
    ])

    _proposal_section("11. DIFERENCIAL OPPI", [
        "A OPPI não atua apenas como fornecedora de tecnologia.",
        "Nosso foco é estruturar operações mais organizadas, previsíveis e acompanháveis, utilizando automação, gestão visual e inteligência operacional para reduzir o caos operacional e melhorar o controle interno da empresa.",
    ])

    _proposal_section("12. CONSIDERAÇÕES FINAIS", [
        "Agradecemos pela oportunidade de apresentar nossa proposta comercial.",
        "Estamos à disposição para alinhamentos, demonstrações e esclarecimentos adicionais.",
    ])

    footer = Table([[Paragraph(
        "Documento gerado automaticamente pelo Dashboard Oppi Comercial.",
        styles["OppiSmall"],
    )]], colWidths=[176 * mm])
    footer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F2FA")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D8CBE6")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(footer)

    doc.build(story)
    return buffer.getvalue()

def _diagnostic_ensure_thread(company_name: str) -> list[dict]:
    threads = _diagnostic_get_threads()
    progress = _diagnostic_get_progress()
    answers = _diagnostic_get_answers()

    if company_name not in threads:
        threads[company_name] = [
            {
                "role": "assistant",
                "content": OPPI_PRICING_INTRO,
                "time": _diagnostic_now(),
            },
            {
                "role": "assistant",
                "content": _pricing_question_text(OPPI_PRICING_STEPS[0]),
                "time": _diagnostic_now(),
            },
        ]
        progress[company_name] = 0
        answers[company_name] = {}

    return threads[company_name]


def _diagnostic_add_answer(company_name: str, answer: str) -> None:
    messages = _diagnostic_ensure_thread(company_name)
    progress = _diagnostic_get_progress()
    answers = _diagnostic_get_answers()
    clean_answer = normalize_text(answer)

    if not clean_answer:
        return

    current_index = int(progress.get(company_name, 0))

    if current_index >= len(OPPI_PRICING_STEPS):
        messages.append(
            {
                "role": "user",
                "content": clean_answer,
                "time": _diagnostic_now(),
            }
        )
        answers.setdefault(company_name, {})["valor_proposta"] = {
            "answer": clean_answer,
            "weight": None,
        }
        sync_pricing_answers_to_sheet(company_name, answers.get(company_name, {}))
        messages.append(
            {
                "role": "assistant",
                "content": (
                    "Perfeito. O valor desejado para a proposta foi confirmado e registrado nesta conversa.\n\n"
                    "Agora clique no botão Gerar PDF do diagnóstico para baixar o documento com os dados do cadastro, "
                    "as respostas da precificação, o resumo da reunião, a solução indicada e o valor confirmado."
                ),
                "time": _diagnostic_now(),
            }
        )
        return

    step = OPPI_PRICING_STEPS[current_index]
    weight = None

    if step["weighted"]:
        weight = _pricing_weight_from_answer(step["id"], clean_answer)

        if weight is None:
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "Não consegui identificar o peso dessa resposta. Responda com uma opção de 1 a 4 para continuarmos.\n\n"
                        f"{step['example']}"
                    ),
                    "time": _diagnostic_now(),
                }
            )
            return

    messages.append(
        {
            "role": "user",
            "content": clean_answer,
            "time": _diagnostic_now(),
        }
    )

    answers.setdefault(company_name, {})[step["id"]] = {
        "answer": clean_answer,
        "weight": weight,
    }

    next_index = current_index + 1
    progress[company_name] = next_index

    if next_index < len(OPPI_PRICING_STEPS):
        messages.append(
            {
                "role": "assistant",
                "content": _pricing_question_text(OPPI_PRICING_STEPS[next_index]),
                "time": _diagnostic_now(),
            }
        )
        return

    messages.append(
        {
            "role": "assistant",
            "content": _pricing_result_message(company_name),
            "time": _diagnostic_now(),
        }
    )
    sync_pricing_answers_to_sheet(company_name, answers.get(company_name, {}))


def _diagnostic_reset(company_name: str) -> None:
    threads = _diagnostic_get_threads()
    progress = _diagnostic_get_progress()
    answers = _diagnostic_get_answers()
    threads.pop(company_name, None)
    progress.pop(company_name, None)
    answers.pop(company_name, None)
    _diagnostic_ensure_thread(company_name)


def _diagnostic_render_messages(messages: list[dict]) -> str:
    rows = ['<div class="oppi-chat-messages">', '<div class="oppi-chat-day"><span>Hoje</span></div>']

    for message in messages:
        role = "user" if message.get("role") == "user" else "assistant"
        safe_content = html.escape(normalize_text(message.get("content"))).replace("\n", "<br>")
        safe_time = html.escape(normalize_text(message.get("time")))
        check = " ✓✓" if role == "user" else ""

        rows.append(
            f'<div class="oppi-chat-message-row {role}">'
            f'<div class="oppi-chat-bubble">{safe_content}'
            f'<span class="oppi-chat-bubble-time">{safe_time}{check}</span>'
            f'</div></div>'
        )

    rows.append("</div>")
    return "".join(rows)
