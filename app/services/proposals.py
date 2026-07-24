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
    proposal_snapshot: dict | None = None,
) -> dict:
    company = resolve_company_name(company, df)
    encoded_company = quote(company)
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
        proposal_snapshot=proposal_snapshot,
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
        "proposal_snapshot": proposal_snapshot or {},
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


def _draft_pricing_message(planos, company: str) -> str:
    from app.services.proposal_pricing import PLAN_LABELS, format_money_br, plans_cards_payload

    cards = plans_cards_payload(planos)
    lines = [
        f"Para **{company}** com **{planos.quantidade_total} colaboradores**, estas são as formas de pagamento:",
        "",
    ]
    for card in cards:
        mark = " ★ recomendado" if card["recommended"] else ""
        lines.append(f"**{card['title']}**{mark}")
        lines.append(f"• Incluídos: {card['quantidade_incluida']} · Adicionais: {card['quantidade_adicional']}")
        lines.append(f"• Base: {card['valor_base']} · Adicionais: {card['valor_adicionais']}")
        lines.append(f"• Final: **{card['valor_final']}** ({card['payment']})")
        if card["valor_mensal_equivalente"]:
            lines.append(f"• Mensal equivalente: **{card['valor_mensal_equivalente']}**")
        lines.append("")

    lines.append("**Sugestão da Oppi**")
    lines.append(
        f"Para uma empresa com {planos.quantidade_total} colaboradores, recomendamos o pagamento "
        f"anual à vista no valor de {format_money_br(planos.total_anual)}, equivalente a "
        f"{format_money_br(planos.mensal_equivalente_anual)} por mês. "
        "Essa é a opção com o melhor custo-benefício entre as formas de pagamento disponíveis."
    )
    lines.append("")
    lines.append("Posso gerar a proposta com esse valor ou deseja alterar?")
    lines.append("")
    lines.append(
        "Responda: **sugerido** · **boleto** · **cartão** · **anual** · **alterar** "
        "(ou use os botões do formulário)."
    )
    return "\n".join(lines)


def _summary_message(client: dict, selected, planos) -> str:
    from app.services.proposal_pricing import format_money_br

    lines = [
        "**Resumo para conferência**",
        "",
        f"• Razão social: {client.get('razao_social') or client.get('empresa') or '—'}",
    ]
    if client.get("nome_fantasia"):
        lines.append(f"• Nome fantasia: {client['nome_fantasia']}")
    if client.get("documento"):
        lines.append(f"• CNPJ/CPF: {client['documento']}")
    if client.get("responsavel"):
        lines.append(f"• Responsável: {client['responsavel']}")
    lines.extend(
        [
            f"• Colaboradores: {planos.quantidade_total}",
            f"• Adicionais: {planos.quantidade_adicional}",
            f"• Plano: {selected.plan_label}",
            f"• Forma de pagamento: {selected.payment_label}",
            f"• Valor mensal: {format_money_br(selected.valor_mensal)}",
        ]
    )
    if selected.valor_anual is not None:
        lines.append(f"• Valor anual: {format_money_br(selected.valor_anual)}")
    if selected.valor_mensal_equivalente is not None:
        lines.append(f"• Mensal equivalente: {format_money_br(selected.valor_mensal_equivalente)}")
    if selected.desconto_valor and selected.desconto_valor > 0:
        lines.append(f"• Desconto: {format_money_br(selected.desconto_valor)}")
    lines.append(f"• Valor final: **{format_money_br(selected.valor_final)}**")
    lines.append(f"• Validade: {selected.validade_dias} dias corridos")
    if selected.observacao:
        lines.append(f"• Observação: {selected.observacao}")
    lines.append("")
    lines.append("Confirme com **gerar** ou diga **voltar** para editar.")
    return "\n".join(lines)


