"""Filtros e transformações de dados do dashboard."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from app.services.legacy_core import (
    STATUS_OPTIONS,
    apply_period_filter,
    flexible_search_match,
    normalize_text,
    normalize_search_text,
    row_matches_status_filter,
)


@dataclass
class DashboardFilters:
    seller: str = "Todos os vendedores"
    status: str = "Todos os status"
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    niche: str = "Todos os nichos"
    state: str = "Todos os estados"
    search: str = ""
    selected_card_status: Optional[str] = None


def get_filter_options(df: pd.DataFrame) -> dict:
    valid_dates = df["_data_chamado"].dropna()
    if valid_dates.empty:
        date_max = date.today()
        date_min = date_max - timedelta(days=30)
    else:
        date_min = valid_dates.min().date()
        date_max = valid_dates.max().date()

    seller_options = sorted(
        s for s in df["_vendedor"].dropna().astype(str).unique().tolist()
        if normalize_text(s)
    )
    niche_options = sorted(
        {n for n in df["_nicho"].dropna().astype(str).unique().tolist() if normalize_text(n)},
        key=normalize_search_text,
    )
    state_options = sorted(
        {s for s in df["_estado"].dropna().astype(str).unique().tolist() if normalize_text(s)},
        key=lambda v: (v == "Não identificado", v),
    )

    return {
        "date_min": date_min,
        "date_max": date_max,
        "seller_options": seller_options,
        "niche_options": niche_options,
        "state_options": state_options,
        "status_options": STATUS_OPTIONS,
    }


def apply_dashboard_filters(
    df: pd.DataFrame,
    columns: dict,
    filters: DashboardFilters,
) -> pd.DataFrame:
    search_term = normalize_text(filters.search)

    if search_term:
        searchable_column_keys = [
            "empresa", "telefone_b2b", "telefone_fixo", "telefone_alternativo",
            "cnpj", "endereco", "email", "site", "socio_1", "socio_2", "socio_3",
        ]

        def row_matches_search(row) -> bool:
            searchable_values = [
                normalize_text(row.get("_empresa", "")),
                normalize_text(row.get("_telefone", "")),
            ]
            for column_key in searchable_column_keys:
                column_name = columns.get(column_key)
                if column_name and column_name in row.index:
                    searchable_values.append(normalize_text(row.get(column_name, "")))
            return flexible_search_match(search_term, " | ".join(searchable_values))

        searched_df = df[df.apply(row_matches_search, axis=1)].copy()
        return searched_df[searched_df["_empresa"].apply(lambda v: normalize_text(v) != "")].copy()

    filtered_df = df.copy()

    if filters.seller != "Todos os vendedores":
        filtered_df = filtered_df[filtered_df["_vendedor"] == filters.seller].copy()

    if filters.status != "Todos os status":
        filtered_df = filtered_df[
            filtered_df.apply(lambda row: row_matches_status_filter(row, filters.status), axis=1)
        ].copy()

    if filters.niche != "Todos os nichos":
        filtered_df = filtered_df[filtered_df["_nicho"] == filters.niche].copy()

    if filters.state != "Todos os estados":
        filtered_df = filtered_df[filtered_df["_estado"] == filters.state].copy()

    if filters.period_start and filters.period_end:
        period = (filters.period_start, filters.period_end)
        filtered_df = apply_period_filter(filtered_df, "_data_chamado", period)

    return filtered_df
