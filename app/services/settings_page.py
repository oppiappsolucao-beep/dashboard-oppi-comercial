"""Configurações — usuários, serviços, permissões e integrações."""
import os
import re
from datetime import date, datetime, timedelta

import pandas as pd

from app.config import settings
from app.services.legacy_core import as_python_datetime

SETTINGS_TABS = [
    ("geral", "Geral"),
    ("usuarios", "Usuários e Permissões"),
    ("servicos", "Serviços"),
    ("integracoes", "Integrações"),
    ("faturamento", "Faturamento"),
]

ROLE_OPTIONS = ["Administrador", "Gerente", "Vendedor", "Analista"]

MODULE_SERVICES = [
    ("CRM Comercial", "ativo"),
    ("Automação WhatsApp", "ativo"),
    ("Propostas com IA", "ativo"),
    ("Dashboard Gerencial", "ativo"),
    ("Integração n8n", "teste"),
    ("Relatórios Financeiros", "ativo"),
    ("Funil de Vendas", "ativo"),
    ("Cadastro de Leads", "ativo"),
]

PERMISSIONS = [
    ("view_leads", "Ver leads"),
    ("edit_pipeline", "Editar pipeline"),
    ("create_proposals", "Criar propostas"),
    ("generate_pdf_ai", "Gerar PDF com IA"),
    ("access_financial", "Acessar financeiro"),
    ("manage_users", "Gerenciar usuários"),
]

DEFAULT_PERMISSIONS = {
    "view_leads": True,
    "edit_pipeline": True,
    "create_proposals": True,
    "generate_pdf_ai": True,
    "access_financial": False,
    "manage_users": False,
}


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _slug_email(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", normalize_name(name).lower()).strip(".")
    return f"{slug or 'usuario'}@oppitech.com.br"


def normalize_name(name: str) -> str:
    return str(name or "").strip()


def _as_datetime(value) -> datetime | None:
    return as_python_datetime(value)


def _format_last_access(value) -> str:
    dt = _as_datetime(value)
    if not dt:
        return "—"
    return dt.strftime("%d/%m/%Y %H:%M")


def _integration_status(env_keys: list[str]) -> tuple[str, str, bool]:
    connected = any(os.getenv(key, "").strip() for key in env_keys)
    if connected or (env_keys == ["GCP_SERVICE_ACCOUNT_B64", "GOOGLE_SERVICE_ACCOUNT_B64"] and settings.sheets_configured):
        return "Conectado", "connected", True
    return "Desconectado", "disconnected", False


def build_integrations() -> list[dict]:
    items = [
        ("WhatsApp Business", ["WHATSAPP_API_URL", "WHATSAPP_API_TOKEN"]),
        ("Google Sheets", ["GCP_SERVICE_ACCOUNT_B64", "GOOGLE_SERVICE_ACCOUNT_B64"]),
        ("n8n", ["N8N_WEBHOOK_URL"]),
        ("Asaas", ["ASAAS_API_KEY"]),
        ("ZapSign", ["ZAPSIGN_API_TOKEN"]),
    ]
    rows = []
    for name, env_keys in items:
        label, css, connected = _integration_status(env_keys)
        rows.append({
            "name": name,
            "status_label": label,
            "status_class": css,
            "connected": connected,
        })
    return rows


def _build_users_from_sheet(df: pd.DataFrame, app_username: str) -> list[dict]:
    users = []
    seen = set()

    admin_name = normalize_name(app_username) or "Administrador"
    users.append({
        "name": admin_name,
        "email": _slug_email(admin_name),
        "role": "Administrador",
        "role_class": "admin",
        "status_label": "Ativo",
        "status_class": "active",
        "last_access": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "initials": _initials(admin_name),
    })
    seen.add(admin_name.lower())

    if df.empty:
        return users

    for seller in sorted(df["_vendedor"].dropna().astype(str).unique().tolist()):
        seller_name = normalize_name(seller)
        if not seller_name or seller_name.lower() in seen or seller_name == "Sem vendedor":
            continue

        seller_rows = df[df["_vendedor"] == seller]
        last_dates = seller_rows["_ultima_atualizacao"].tolist() + seller_rows["_data_chamado"].tolist()
        last_dt = None
        for value in last_dates:
            dt = _as_datetime(value)
            if dt and (last_dt is None or dt > last_dt):
                last_dt = dt

        recent = last_dt and (datetime.now() - last_dt).days <= 30
        users.append({
            "name": seller_name,
            "email": _slug_email(seller_name),
            "role": "Vendedor",
            "role_class": "seller",
            "status_label": "Ativo" if recent else "Pendente",
            "status_class": "active" if recent else "pending",
            "last_access": _format_last_access(last_dt),
            "initials": _initials(seller_name),
        })
        seen.add(seller_name.lower())

    return users


def _month_trend(current: int, previous: int) -> dict:
    if previous == 0:
        pct = 100 if current > 0 else 0
    else:
        pct = round(((current - previous) / previous) * 100)
    if pct == 0:
        return {"trend_label": "— Sem alterações", "trend_up": True, "trend_flat": True}
    sign = "+" if pct >= 0 else "-"
    return {
        "trend_label": f"{sign} {abs(pct)}% vs mês anterior",
        "trend_up": pct >= 0,
        "trend_flat": False,
    }


def _count_companies_in_month(df: pd.DataFrame, reference: date) -> int:
    if df.empty or "_data_chamado" not in df.columns:
        return 0
    month_start = reference.replace(day=1)
    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1)
    count = 0
    for value in df["_data_chamado"].tolist():
        dt = _as_datetime(value)
        if not dt:
            continue
        day = dt.date()
        if month_start <= day < month_end:
            count += 1
    return count


