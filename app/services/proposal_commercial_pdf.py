"""PDF comercial Ponto Eletrônico Oppi — ReportLab (padrão visual do sistema)."""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from decimal import Decimal

from app.services.legacy_core import (
    find_prepared_company_row,
    format_endereco_for_display,
    normalize_text,
    resolve_company_name,
    row_contact_email,
    row_contact_phone,
    row_field_value,
    row_get,
)
from app.services.proposal_pricing import (
    EXTRA_MENSAL,
    PLAN_ANUAL,
    PLAN_BOLETO,
    PLAN_CARTAO,
    PLAN_LABELS,
    SelectedProposalPricing,
    calcular_planos_ponto,
    format_money_br,
    select_plan,
)

OPPI_CONTRATADA = {
    "nome": "OPPI TECH LTDA",
    "cnpj": "42.412.507/0001-00",
    "endereco": "Rua Francisco Furtado, 117 A – Piso 1",
    "cidade": "Cidade Líder – CEP 08280-200 – São Paulo/SP",
}

OBJETIVO = (
    "A plataforma tem como objetivo facilitar a gestão de ponto dos colaboradores, "
    "oferecendo mais controle para a empresa, mais transparência para a equipe e mais "
    "segurança na conferência da jornada de trabalho.\n\n"
    "Com o Ponto Eletrônico Oppi, sua empresa consegue comprovar horários, organizar "
    "documentos e acompanhar os registros de forma digital, reduzindo retrabalho e "
    "melhorando a gestão interna."
)

FUNCIONALIDADES = [
    ("Registro de ponto online", "O colaborador pode bater ponto pelo celular, computador ou tablet."),
    ("Registro com foto", "Mais segurança e comprovação real no momento da batida de ponto."),
    (
        "Controle de jornada dos colaboradores",
        "Acompanhamento de entradas, saídas, intervalos e horários registrados.",
    ),
    (
        "Relatórios completos",
        "A empresa consegue consultar e acompanhar as informações do ponto de forma organizada, "
        "sem precisar somar horas manualmente.",
    ),
    ("Espelho de ponto", "Disponibilização do espelho de ponto para conferência dos registros."),
    ("Envio de holerites", "Possibilidade de envio de holerites pela plataforma."),
    ("Envio de documentos", "O gestor pode enviar documentos importantes aos colaboradores de forma digital."),
    ("Assinatura digital de documentos", "Assinatura de holerites e documentos de forma prática e segura."),
    ("Lembretes e comunicados", "Envio de lembretes e avisos para auxiliar na rotina da empresa."),
    ("Gestão de colaboradores", "Organização dos colaboradores cadastrados em uma única plataforma."),
    ("Acesso do gestor", "Painel para acompanhar registros, colaboradores, documentos e informações da empresa."),
]

PROPOSTA_VALOR = (
    "A Oppi entrega uma solução simples e acessível para empresas que desejam "
    "profissionalizar o controle de ponto sem burocracia.\n\n"
    "Com a plataforma, sua empresa ganha:\n"
    "- Mais controle sobre os horários dos colaboradores;\n"
    "- Menos retrabalho com cálculos manuais;\n"
    "- Mais organização nos documentos internos;\n"
    "- Mais segurança na comprovação da jornada;\n"
    "- Mais praticidade para gestores e colaboradores;\n"
    "- Relatórios completos para acompanhamento da operação;\n"
    "- Redução do uso de papel e processos manuais.\n\n"
    "A proposta é transformar a gestão de ponto em um processo simples, digital e seguro."
)

ATIVACAO = (
    "A ativação é realizada após o envio dos dados da empresa e confirmação do plano escolhido.\n\n"
    "Para o cadastro, solicitamos:\n"
    "- Nome completo do responsável;\n"
    "- Cargo do responsável;\n"
    "- CNPJ da empresa;\n"
    "- Razão social;\n"
    "- Nome fantasia;\n"
    "- Telefone ou WhatsApp da empresa;\n"
    "- E-mail de login do gestor;\n"
    "- E-mail para confirmação do administrador;\n"
    "- E-mail para cobrança;\n"
    "- Plano escolhido;\n"
    "- Forma de pagamento.\n\n"
    "Após o envio das informações, nossa equipe realiza o cadastro e libera o acesso à plataforma."
)

