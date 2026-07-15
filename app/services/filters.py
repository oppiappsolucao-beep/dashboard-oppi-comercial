"""Filtros e transformações de dados do dashboard."""
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

import pandas as pd

from app.services.legacy_core import (
    STATUS_OPTIONS,
    apply_period_filter,
    as_datetime_series,
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
    if "_data_chamado" in df.columns:
        valid_dates = as_datetime_series(df["_data_chamado"]).dropna()
    else:
        valid_dates = pd.Series(dtype="datetime64[ns]")
    has_reference_dates = not valid_dates.empty

    if has_reference_dates:
        date_min = valid_dates.min().date()
        date_max = valid_dates.max().date()
    else:
        date_min = None
        date_max = None

    seller_options = sorted(
        s for s in df["_vendedor"].dropna().astype(str).unique().tolist()
        if normalize_text(s) and normalize_text(s) != "Sem vendedor"
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
        "has_reference_dates": has_reference_dates,
        "seller_options": seller_options,
        "niche_options": niche_options,
        "state_options": state_options,
        "status_options": STATUS_OPTIONS,
        "total_companies": int(df["_empresa"].apply(lambda v: normalize_text(v) != "").sum()) if not df.empty else 0,
        "total_capital": float(df["_capital_num"].sum()) if not df.empty and "_capital_num" in df.columns else 0.0,
    }


def apply_default_period_filters(filters: DashboardFilters, df: pd.DataFrame) -> DashboardFilters:
    """Preenche o período padrão com base nas datas reais da planilha."""
    if filters.period_start and filters.period_end:
        return filters

    options = get_filter_options(df)
    if not options["has_reference_dates"]:
        return replace(filters, period_start=None, period_end=None)

    return replace(
        filters,
        period_start=filters.period_start or options["date_min"],
        period_end=filters.period_end or options["date_max"],
    )


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


def parse_dashboard_filters(request, form: dict | None = None) -> DashboardFilters:
    data = form or {}
    period_start = data.get("period_start") or request.query_params.get("period_start")
    period_end = data.get("period_end") or request.query_params.get("period_end")

    def to_date(value):
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None

    return DashboardFilters(
        seller=data.get("seller") or request.query_params.get("seller", "Todos os vendedores"),
        status=data.get("status") or request.query_params.get("status", "Todos os status"),
        period_start=to_date(period_start),
        period_end=to_date(period_end),
        niche=data.get("niche") or request.query_params.get("niche", "Todos os nichos"),
        state=data.get("state") or request.query_params.get("state", "Todos os estados"),
        search=data.get("search") or request.query_params.get("search", ""),
        selected_card_status=data.get("selected_card_status") or request.query_params.get("selected_card_status"),
    )
