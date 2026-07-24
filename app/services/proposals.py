"""Propostas — KPIs, tabela e chat derivados da planilha comercial."""
import html
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode

import pandas as pd

from app.services.filters import DashboardFilters, apply_dashboard_filters
from app.services.legacy_core import apply_period_filter, as_python_date, find_prepared_company_row, identify_columns, normalize_search_text, normalize_text, parse_money, resolve_company_name, row_contact_email, status_group, deal_value_from_row
from app.services.proposal_pdf import prepare_generated_proposal_pdf, proposal_pdf_filename

PROPOSAL_ROW_STATUSES = {"Proposta", "Fechado", "Conversando", "Reunião"}

PROPOSAL_STATUS_OPTIONS = [
    "Todos os status",
    "Aguardando resposta",
    "Em revisão",
    "Enviada",
    "Aprovada",
    "Em negociação",
]

STATUS_FILTER_MAP = {
    "Aguardando resposta": "aguardando",
    "Em revisão": "revisao",
    "Enviada": "enviada",
    "Aprovada": "aprovada",
    "Em negociação": "negociacao",
}


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _as_date(value) -> date | None:
    return as_python_date(value)


def _format_money(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number <= 0:
        return "—"
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _format_money_short(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number <= 0:
        return "R$ 0"
    formatted = f"{number:,.0f}".replace(",", ".")
    return f"R$ {formatted}"


def _month_bounds(reference: date) -> tuple[date, date]:
    start = reference.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1, day=1)
    else:
        next_month = start.replace(month=start.month + 1, day=1)
    end = next_month - timedelta(days=1)
    return start, end


def _previous_month_bounds(reference: date) -> tuple[date, date]:
    current_start, _ = _month_bounds(reference)
    prev_end = current_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    return prev_start, prev_end


def _month_trend(current: int | float, previous: int | float) -> dict:
    if previous == 0:
        pct = 100 if current > 0 else 0
    else:
        pct = round(((current - previous) / previous) * 100)
    return {
        "trend_pct": abs(pct),
        "trend_up": pct >= 0,
        "trend_flat": pct == 0,
    }


def _filter_by_period(df: pd.DataFrame, period: tuple[date, date]) -> pd.DataFrame:
    return apply_period_filter(df.copy(), "_data_chamado", period)


def _proposal_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[
        df.apply(
            lambda row: status_group(row.get("_status_grupo") or row.get("_status_original", ""))
            in PROPOSAL_ROW_STATUSES,
            axis=1,
        )
    ].copy()


def _map_proposal_status(grouped: str, activity_date: date | None, today: date) -> tuple[str, str]:
    if grouped == "Fechado":
        return "Aprovada", "aprovada"
    if grouped == "Conversando":
        return "Em revisão", "revisao"
    if grouped == "Reunião":
        return "Em negociação", "negociacao"
    if grouped == "Proposta":
        if activity_date and (today - activity_date).days <= 7:
            return "Enviada", "enviada"
        return "Aguardando resposta", "aguardando"
    return "Em negociação", "negociacao"


def _row_to_proposal(row, index: int, today: date) -> dict:
    grouped = status_group(row.get("_status_grupo") or row.get("_status_original", ""))
    activity_date = _as_date(row.get("_ultima_atualizacao") or row.get("_data_chamado"))
    status_label, status_class = _map_proposal_status(grouped, activity_date, today)
    empresa = row.get("_empresa") or "—"
    vencimento = (activity_date + timedelta(days=15)) if activity_date else today + timedelta(days=15)
    sheet_row = int(row.get("_sheet_row", 0) or 0)
    code_number = sheet_row - 1 if sheet_row else index + 1

    return {
        "code": f"PROP-{code_number:03d}",
        "empresa": empresa,
        "empresa_initials": _initials(empresa),
        "vendedor": row.get("_vendedor") or "Sem vendedor",
        "vendedor_initials": _initials(row.get("_vendedor", "")),
        "valor": _format_money(deal_value_from_row(row)),
        "valor_num": deal_value_from_row(row),
        "status_label": status_label,
        "status_class": status_class,
        "vencimento": vencimento.strftime("%d/%m/%Y"),
        "vencimento_date": vencimento,
        "activity_date": activity_date or date.min,
        "sheet_row": sheet_row,
        "grouped_status": grouped,
    }


def build_proposals_list(filtered_df: pd.DataFrame) -> list[dict]:
    today = date.today()
    proposal_df = _proposal_rows(filtered_df)
    if proposal_df.empty:
        return []

    proposal_df = proposal_df.sort_values(
        ["_data_chamado", "_empresa"],
        ascending=[False, True],
    )
    return [
        _row_to_proposal(row, index, today)
        for index, (_, row) in enumerate(proposal_df.iterrows())
    ]


def _filter_statuses(dataframe: pd.DataFrame, statuses: set[str]) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe
    mask = dataframe.apply(
        lambda row: status_group(row.get("_status_grupo") or "") in statuses,
        axis=1,
    )
    return dataframe[mask]


def build_proposals_kpi_cards(df: pd.DataFrame, columns: dict, filters: DashboardFilters) -> list[dict]:
    today = date.today()
    month_start, month_end = _month_bounds(today)
    prev_start, prev_end = _previous_month_bounds(today)

    base_df = apply_dashboard_filters(df, columns, replace(filters, period_start=None, period_end=None))
    current_month = _filter_by_period(_proposal_rows(base_df), (month_start, month_end))
    previous_month = _filter_by_period(_proposal_rows(base_df), (prev_start, prev_end))

    def count_status(dataframe, statuses):
        if dataframe.empty:
            return 0
        return int(
            dataframe.apply(
                lambda row: status_group(row.get("_status_grupo") or "") in statuses,
                axis=1,
            ).sum()
        )

    def sum_value(dataframe):
        if dataframe.empty:
            return 0.0
        return float(sum(deal_value_from_row(row) for _, row in dataframe.iterrows()))

    created_current = count_status(current_month, {"Proposta", "Fechado", "Conversando", "Reunião"})
    created_prev = count_status(previous_month, {"Proposta", "Fechado", "Conversando", "Reunião"})
    negotiation_current = count_status(current_month, {"Conversando", "Reunião", "Proposta"})
    negotiation_prev = count_status(previous_month, {"Conversando", "Reunião", "Proposta"})
    waiting_current = count_status(current_month, {"Proposta"})
    waiting_prev = count_status(previous_month, {"Proposta"})
    approved_current = count_status(current_month, {"Fechado"})
    approved_prev = count_status(previous_month, {"Fechado"})
    value_current = sum_value(_filter_statuses(current_month, {"Proposta", "Conversando", "Reunião"}))
    value_prev = sum_value(_filter_statuses(previous_month, {"Proposta", "Conversando", "Reunião"}))

    cards = [
        ("Propostas criadas", created_current, created_prev, "📅", "purple"),
        ("Em negociação", negotiation_current, negotiation_prev, "👤", "orange"),
        ("Aguardando resposta", waiting_current, waiting_prev, "💬", "pink"),
        ("Aprovadas no mês", approved_current, approved_prev, "✓", "green"),
        ("Valor total em propostas", value_current, value_prev, "💰", "blue", True),
    ]

    result = []
    for item in cards:
        is_money = len(item) == 6 and item[5]
        if is_money:
            label, current, previous, icon, tone, _ = item
            display_value = _format_money_short(current)
        else:
            label, current, previous, icon, tone = item
            display_value = current

        result.append({
            "label": label,
            "value": display_value,
            "icon": icon,
            "tone": tone,
            **_month_trend(current, previous),
        })
    return result


def apply_proposals_view(
    proposals: list[dict],
    status_filter: str,
) -> list[dict]:
    if not status_filter or status_filter == "Todos os status":
        return proposals
    target = STATUS_FILTER_MAP.get(status_filter)
    if not target:
        return proposals
    return [item for item in proposals if item["status_class"] == target]


def build_proposals_table(
    filtered_df: pd.DataFrame,
    status_filter: str = "Todos os status",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    proposals = build_proposals_list(filtered_df)
    view = apply_proposals_view(proposals, status_filter)
    total = len(view)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    rows = view[start : start + per_page]

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "from_record": start + 1 if total else 0,
        "to_record": min(start + per_page, total),
    }


def _extract_company(message: str, df: pd.DataFrame) -> str | None:
    normalized_message = normalize_search_text(message)
    if not normalized_message:
        return None

    matches = []
    for company in df["_empresa"].dropna().astype(str).unique().tolist():
        company_text = normalize_text(company)
        if not company_text:
            continue
        if normalize_search_text(company_text) in normalized_message:
            matches.append(company_text)

    if matches:
        return max(matches, key=len)

    patterns = [
        r"proposta(?:\s+comercial)?\s+(?:para|p/)\s+(.+?)(?:\.\s|,|\s+valor|\s+com\s|\s+r\$|\s+no\s+valor|$)",
        r"(?:para|p/)\s+(.+?)(?:\.\s|,|\s+valor|\s+com\s|\s+r\$|\s+no\s+valor|$)",
        r"empresa\s+(.+?)(?:\.\s|,|\s+valor|\s+com\s|\s+r\$|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            candidate = normalize_text(match.group(1))
            if len(candidate) >= 3:
                return candidate

    return None


def _company_row(company_name: str, df: pd.DataFrame):
    return find_prepared_company_row(company_name, df)


def _company_email(company_name: str, df: pd.DataFrame, columns: dict) -> str:
    row = _company_row(company_name, df)
    if row is None:
        row = _company_row(resolve_company_name(company_name, df), df)
    return row_contact_email(row, columns)


def _proposal_pdf_query(
    value: str | None = None,
    servico: str | None = None,
    colaboradores: str | None = None,
    services_description: str | None = None,
) -> str:
    params = {}
    if value:
        params["valor"] = value
    if servico:
        params["servico"] = servico
    if colaboradores:
        params["colaboradores"] = colaboradores
    if services_description:
        params["servicos"] = services_description
    return f"?{urlencode(params)}" if params else ""


def build_generated_proposal(
    company: str,
    value: str | None,
    df: pd.DataFrame,
    columns: dict,
    *,
    servico: str | None = None,
    colaboradores: str | None = None,
    services_description: str | None = None,
    plans_text: str | None = None,
) -> dict:
    company = resolve_company_name(company, df)
    encoded_company = quote(company)
    # PDF usa sessão + cache; evita URL gigante com a descrição dos serviços
    query = _proposal_pdf_query(value, servico, colaboradores, None)
    preview_query = f"{query}&inline=1" if query else "?inline=1"
    value_label = ""
    if value:
        amount = parse_money(value)
        if amount > 0:
            value_label = f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    client_email = _company_email(company, df, columns)
    pdf_cache_key, pdf_error = prepare_generated_proposal_pdf(
        company,
        df,
        columns,
        value=value,
        servico=servico,
        colaboradores=colaboradores,
        services_description=services_description,
        plans_text=plans_text,
    )
    subject = quote(f"Proposta comercial - {company}")
    body = quote(
        "Prezado(a),\n\n"
        "Segue em anexo a nossa proposta comercial"
        + (f", no valor de {value_label}" if value_label else "")
        + f", referente à empresa {company}.\n\n"
        "Baixe o PDF pelo dashboard e anexe a este e-mail antes de enviar.\n\n"
        "Atenciosamente,\n"
        "Equipe Oppi Comercial"
    )
    email_href = f"mailto:{quote(client_email)}?subject={subject}&body={body}" if client_email else f"mailto:?subject={subject}&body={body}"

    return {
        "company": company,
        "filename": proposal_pdf_filename(company),
        "value": value,
        "servico": servico or "",
        "colaboradores": colaboradores or "",
        "services_description": services_description or "",
        "plans_text": plans_text or "",
        "value_label": value_label,
        "servico_label": normalize_text(servico) or "",
        "colaboradores_label": normalize_text(colaboradores) or "",
        "client_email": client_email or "Não informado na planilha",
        "pdf_cache_key": pdf_cache_key,
        "pdf_error": pdf_error or "",
        "pdf_ready": not bool(pdf_error),
        "preview_url": f"/propostas/{encoded_company}/pdf{preview_query}",
        "download_url": f"/propostas/{encoded_company}/pdf{query}",
        "email_href": email_href,
    }


def get_generated_proposal(request) -> dict | None:
    data = request.session.get("proposals_generated")
    return data if isinstance(data, dict) else None


def clear_generated_proposal(request) -> None:
    request.session.pop("proposals_generated", None)


def strip_proposal_pdf_cards(chat_messages: list[dict]) -> list[dict]:
    return [message for message in chat_messages if message.get("type") != "pdf_card"]


def _extract_colaboradores(message: str) -> str | None:
    match = re.search(r"colaboradores:\s*(.+)$", message, flags=re.IGNORECASE)
    if match:
        return normalize_text(match.group(1)).rstrip(".")
    return None


def _extract_servico(message: str) -> str | None:
    match = re.search(
        r"servi[çc]o:\s*(.+?)(?:\.\s+(?:valor|colaboradores)|$)",
        message,
        flags=re.IGNORECASE,
    )
    if match:
        return normalize_text(match.group(1)).rstrip(".")
    return None


def _extract_value(message: str) -> str | None:
    patterns = [
        r"R\$\s*([\d.,]+)",
        r"valor\s*(?:de\s*)?R\$\s*([\d.,]+)",
        r"valor\s*(?:de\s*)?([\d.,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1).replace(".", "").replace(",", ".")
    return None


def _now_time() -> str:
    return datetime.now().strftime("%H:%M")


def _is_affirmative(text: str) -> bool:
    clean = normalize_text(text).lower()
    keys = (
        "sim", "pode", "gerar", "confirmo", "confirma", "ok", "okay", "fechado",
        "pode gerar", "gerar proposta", "pode gerar a proposta", "isso", "perfeito",
        "pode sim", "vamos", "segue",
    )
    return any(clean == key or clean.startswith(key + " ") or clean.endswith(" " + key) for key in keys) or clean in keys


def _wants_change(text: str) -> bool:
    clean = normalize_text(text).lower()
    return any(
        token in clean
        for token in ("alterar", "mudar", "trocar", "outro valor", "quero mudar", "desejo alterar")
    )


def _extract_money_value(text: str) -> float | None:
    from app.services.legacy_core import parse_money

    clean = normalize_text(text)
    match = re.search(r"R\$\s*([\d.]+,?\d*)", clean, flags=re.IGNORECASE)
    if match:
        amount = parse_money(match.group(1))
        return amount if amount > 0 else None
    # número puro (ex.: 199,90 ou 200)
    match = re.search(r"^\s*([\d.]+,?\d*)\s*$", clean.replace(" ", ""))
    if match and ("," in match.group(1) or "." in match.group(1) or len(match.group(1)) >= 2):
        amount = parse_money(match.group(1))
        # evita confundir "25" colaboradores com valor — só se tiver vírgula/ponto ou for claramente valor
        if "," in match.group(1) or "." in match.group(1):
            return amount if amount > 0 else None
        if amount >= 40:  # valores de plano costumam ser >= 40
            return amount
    return None


def _draft_pricing_message(pricing, company: str) -> str:
    from app.services.proposal_pricing import format_money_br, per_employee_rate

    n = pricing.cadastro_collaborators or pricing.included_collaborators
    per = per_employee_rate(pricing)
    return (
        f"Para **{company}** com **{n} colaboradores**, a sugestão é:\n\n"
        f"• Faixa do plano: até **{pricing.included_collaborators}** colaboradores\n"
        f"• Valor sugerido (boleto): **{format_money_br(pricing.boleto_monthly)}/mês**\n"
        f"• Equivalente a **{format_money_br(per)} por colaborador/mês**\n"
        f"• Recorrente: **{format_money_br(pricing.recorrente_monthly)}/mês**\n"
        f"• Anual: **{format_money_br(pricing.anual_upfront)}** à vista\n\n"
        "Posso gerar a proposta com esse valor ou deseja alterar?"
    )


def _finalize_proposal(
    *,
    company: str,
    df: pd.DataFrame,
    columns: dict,
    pricing,
    chat_messages: list[dict],
) -> tuple[list[dict], dict | None, str | None, dict | None]:
    from app.services.proposal_pricing import format_money_br

    n = pricing.cadastro_collaborators or pricing.included_collaborators
    description = (
        f"Ponto Eletrônico Oppi — plano até {pricing.included_collaborators} colaboradores "
        f"({n} informados). Boleto: R$ {pricing.boleto_monthly:.2f}/mês."
    )
    value = str(pricing.boleto_monthly)
    generated = build_generated_proposal(
        company,
        value,
        df,
        columns,
        servico=pricing.product_label,
        colaboradores=str(n),
        services_description=description,
        plans_text=pricing.plans_block,
    )
    if generated.get("pdf_error"):
        footer = f"\n\nNão foi possível gerar o PDF.\n{generated['pdf_error']}"
    else:
        footer = "\n\nO PDF já está disponível ao lado."
    chat_messages.append({
        "role": "assistant",
        "content": (
            f"Proposta gerada para **{company}**.\n\n"
            f"Colaboradores: **{n}**\n"
            f"Valor (boleto): **{format_money_br(pricing.boleto_monthly)}/mês**"
            f"{footer}"
        ),
        "time": _now_time(),
    })
    if generated.get("pdf_ready"):
        chat_messages.append({
            "role": "assistant",
            "type": "pdf_card",
            "company": company,
            "filename": generated["filename"],
            "value": value,
            "generated": generated,
            "time": _now_time(),
        })
    return chat_messages, generated, None, None


def handle_proposal_chat_message(
    message: str,
    df: pd.DataFrame,
    chat_messages: list[dict],
    columns: dict | None = None,
    *,
    servico: str | None = None,
    colaboradores: str | None = None,
    company_override: str | None = None,
    pending_company: str | None = None,
    services_description: str | None = None,
    draft: dict | None = None,
) -> tuple[list[dict], dict | None, str | None, dict | None]:
    """
    Retorna (mensagens, proposta_gerada|None, pending_company|None, draft|None).

    Fluxo:
    1) Seleciona empresa
    2) Pergunta quantidade de colaboradores
    3) Calcula valor por funcionário e sugere
    4) Confirma ou altera o valor → gera PDF local
    """
    from app.services.proposal_pricing import (
        parse_collaborators_count,
        pricing_with_boleto_override,
        suggest_pricing_for_collaborators,
    )
    from app.services.proposal_commercial_pdf import collect_client_data

    clean_message = normalize_text(message)
    company_pick = normalize_text(company_override) or None
    draft = dict(draft or {})
    step = normalize_text(draft.get("step")) or ""

    if columns is None:
        columns = identify_columns(df)

    # —— Etapa 1: escolher empresa ——
    if company_pick and (not step or step == "pick_company") and (
        not clean_message
        or clean_message.lower().startswith("selecionei a empresa")
        or clean_message.lower().startswith("empresa selecionada")
    ):
        company = resolve_company_name(company_pick, df)
        if not company:
            chat_messages.append({
                "role": "assistant",
                "content": "Não encontrei essa empresa no cadastro. Selecione um cliente da lista.",
                "time": _now_time(),
            })
            return chat_messages, None, None, None

        client = collect_client_data(company, df, columns)
        known = parse_collaborators_count(client.get("colaboradores") or colaboradores)
        chat_messages.append({
            "role": "user",
            "content": f"Empresa selecionada: {company}",
            "time": _now_time(),
        })
        hint = ""
        if known:
            hint = f"\nNo cadastro constam **{known}** colaboradores — confirme ou informe outro número."
        chat_messages.append({
            "role": "assistant",
            "content": (
                f"Perfeito. Vou usar o cadastro de **{company}**.\n\n"
                f"**Quantos colaboradores a empresa tem?**{hint}"
            ),
            "time": _now_time(),
        })
        return chat_messages, None, company, {
            "company": company,
            "step": "ask_collaborators",
            "known_collaborators": known,
        }

    if not clean_message and not draft:
        return chat_messages, None, pending_company, draft or None

    if clean_message:
        chat_messages.append({"role": "user", "content": clean_message, "time": _now_time()})

    company = resolve_company_name(draft.get("company") or pending_company or company_pick or "", df)
    if not company and clean_message:
        extracted = _extract_company(clean_message, df)
        company = resolve_company_name(extracted, df) if extracted else None

    if not company:
        chat_messages.append({
            "role": "assistant",
            "content": "Selecione a empresa do cadastro no formulário ao lado para começarmos.",
            "time": _now_time(),
        })
        return chat_messages, None, pending_company, None

    # —— Etapa 2: quantidade de colaboradores ——
    if step in ("", "ask_collaborators", "pick_company") or not step:
        count = parse_collaborators_count(clean_message) or parse_collaborators_count(colaboradores)
        if not count and draft.get("known_collaborators") and _is_affirmative(clean_message):
            count = int(draft["known_collaborators"])
        if not count:
            chat_messages.append({
                "role": "assistant",
                "content": (
                    f"Para **{company}**, me diga só o número de colaboradores "
                    "(ex.: **25**)."
                ),
                "time": _now_time(),
            })
            return chat_messages, None, company, {
                "company": company,
                "step": "ask_collaborators",
                "known_collaborators": draft.get("known_collaborators"),
            }

        pricing = suggest_pricing_for_collaborators(count)
        chat_messages.append({
            "role": "assistant",
            "content": _draft_pricing_message(pricing, company),
            "time": _now_time(),
        })
        return chat_messages, None, company, {
            "company": company,
            "step": "confirm_value",
            "collaborators": count,
            "suggested_boleto": pricing.boleto_monthly,
            "included": pricing.included_collaborators,
        }

    # —— Etapa 3: confirmar ou alterar valor ——
    if step == "confirm_value":
        count = int(draft.get("collaborators") or 0) or 10
        suggested = float(draft.get("suggested_boleto") or 0)

        if _wants_change(clean_message):
            money = _extract_money_value(clean_message)
            if money:
                pricing = pricing_with_boleto_override(count, money)
                return _finalize_proposal(
                    company=company,
                    df=df,
                    columns=columns,
                    pricing=pricing,
                    chat_messages=chat_messages,
                )
            chat_messages.append({
                "role": "assistant",
                "content": (
                    "Sem problema. Qual valor mensal (boleto) deseja usar?\n"
                    "Ex.: **199,90**"
                ),
                "time": _now_time(),
            })
            return chat_messages, None, company, {
                **draft,
                "step": "await_custom_value",
            }

        if _is_affirmative(clean_message):
            pricing = suggest_pricing_for_collaborators(count)
            if suggested > 0:
                pricing = pricing_with_boleto_override(count, suggested)
            return _finalize_proposal(
                company=company,
                df=df,
                columns=columns,
                pricing=pricing,
                chat_messages=chat_messages,
            )

        money = _extract_money_value(clean_message)
        if money:
            pricing = pricing_with_boleto_override(count, money)
            from app.services.proposal_pricing import format_money_br

            chat_messages.append({
                "role": "assistant",
                "content": (
                    f"Valor atualizado para **{format_money_br(money)}/mês**. "
                    "Posso gerar a proposta com esse valor?"
                ),
                "time": _now_time(),
            })
            return chat_messages, None, company, {
                **draft,
                "step": "confirm_value",
                "suggested_boleto": money,
            }

        chat_messages.append({
            "role": "assistant",
            "content": (
                "Responda **sim** para gerar com o valor sugerido, "
                "ou **alterar** / informe o novo valor (ex.: 189,90)."
            ),
            "time": _now_time(),
        })
        return chat_messages, None, company, draft

    if step == "await_custom_value":
        count = int(draft.get("collaborators") or 0) or 10
        money = _extract_money_value(clean_message)
        if not money:
            chat_messages.append({
                "role": "assistant",
                "content": "Informe o valor mensal desejado, por exemplo **209,90**.",
                "time": _now_time(),
            })
            return chat_messages, None, company, draft
        pricing = pricing_with_boleto_override(count, money)
        return _finalize_proposal(
            company=company,
            df=df,
            columns=columns,
            pricing=pricing,
            chat_messages=chat_messages,
        )

    # fallback: reinicia pergunta de colaboradores
    chat_messages.append({
        "role": "assistant",
        "content": f"**Quantos colaboradores a empresa {company} tem?**",
        "time": _now_time(),
    })
    return chat_messages, None, company, {
        "company": company,
        "step": "ask_collaborators",
    }


def render_proposal_chat_messages(messages: list[dict]) -> str:
    rows = ['<div class="oppi-chat-messages proposals-chat-messages">', '<div class="oppi-chat-day"><span>Hoje</span></div>']

    for message in messages:
        if message.get("type") == "pdf_card":
            generated = message.get("generated") or {}
            if not generated.get("pdf_ready"):
                continue
            company = html.escape(message.get("company", ""))
            filename = html.escape(message.get("filename", "proposta.pdf"))
            download_url = html.escape(generated.get("download_url", "#"))
            email_href = html.escape(generated.get("email_href", "mailto:"))
            preview_url = html.escape(generated.get("preview_url", "#"))
            rows.append(
                f'<div class="proposal-pdf-card proposal-pdf-card-chat">'
                f'<div class="proposal-pdf-card-top">'
                f'<span class="proposal-pdf-icon">📄</span>'
                f'<div><div class="proposal-pdf-name">{filename}</div>'
                f'<div class="proposal-pdf-meta">{company}</div></div>'
                f'<span class="proposal-pdf-ready">Pronto</span>'
                f'</div>'
                f'<div class="proposal-pdf-actions">'
                f'<a href="{download_url}" class="proposal-pdf-btn primary">⬇ Baixar PDF</a>'
                f'<a href="{email_href}" class="proposal-pdf-btn">✉ Enviar por e-mail</a>'
                f'<a href="{preview_url}" target="_blank" class="proposal-pdf-btn">👁 Abrir</a>'
                f'</div></div>'
            )
            continue

        role = "user" if message.get("role") == "user" else "assistant"
        safe_content = html.escape(normalize_text(message.get("content"))).replace("\n", "<br>")
        safe_content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe_content)
        safe_time = html.escape(normalize_text(message.get("time")))
        check = " ✓✓" if role == "user" else ""
        rows.append(
            f'<div class="oppi-chat-message-row {role}">'
            f'<div class="oppi-chat-bubble">{safe_content}'
            f'<span class="oppi-chat-bubble-time">{safe_time}{check}</span>'
            f"</div></div>"
        )

    rows.append("</div>")
    return "".join(rows)


def should_show_proposal_quick_form(messages: list[dict]) -> bool:
    # Mostra formulário enquanto não houver PDF gerado
    return not any(message.get("type") == "pdf_card" for message in messages)


def build_proposal_company_options(df: pd.DataFrame) -> list[str]:
    if df.empty or "_empresa" not in df.columns:
        return []
    companies = []
    seen = set()
    for raw in df["_empresa"].dropna().astype(str).tolist():
        company = normalize_text(raw)
        if not company or company in seen:
            continue
        seen.add(company)
        companies.append(company)
    return sorted(companies, key=str.casefold)


def build_proposal_form_message(
    empresa: str,
    servico: str = "",
    valor_proposta: str = "",
    colaboradores: str = "",
    services_description: str = "",
) -> str:
    empresa = normalize_text(empresa)
    services_description = normalize_text(services_description)
    if empresa and not services_description:
        return f"Empresa selecionada: {empresa}"
    if empresa and services_description:
        return services_description
    parts = []
    if empresa:
        parts.append(f"Crie uma proposta para {empresa}")
    if normalize_text(servico):
        parts.append(f"Serviço: {normalize_text(servico)}")
    if normalize_text(valor_proposta):
        value = normalize_text(valor_proposta)
        if not value.lower().startswith("r$"):
            value = f"R$ {value}"
        parts.append(f"Valor {value}")
    if normalize_text(colaboradores):
        parts.append(f"Colaboradores: {normalize_text(colaboradores)}")
    if services_description:
        parts.append(services_description)
    return ". ".join(parts) + ("." if parts else "")


def default_proposal_chat_messages() -> list[dict]:
    return [{
        "role": "assistant",
        "content": (
            "Olá! Vou montar a proposta comercial com você.\n\n"
            "1) Selecione a empresa cadastrada.\n"
            "2) Eu pergunto quantos colaboradores a empresa tem.\n"
            "3) Calculo o valor por funcionário e sugiro o preço.\n"
            "4) Você confirma ou altera — e o PDF aparece ao lado."
        ),
        "time": _now_time(),
    }]