def _finalize_proposal(
    *,
    company: str,
    df: pd.DataFrame,
    columns: dict,
    selected,
    chat_messages: list[dict],
    usuario: str = "",
) -> tuple[list[dict], dict | None, str | None, dict | None]:
    from app.services.proposal_commercial_pdf import collect_client_data
    from app.services.proposal_history import save_proposal_history_entry
    from app.services.proposal_pricing import PlanPricing, format_money_br

    planos = selected.planos
    n = planos.quantidade_total
    adapter = PlanPricing(planos)
    description = (
        f"Ponto Eletrônico Oppi — {selected.plan_label} — {n} colaboradores — "
        f"{selected.payment_label}."
    )
    value = str(selected.valor_final)
    snapshot = {
        "colaboradores": n,
        "selected": selected.to_dict(),
        "plan_key": selected.plan_key,
        "validade_dias": selected.validade_dias,
        "observacao": selected.observacao,
        "manual": selected.manual,
        "valor_mensal": str(selected.valor_mensal),
        "valor_anual": str(selected.valor_anual) if selected.valor_anual is not None else "",
        "valor_mensal_equivalente": (
            str(selected.valor_mensal_equivalente) if selected.valor_mensal_equivalente is not None else ""
        ),
        "desconto_valor": str(selected.desconto_valor),
        "desconto_percentual": str(selected.desconto_percentual),
        "valor_final": str(selected.valor_final),
    }
    generated = build_generated_proposal(
        company,
        value,
        df,
        columns,
        servico=adapter.product_label,
        colaboradores=str(n),
        services_description=description,
        plans_text=adapter.plans_block,
        proposal_snapshot=snapshot,
    )
    client = collect_client_data(company, df, columns)
    if generated.get("pdf_ready"):
        save_proposal_history_entry(
            {
                "cliente": company,
                "cnpj_cpf": client.get("documento") or "",
                "colaboradores": n,
                "quantidade_adicional": planos.quantidade_adicional,
                "plano": selected.plan_label,
                "forma_pagamento": selected.payment_label,
                "valor_mensal": format_money_br(selected.valor_mensal),
                "valor_anual": format_money_br(selected.valor_anual) if selected.valor_anual else "",
                "valor_final": format_money_br(selected.valor_final),
                "validade": f"{selected.validade_dias} dias",
                "usuario": usuario,
                "filename": generated.get("filename"),
                "pdf_cache_key": generated.get("pdf_cache_key"),
                "preview_url": generated.get("preview_url"),
                "download_url": generated.get("download_url"),
                "snapshot": snapshot,
            }
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
            f"Plano: **{selected.plan_label}**\n"
            f"Valor final: **{format_money_br(selected.valor_final)}**"
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
    action: str | None = None,
    plan_key: str | None = None,
    manual_fields: dict | None = None,
    usuario: str = "",
) -> tuple[list[dict], dict | None, str | None, dict | None]:
    """
    Fluxo: empresa → colaboradores → planos/sugestão → resumo → PDF.
    """
    from app.services.proposal_commercial_pdf import collect_client_data
    from app.services.proposal_pricing import (
        PLAN_ANUAL,
        PLAN_BOLETO,
        PLAN_CARTAO,
        apply_manual_override,
        calcular_planos_ponto,
        parse_collaborators_count,
        select_plan,
    )

    clean_message = normalize_text(message)
    action = normalize_text(action or "").lower()
    company_pick = normalize_text(company_override) or None
    draft = dict(draft or {})
    step = normalize_text(draft.get("step")) or ""
    manual_fields = dict(manual_fields or {})

    if columns is None:
        columns = identify_columns(df)

    # —— Etapa 1: escolher empresa ——
    if (
        company_pick
        and (not step or step == "pick_company")
        and not parse_collaborators_count(colaboradores)
        and (
            not clean_message
            or clean_message.lower().startswith("selecionei a empresa")
            or clean_message.lower().startswith("empresa selecionada")
            or action in ("", "continuar")
        )
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
        chat_messages.append({
            "role": "user",
            "content": f"Empresa selecionada: {company}",
            "time": _now_time(),
        })
        chat_messages.append({
            "role": "assistant",
            "content": (
                f"Cadastro de **{company}** carregado.\n\n"
                "**Quantos colaboradores a empresa possui?**\n"
                "Informe a quantidade exata (número inteiro maior que zero)."
            ),
            "time": _now_time(),
        })
        return chat_messages, None, company, {
            "company": company,
            "step": "ask_collaborators",
            "client": {
                "razao_social": client.get("razao_social"),
                "nome_fantasia": client.get("nome_fantasia"),
                "documento": client.get("documento"),
                "responsavel": client.get("responsavel"),
            },
        }

    if not clean_message and not draft and not action:
        return chat_messages, None, pending_company, draft or None

    if clean_message and action not in (
        "sugerido", "boleto", "cartao", "anual", "alterar", "gerar", "voltar", "resumo", "salvar_manual"
    ):
        chat_messages.append({"role": "user", "content": clean_message, "time": _now_time()})

    company = resolve_company_name(draft.get("company") or pending_company or company_pick or "", df)
    if not company and clean_message:
        extracted = _extract_company(clean_message, df)
        company = resolve_company_name(extracted, df) if extracted else None

    if not company:
        chat_messages.append({
            "role": "assistant",
            "content": "Selecione a empresa do cadastro no formulário para começarmos.",
            "time": _now_time(),
        })
        return chat_messages, None, pending_company, None

    # —— Etapa 2: quantidade de colaboradores ——
    if step in ("", "ask_collaborators", "pick_company") or step == "ask_collaborators":
        count = parse_collaborators_count(colaboradores) or parse_collaborators_count(clean_message)
        if not count:
            chat_messages.append({
                "role": "assistant",
                "content": (
                    f"**Quantos colaboradores a empresa possui?**\n"
                    f"Informe um número inteiro maior que zero para **{company}**."
                ),
                "time": _now_time(),
            })
            return chat_messages, None, company, {
                "company": company,
                "step": "ask_collaborators",
                "client": draft.get("client") or {},
            }

        planos = calcular_planos_ponto(count)
        chat_messages.append({
            "role": "assistant",
            "content": _draft_pricing_message(planos, company),
            "time": _now_time(),
        })
        return chat_messages, None, company, {
            "company": company,
            "step": "choose_plan",
            "collaborators": count,
            "planos": planos.to_dict(),
            "client": draft.get("client") or {},
        }

    # —— Etapa 3: escolher plano / sugerido / alterar ——
    if step == "choose_plan":
        count = int(draft.get("collaborators") or 0)
        if count <= 0:
            return chat_messages, None, company, {"company": company, "step": "ask_collaborators"}

        planos = calcular_planos_ponto(count)
        chosen = normalize_text(plan_key or action or clean_message).lower()

        if chosen in ("alterar", "manual", "alterar o valor manualmente") or _wants_change(clean_message):
            chat_messages.append({
                "role": "assistant",
                "content": (
                    "Certo. Preencha os campos de alteração manual no formulário "
                    "(forma de pagamento, valores, desconto, validade e observação) e clique em **Continuar**."
                ),
                "time": _now_time(),
            })
            return chat_messages, None, company, {
                **draft,
                "step": "manual_edit",
                "planos": planos.to_dict(),
                "plan_key": planos.plano_recomendado,
            }

        plan_map = {
            "sugerido": planos.plano_recomendado,
            "sugerida": planos.plano_recomendado,
            "gerar proposta com o valor sugerido": planos.plano_recomendado,
            "boleto": PLAN_BOLETO,
            "boleto mensal": PLAN_BOLETO,
            "escolher boleto mensal": PLAN_BOLETO,
            "cartao": PLAN_CARTAO,
            "cartão": PLAN_CARTAO,
            "cartao recorrente": PLAN_CARTAO,
            "escolher cartão recorrente": PLAN_CARTAO,
            "anual": PLAN_ANUAL,
            "anual a vista": PLAN_ANUAL,
            "anual à vista": PLAN_ANUAL,
            "escolher pagamento anual à vista": PLAN_ANUAL,
            "sim": planos.plano_recomendado,
        }
        if _is_affirmative(clean_message) and chosen not in plan_map:
            chosen = "sugerido"
        selected_key = plan_map.get(chosen) or (chosen if chosen in (PLAN_BOLETO, PLAN_CARTAO, PLAN_ANUAL) else None)
        if not selected_key:
            chat_messages.append({
                "role": "assistant",
                "content": (
                    "Escolha uma opção: **sugerido**, **boleto**, **cartão**, **anual** ou **alterar**."
                ),
                "time": _now_time(),
            })
            return chat_messages, None, company, {**draft, "step": "choose_plan", "planos": planos.to_dict()}

        selected = select_plan(planos, selected_key)
        client = collect_client_data(company, df, columns)
        chat_messages.append({
            "role": "assistant",
            "content": _summary_message(client, selected, planos),
            "time": _now_time(),
        })
        return chat_messages, None, company, {
            **draft,
            "step": "confirm_summary",
            "plan_key": selected.plan_key,
            "selected": selected.to_dict(),
            "planos": planos.to_dict(),
            "client": {
                "razao_social": client.get("razao_social"),
                "nome_fantasia": client.get("nome_fantasia"),
                "documento": client.get("documento"),
                "responsavel": client.get("responsavel"),
            },
        }

    if step == "manual_edit":
        count = int(draft.get("collaborators") or 0)
        planos = calcular_planos_ponto(count)
        key = normalize_text(plan_key or manual_fields.get("plan_key") or draft.get("plan_key") or PLAN_ANUAL)
        selected = apply_manual_override(
            planos,
            plan_key=key,
            valor_mensal=manual_fields.get("valor_mensal"),
            valor_anual=manual_fields.get("valor_anual"),
            valor_mensal_equivalente=manual_fields.get("valor_mensal_equivalente"),
            desconto_valor=manual_fields.get("desconto_valor"),
            desconto_percentual=manual_fields.get("desconto_percentual"),
            valor_final=manual_fields.get("valor_final"),
            parcelas=manual_fields.get("parcelas"),
            valor_parcela=manual_fields.get("valor_parcela"),
            observacao=manual_fields.get("observacao") or "",
            validade_dias=manual_fields.get("validade_dias") or 10,
        )
        client = collect_client_data(company, df, columns)
        chat_messages.append({
            "role": "assistant",
            "content": _summary_message(client, selected, planos),
            "time": _now_time(),
        })
        return chat_messages, None, company, {
            **draft,
            "step": "confirm_summary",
            "plan_key": selected.plan_key,
            "selected": selected.to_dict(),
            "planos": planos.to_dict(),
            "manual": True,
        }

    if step == "confirm_summary":
        if action == "voltar" or clean_message.lower() in ("voltar", "editar", "voltar e editar"):
            chat_messages.append({
                "role": "assistant",
                "content": _draft_pricing_message(
                    calcular_planos_ponto(int(draft.get("collaborators") or 1)),
                    company,
                ),
                "time": _now_time(),
            })
            return chat_messages, None, company, {
                **draft,
                "step": "choose_plan",
            }

        if action in ("gerar", "gerar_pdf") or _is_affirmative(clean_message) or clean_message.lower() in (
            "gerar", "gerar proposta", "gerar proposta em pdf", "confirmar"
        ):
            count = int(draft.get("collaborators") or 0)
            planos = calcular_planos_ponto(count)
            selected_data = draft.get("selected") or {}
            if draft.get("manual"):
                selected = apply_manual_override(
                    planos,
                    plan_key=selected_data.get("plan_key") or draft.get("plan_key") or PLAN_ANUAL,
                    valor_mensal=selected_data.get("valor_mensal"),
                    valor_anual=selected_data.get("valor_anual"),
                    valor_mensal_equivalente=selected_data.get("valor_mensal_equivalente"),
                    desconto_valor=selected_data.get("desconto_valor"),
                    desconto_percentual=selected_data.get("desconto_percentual"),
                    valor_final=selected_data.get("valor_final"),
                    parcelas=selected_data.get("parcelas"),
                    valor_parcela=selected_data.get("valor_parcela"),
                    observacao=selected_data.get("observacao") or "",
                    validade_dias=selected_data.get("validade_dias") or 10,
                )
            else:
                selected = select_plan(
                    planos,
                    selected_data.get("plan_key") or draft.get("plan_key") or planos.plano_recomendado,
                    validade_dias=int(selected_data.get("validade_dias") or 10),
                    observacao=normalize_text(selected_data.get("observacao") or ""),
                )
            return _finalize_proposal(
                company=company,
                df=df,
                columns=columns,
                selected=selected,
                chat_messages=chat_messages,
                usuario=usuario,
            )

        chat_messages.append({
            "role": "assistant",
            "content": "Responda **gerar** para criar o PDF ou **voltar** para editar.",
            "time": _now_time(),
        })
        return chat_messages, None, company, draft

    # fallback
    chat_messages.append({
        "role": "assistant",
        "content": f"**Quantos colaboradores a empresa {company} possui?**",
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
            "Olá! Vamos montar a proposta do **Ponto Eletrônico Oppi**.\n\n"
            "1) Selecione o cliente cadastrado.\n"
            "2) Informe a quantidade de colaboradores.\n"
            "3) Veja as três formas de pagamento e a sugestão da Oppi.\n"
            "4) Confirme o resumo — o PDF só é gerado depois da confirmação."
        ),
        "time": _now_time(),
    }]