def _count_active_sellers_in_month(df: pd.DataFrame, reference: date) -> int:
    if df.empty:
        return 0
    month_start = reference.replace(day=1)
    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1)

    active = set()
    for _, row in df.iterrows():
        seller = normalize_name(row.get("_vendedor", ""))
        if not seller or seller == "Sem vendedor":
            continue
        for column in ("_ultima_atualizacao", "_data_chamado", "_data_abertura"):
            dt = _as_datetime(row.get(column))
            if dt and month_start <= dt.date() < month_end:
                active.add(seller.lower())
                break
    return len(active)


def build_settings_kpi_cards(df: pd.DataFrame, integrations: list[dict]) -> list[dict]:
    users = _build_users_from_sheet(df, settings.app_username)
    active_users = len([user for user in users if user["status_class"] == "active"])
    active_integrations = len([item for item in integrations if item["connected"]])
    services_count = len(MODULE_SERVICES)
    companies_count = int(df["_empresa"].apply(lambda v: normalize_name(v) != "").sum()) if not df.empty else 0
    total_capital = float(df["_capital_num"].sum()) if not df.empty and "_capital_num" in df.columns else 0.0

    today = date.today()
    prev_month = today.replace(day=1) - timedelta(days=1)
    companies_this_month = _count_companies_in_month(df, today)
    companies_prev_month = _count_companies_in_month(df, prev_month)
    sellers_prev_month = _count_active_sellers_in_month(df, prev_month)
    active_users_prev = max(1, 1 + sellers_prev_month)

    renewal = (date.today().replace(day=1) + timedelta(days=32)).replace(day=18)
    if renewal < date.today():
        renewal = renewal.replace(month=renewal.month + 1 if renewal.month < 12 else 1)

    return [
        {
            "label": "Usuários ativos",
            "value": active_users,
            "icon": "👥",
            "tone": "purple",
            **_month_trend(active_users, active_users_prev if sellers_prev_month else max(active_users - 1, 1)),
        },
        {
            "label": "Empresas cadastradas",
            "value": companies_count,
            "icon": "🏢",
            "tone": "blue",
            **_month_trend(companies_this_month or companies_count, companies_prev_month or max(companies_count - 1, 0)),
        },
        {
            "label": "Capital monitorado",
            "value": f"R$ {total_capital:,.0f}".replace(",", "."),
            "icon": "💰",
            "tone": "pink",
            "trend_label": f"{companies_count} empresa{'s' if companies_count != 1 else ''} na planilha",
            "trend_up": True,
            "trend_flat": companies_count == 0,
        },
        {
            "label": "Integrações ativas",
            "value": active_integrations,
            "icon": "🔗",
            "tone": "green",
            **_month_trend(active_integrations, max(active_integrations - 1, 0)),
        },
        {
            "label": "Plano atual",
            "value": "Professional",
            "icon": "⭐",
            "tone": "orange",
            "trend_label": f"Renovação em {renewal.strftime('%d/%m/%Y')}",
            "trend_up": True,
            "trend_flat": True,
            "is_period": True,
        },
    ]


def build_services_list() -> list[dict]:
    status_map = {
        "ativo": ("Ativo", "active"),
        "teste": ("Em teste", "test"),
        "opcional": ("Opcional", "optional"),
    }
    rows = []
    for name, status_key in MODULE_SERVICES:
        label, css = status_map.get(status_key, ("Ativo", "active"))
        rows.append({"name": name, "status_label": label, "status_class": css})
    return rows


def build_permissions(session_permissions: dict | None) -> list[dict]:
    merged = {**DEFAULT_PERMISSIONS, **(session_permissions or {})}
    rows = []
    for key, label in PERMISSIONS:
        rows.append({
            "key": key,
            "label": label,
            "enabled": bool(merged.get(key)),
        })
    return rows


def build_company_profile(df: pd.DataFrame | None = None) -> dict:
    renewal = (date.today().replace(day=1) + timedelta(days=32)).replace(day=18)
    companies_count = 0
    total_capital = 0.0
    if df is not None and not df.empty:
        companies_count = int(df["_empresa"].apply(lambda v: normalize_name(v) != "").sum())
        total_capital = float(df["_capital_num"].sum()) if "_capital_num" in df.columns else 0.0

    return {
        "company_name": os.getenv("COMPANY_NAME", "Oppi Comercial LTDA"),
        "trade_name": os.getenv("COMPANY_TRADE_NAME", "Oppi CRM"),
        "cnpj": os.getenv("COMPANY_CNPJ", "00.000.000/0001-00"),
        "plan_name": "Professional",
        "user_limit": int(os.getenv("USER_LIMIT", "20")),
        "billing_cycle": "Mensal",
        "renewal_date": renewal.strftime("%d/%m/%Y"),
        "worksheet_name": settings.worksheet_name,
        "sheet_id": settings.sheet_id,
        "sheet_configured": settings.sheets_configured,
        "companies_count": companies_count,
        "total_capital_label": f"R$ {total_capital:,.0f}".replace(",", "."),
    }


def apply_users_view(users: list[dict], search: str = "", role: str = "Todos os perfis") -> list[dict]:
    result = users
    if search:
        term = search.lower()
        result = [
            user for user in result
            if term in user["name"].lower() or term in user["email"].lower()
        ]
    if role and role != "Todos os perfis":
        result = [user for user in result if user["role"] == role]
    return result


def build_users_table(
    df: pd.DataFrame,
    app_username: str,
    search: str = "",
    role: str = "Todos os perfis",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    users = apply_users_view(_build_users_from_sheet(df, app_username), search, role)
    total = len(users)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    rows = users[start : start + per_page]

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "from_record": start + 1 if total else 0,
        "to_record": min(start + per_page, total),
    }