SUPORTE = (
    "O suporte é realizado em horário comercial, de segunda a sexta-feira, das 09h às 18h, "
    "exceto feriados.\n\n"
    "O suporte inclui orientações de uso, dúvidas operacionais, análise de erros e pequenos "
    "ajustes relacionados ao funcionamento contratado.\n\n"
    "Demandas fora do escopo, como novas funcionalidades, novas integrações, novas páginas ou "
    "alterações estruturais, poderão ser avaliadas e orçadas separadamente."
)

PRAZOS = (
    "- Retorno inicial: até 24 horas úteis;\n"
    "- Correção de erro simples: até 2 dias úteis;\n"
    "- Ajuste visual ou alteração de texto: de 2 a 5 dias úteis;\n"
    "- Inclusão ou alteração de campo simples: de 3 a 7 dias úteis;\n"
    "- Ajustes em relatórios, filtros ou gráficos: de 5 a 10 dias úteis;\n"
    "- Novas funcionalidades ou integrações: prazo definido mediante orçamento."
)

MONTHS_PT = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def _escape(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _paragraphs(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").split("\n") if line.strip()]


def _field_line(label: str, value: str | None) -> str | None:
    clean = normalize_text(value)
    if not clean or clean.lower() in {"não informado", "nao informado", "—", "-"}:
        return None
    return f"{label}: {clean}"


def collect_client_data(company: str, df, columns: dict) -> dict:
    company = resolve_company_name(company, df)
    row = find_prepared_company_row(company, df)

    def field(*keys: str) -> str:
        if row is None:
            return ""
        for key in keys:
            value = normalize_text(row_field_value(row, columns, key) or row_get(row, f"_{key}"))
            if value:
                return value
        return ""

    endereco = ""
    if row is not None:
        endereco = format_endereco_for_display(row, columns) or field("endereco")

    razao = field("empresa") or company
    fantasia = field("nome_fantasia", "fantasia")
    cnpj = field("cnpj")
    cpf = field("cpf_socio_1", "cpf")
    documento = cnpj or cpf
    email = row_contact_email(row, columns) if row is not None else ""
    telefone = row_contact_phone(row, columns) if row is not None else ""
    whatsapp = field("telefone_b2b") or telefone
    responsavel = field("socio_1", "responsavel")
    cargo = field("cargo_responsavel", "cargo")

    return {
        "empresa": company,
        "razao_social": razao,
        "nome_fantasia": fantasia,
        "cnpj": cnpj,
        "cpf": cpf,
        "documento": documento,
        "endereco": endereco,
        "numero": field("endereco_numero"),
        "complemento": field("endereco_complemento"),
        "bairro": field("bairro"),
        "cidade": field("municipio"),
        "estado": field("uf"),
        "cep": field("cep"),
        "email": normalize_text(email),
        "telefone": normalize_text(telefone),
        "whatsapp": normalize_text(whatsapp),
        "responsavel": responsavel,
        "cargo": cargo,
        "colaboradores": field("colaboradores"),
        "vendedor": field("vendedor"),
        "servico_cadastro": field("servico"),
    }


def proposal_pdf_filename(company_name: str, emission: date | None = None) -> str:
    emission = emission or date.today()
    normalized = unicodedata.normalize("NFKD", normalize_text(company_name) or "Cliente")
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_")
    ascii_name = re.sub(r"_+", "_", ascii_name) or "Cliente"
    return f"Proposta_Ponto_Oppi_{ascii_name}_{emission.strftime('%d-%m-%Y')}.pdf"


def _selected_from_payload(payload: dict | None, colaboradores: int) -> SelectedProposalPricing:
    planos = calcular_planos_ponto(colaboradores)
    if not payload:
        return select_plan(planos, planos.plano_recomendado)
    plan_key = normalize_text(payload.get("plan_key") or planos.plano_recomendado)
    selected = select_plan(
        planos,
        plan_key,
        validade_dias=int(payload.get("validade_dias") or 10),
        observacao=normalize_text(payload.get("observacao") or ""),
    )
    # Overrides manuais já serializados
    for attr, key in (
        ("valor_mensal", "valor_mensal"),
        ("valor_anual", "valor_anual"),
        ("valor_mensal_equivalente", "valor_mensal_equivalente"),
        ("desconto_valor", "desconto_valor"),
        ("desconto_percentual", "desconto_percentual"),
        ("valor_final", "valor_final"),
    ):
        raw = payload.get(key)
        if raw not in (None, ""):
            try:
                setattr(selected, attr, Decimal(str(raw)))
            except Exception:
                pass
    if payload.get("manual"):
        selected.manual = True
    return selected


def generate_commercial_proposal_pdf(
    company: str,
    df,
    columns: dict,
    *,
    services_description: str = "",
    plans_text: str | None = None,
    proposal_snapshot: dict | None = None,
) -> bytes:
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    client = collect_client_data(company, df, columns)
    snapshot = dict(proposal_snapshot or {})
    try:
        colaboradores = int(snapshot.get("colaboradores") or 0)
    except (TypeError, ValueError):
        colaboradores = 0
    if colaboradores <= 0:
        from app.services.proposal_pricing import parse_collaborators_count

        colaboradores = parse_collaborators_count(snapshot.get("colaboradores")) or parse_collaborators_count(
            services_description
        ) or 10

    selected = _selected_from_payload(snapshot.get("selected") or snapshot, colaboradores)
    planos = selected.planos
    today = date.today()
    date_label = f"São Paulo, {today.day} de {MONTHS_PT[today.month - 1]} de {today.year}."

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "OppiTitle", parent=styles["Heading1"], fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=2
    )
    subtitle = ParagraphStyle(
        "OppiSubtitle", parent=styles["Normal"], fontSize=10, leading=13, alignment=TA_CENTER, spaceAfter=10
    )
    heading = ParagraphStyle(
        "OppiH", parent=styles["Heading2"], fontSize=11, leading=14, spaceBefore=10, spaceAfter=4
    )
    body = ParagraphStyle(
        "OppiBody", parent=styles["Normal"], fontSize=9.5, leading=13, alignment=TA_JUSTIFY, spaceAfter=3
    )
    body_left = ParagraphStyle("OppiBodyLeft", parent=body, alignment=TA_LEFT)
    highlight = ParagraphStyle(
        "OppiHighlight", parent=body_left, backColor="#F3F4F6", borderPadding=4, spaceBefore=2, spaceAfter=2
    )
    small = ParagraphStyle(
        "OppiSmall", parent=styles["Normal"], fontSize=9, leading=12, alignment=TA_CENTER, spaceBefore=8
    )
    story: list = []

    def add_heading(text: str) -> None:
        story.append(Paragraph(_escape(text), heading))
        story.append(HRFlowable(width="100%", thickness=0.6, color="#333333", spaceAfter=4))

    def add_text(text: str, style=body) -> None:
        for line in _paragraphs(text):
            story.append(Paragraph(_escape(line), style))

    def add_labeled_block(lines: list[str | None], style=body_left) -> None:
        for line in lines:
            if line:
                story.append(Paragraph(_escape(line), style))

    story.append(Paragraph("PROPOSTA COMERCIAL", title))
    story.append(Paragraph("PONTO ELETRÔNICO OPPI", subtitle))
    story.append(Spacer(1, 4))

    add_heading("Contratante")
    add_labeled_block(
        [
            _field_line("CONTRATANTE", client.get("razao_social") or client.get("empresa")),
            _field_line("NOME FANTASIA", client.get("nome_fantasia")),
            _field_line("CNPJ/CPF", client.get("documento")),
            _field_line("ENDEREÇO", client.get("endereco")),
            _field_line("E-MAIL", client.get("email")),
            _field_line("TELEFONE", client.get("telefone")),
            _field_line("WHATSAPP", client.get("whatsapp") if client.get("whatsapp") != client.get("telefone") else ""),
            _field_line("RESPONSÁVEL", client.get("responsavel")),
            _field_line("CARGO", client.get("cargo")),
        ]
    )

    add_heading("Contratada")
    add_labeled_block(
        [
            f"CONTRATADA: {OPPI_CONTRATADA['nome']}",
            f"CNPJ: {OPPI_CONTRATADA['cnpj']}",
            OPPI_CONTRATADA["endereco"],
            OPPI_CONTRATADA["cidade"],
        ]
    )

    add_heading("Objetivo da plataforma")
    add_text(OBJETIVO)

    add_heading("Funcionalidades inclusas")
    for name, desc in FUNCIONALIDADES:
        story.append(Paragraph(f"<b>{_escape(name)}</b><br/>{_escape(desc)}", body_left))

    add_heading("Proposta de valor")
    add_text(PROPOSTA_VALOR)

    add_heading("Planos disponíveis")
    add_text(
        "Plano Mensal no Boleto\n"
        f"- Quantidade de colaboradores: {planos.quantidade_total}\n"
        f"- Valor mensal: {format_money_br(planos.total_mensal_boleto)}\n"
        + (
            f"- Valor dos adicionais: {format_money_br(planos.adicional_mensal)}\n"
            if planos.quantidade_adicional
            else ""
        )
        + "- Forma de pagamento: boleto mensal.\n\n"
        "Plano Mensal Recorrente no Cartão\n"
        f"- Quantidade de colaboradores: {planos.quantidade_total}\n"
        f"- Valor mensal: {format_money_br(planos.total_mensal_cartao)}\n"
        + (
            f"- Valor dos adicionais: {format_money_br(planos.adicional_mensal)}\n"
            if planos.quantidade_adicional
            else ""
        )
        + "- Forma de pagamento: cartão recorrente.\n\n"
        "Plano Anual\n"
        f"- Quantidade de colaboradores: {planos.quantidade_total}\n"
        f"- Valor anual: {format_money_br(planos.total_anual)}\n"
        f"- Valor mensal equivalente: {format_money_br(planos.mensal_equivalente_anual)}\n"
        + (
            f"- Valor anual dos adicionais: {format_money_br(planos.adicional_anual)}\n"
            if planos.quantidade_adicional
            else ""
        )
        + "- Forma de pagamento: anual à vista.\n\n"
        f"Colaboradores adicionais: {format_money_br(EXTRA_MENSAL)} por colaborador por mês.",
        body_left,
    )

    recommended_label = PLAN_LABELS.get(planos.plano_recomendado, "Anual à vista")
    selected_label = selected.plan_label
    if planos.plano_recomendado == selected.plan_key:
        story.append(Paragraph(f"<b>Destaque:</b> plano recomendado e selecionado — { _escape(selected_label) }.", highlight))
    else:
        story.append(Paragraph(f"<b>Plano recomendado:</b> {_escape(recommended_label)}.", highlight))
        story.append(Paragraph(f"<b>Plano selecionado:</b> {_escape(selected_label)}.", highlight))

    add_heading("Plano selecionado")
    selected_lines = [
        f"Quantidade de colaboradores: {planos.quantidade_total}",
        f"Quantidade incluída: {planos.quantidade_incluida}",
        f"Quantidade adicional: {planos.quantidade_adicional}",
        f"Forma de pagamento: {selected.payment_label}",
    ]
    if selected.plan_key == PLAN_BOLETO:
        selected_lines.append(f"Valor-base: {format_money_br(planos.valor_base_boleto)}")
        if planos.quantidade_adicional:
            selected_lines.append(f"Valor dos adicionais: {format_money_br(planos.adicional_mensal)}")
        selected_lines.append(f"Valor mensal final: {format_money_br(selected.valor_mensal)}")
    elif selected.plan_key == PLAN_CARTAO:
        selected_lines.append(f"Valor-base: {format_money_br(planos.valor_base_cartao)}")
        if planos.quantidade_adicional:
            selected_lines.append(f"Valor dos adicionais: {format_money_br(planos.adicional_mensal)}")
        selected_lines.append(f"Valor mensal final: {format_money_br(selected.valor_mensal)}")
    else:
        selected_lines.append(f"Valor-base: {format_money_br(planos.valor_base_anual)}")
        if planos.quantidade_adicional:
            selected_lines.append(f"Valor dos adicionais: {format_money_br(planos.adicional_anual)}")
        selected_lines.append(f"Valor anual final: {format_money_br(selected.valor_anual or planos.total_anual)}")
        selected_lines.append(
            f"Valor mensal equivalente: {format_money_br(selected.valor_mensal_equivalente or planos.mensal_equivalente_anual)}"
        )
    if selected.desconto_valor and selected.desconto_valor > 0:
        selected_lines.append(f"Desconto: {format_money_br(selected.desconto_valor)}")
    if selected.desconto_percentual and selected.desconto_percentual > 0:
        selected_lines.append(f"Desconto: {selected.desconto_percentual}%")
    selected_lines.append(f"Valor final: {format_money_br(selected.valor_final)}")
    if selected.observacao:
        selected_lines.append(f"Observação comercial: {selected.observacao}")
    add_labeled_block(selected_lines)

    add_heading("Ativação da plataforma")
    add_text(ATIVACAO, body_left)

    add_heading("Suporte")
    add_text(SUPORTE)

    add_heading("Prazos de atendimento")
    add_text(PRAZOS, body_left)

    add_heading("Investimento acessível para sua empresa")
    investimento = (
        f"Para uma empresa com {planos.quantidade_total} colaboradores, o plano selecionado possui "
        f"o investimento de {format_money_br(selected.valor_final)}, na modalidade {selected.payment_label}.\n\n"
        "Com a Oppi, sua empresa passa a contar com uma solução digital para controle de ponto, "
        "documentos e relatórios."
    )
    if selected.plan_key == PLAN_ANUAL:
        investimento += (
            f"\n\nO investimento anual é de {format_money_br(selected.valor_anual or planos.total_anual)} "
            f"à vista, equivalente a "
            f"{format_money_br(selected.valor_mensal_equivalente or planos.mensal_equivalente_anual)} por mês."
        )
    add_text(investimento)

    add_heading("Validade da proposta")
    add_text(
        f"Esta proposta possui validade de {selected.validade_dias} dias corridos a partir da data de emissão."
    )

    add_heading("Encerramento")
    add_text(
        "A Oppi foi criada para empresas que buscam praticidade, organização e mais segurança "
        "na gestão dos colaboradores.\n\n"
        "Agradecemos pela oportunidade de apresentar nossa proposta comercial.\n\n"
        "OPPI – Gestão • Operação • Performance"
    )
    story.append(Paragraph(_escape(date_label), small))
    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.5, color="#999999", spaceBefore=8, spaceAfter=12))

    add_text("______________________________", body_left)
    add_labeled_block(
        [
            _field_line("CONTRATANTE", client.get("razao_social") or client.get("empresa")),
            _field_line("RESPONSÁVEL", client.get("responsavel")),
            _field_line("CNPJ/CPF", client.get("documento")),
        ]
    )
    story.append(Spacer(1, 16))
    add_text("______________________________", body_left)
    add_labeled_block(
        [
            OPPI_CONTRATADA["nome"],
            f"CNPJ: {OPPI_CONTRATADA['cnpj']}",
        ]
    )

    doc.build(story)
    return buffer.getvalue()
