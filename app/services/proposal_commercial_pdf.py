"""PDF comercial Oppi (modelo completo) via ReportLab."""
from __future__ import annotations

import io
from datetime import date

from app.services.legacy_core import (
    build_client_commercial_summary,
    find_prepared_company_row,
    format_endereco_for_display,
    normalize_text,
    resolve_company_name,
    row_contact_email,
    row_contact_phone,
    row_field_value,
    row_get,
)
from app.services.proposal_pricing import compute_plan_pricing

OPPI_CONTRATADO = {
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
    ("Controle de jornada dos colaboradores", "Acompanhamento de entradas, saídas e horários registrados."),
    ("Relatórios completos", "A empresa consegue consultar e acompanhar as informações do ponto de forma organizada, sem precisar somar horas manualmente."),
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
    "Mais controle sobre os horários dos colaboradores.\n"
    "Menos retrabalho com cálculos manuais.\n"
    "Mais organização nos documentos internos.\n"
    "Mais segurança na comprovação da jornada.\n"
    "Mais praticidade para gestores e colaboradores.\n"
    "Relatórios completos para acompanhamento da operação.\n"
    "Redução do uso de papel e processos manuais.\n\n"
    "A proposta é transformar a gestão de ponto em um processo simples, digital e seguro."
)

ATIVACAO = (
    "A ativação é realizada após o envio dos dados da empresa e confirmação do plano escolhido.\n"
    "Para cadastro, solicitamos:\n"
    "Nome completo do responsável\n"
    "Cargo do responsável\n"
    "CNPJ da empresa\n"
    "Razão social\n"
    "Nome fantasia\n"
    "Telefone / WhatsApp da empresa\n"
    "E-mail de login do gestor\n"
    "E-mail para confirmação do administrador\n"
    "E-mail para cobrança\n"
    "Plano escolhido\n"
    "Forma de pagamento\n"
    "Após o envio das informações, nossa equipe realiza o cadastro e libera o acesso à plataforma."
)

SUPORTE = (
    "O suporte é realizado em horário comercial, de segunda a sexta-feira, das 09h às 18h, "
    "exceto feriados.\n"
    "O suporte inclui orientações de uso, dúvidas operacionais, análise de erros e pequenos "
    "ajustes relacionados ao funcionamento contratado.\n"
    "Demandas fora do escopo, como novas funcionalidades, novas integrações, novas páginas ou "
    "alterações estruturais, poderão ser avaliadas e orçadas separadamente."
)

PRAZOS = (
    "Retorno inicial: até 24 horas úteis.\n"
    "Correção de erro simples: até 2 dias úteis.\n"
    "Ajuste visual ou alteração de texto: de 2 a 5 dias úteis.\n"
    "Inclusão ou alteração de campo simples: de 3 a 7 dias úteis.\n"
    "Ajustes em relatórios, filtros ou gráficos: de 5 a 10 dias úteis.\n"
    "Novas funcionalidades ou integrações: prazo definido mediante orçamento."
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


def collect_client_data(company: str, df, columns: dict) -> dict:
    company = resolve_company_name(company, df)
    row = find_prepared_company_row(company, df)
    summary = build_client_commercial_summary(row, columns) if row is not None else {}
    endereco = ""
    cnpj = ""
    if row is not None:
        endereco = format_endereco_for_display(row, columns) or normalize_text(
            row_field_value(row, columns, "endereco") or row_get(row, "_endereco")
        )
        cnpj = normalize_text(row_field_value(row, columns, "cnpj") or row_get(row, "_cnpj"))
    return {
        "empresa": company,
        "cnpj": cnpj or "Não informado",
        "endereco": endereco or "Não informado",
        "email": row_contact_email(row, columns) if row is not None else "",
        "telefone": row_contact_phone(row, columns) if row is not None else "",
        "colaboradores": normalize_text(summary.get("colaboradores") or ""),
        "vendedor": normalize_text(
            (summary.get("vendedor") or row_get(row, "_vendedor") or "") if row is not None else ""
        ),
        "servico_cadastro": normalize_text(summary.get("servico") or ""),
    }


def generate_commercial_proposal_pdf(
    company: str,
    df,
    columns: dict,
    *,
    services_description: str,
    plans_text: str | None = None,
) -> bytes:
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    client = collect_client_data(company, df, columns)
    pricing = compute_plan_pricing(
        services_description,
        cadastro_collaborators=client.get("colaboradores"),
    )
    plans_block = normalize_text(plans_text) or pricing.plans_block
    today = date.today()
    months = (
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    )
    date_label = f"São Paulo, {today.day} de {months[today.month - 1]} de {today.year}"

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
        "OppiTitle",
        parent=styles["Heading1"],
        fontSize=14,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    subtitle = ParagraphStyle(
        "OppiSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "OppiH",
        parent=styles["Heading2"],
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "OppiBody",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13,
        alignment=TA_JUSTIFY,
        spaceAfter=3,
    )
    body_left = ParagraphStyle(
        "OppiBodyLeft",
        parent=body,
        alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        "OppiSmall",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        spaceBefore=8,
    )
    story = []

    def add_heading(text: str) -> None:
        story.append(Paragraph(_escape(text), heading))
        story.append(HRFlowable(width="100%", thickness=0.6, color="#333333", spaceAfter=4))

    def add_text(text: str, style=body) -> None:
        for line in _paragraphs(text):
            story.append(Paragraph(_escape(line), style))

    story.append(Paragraph("PROPOSTA COMERCIAL", title))
    story.append(Paragraph("IMPLANTAÇÃO OPERACIONAL E COMERCIAL — OPPI", subtitle))
    story.append(Spacer(1, 4))

    add_heading("Partes")
    add_text(
        f"CONTRATANTE: {client['empresa']}\n"
        f"CNPJ: {client['cnpj']}\n"
        f"Rua: {client['endereco']}\n"
        f"E-mail: {client['email'] or '—'}",
        body_left,
    )
    add_text(
        f"CONTRATADO: {OPPI_CONTRATADO['nome']}\n"
        f"CNPJ: {OPPI_CONTRATADO['cnpj']}\n"
        f"{OPPI_CONTRATADO['endereco']}\n"
        f"{OPPI_CONTRATADO['cidade']}",
        body_left,
    )

    if services_description:
        add_heading("Serviços solicitados")
        add_text(services_description, body_left)

    add_heading("Objetivo da plataforma")
    add_text(OBJETIVO)

    add_heading("Funcionalidades inclusas")
    add_text("A plataforma oferece:")
    for name, desc in FUNCIONALIDADES:
        story.append(Paragraph(f"<b>{_escape(name)}</b><br/>{_escape(desc)}", body_left))

    add_heading("Proposta de valor")
    add_text(PROPOSTA_VALOR)

    add_heading("Planos disponíveis")
    add_text(plans_block, body_left)

    add_heading("Ativação da plataforma")
    add_text(ATIVACAO, body_left)

    add_heading("Suporte")
    add_text(SUPORTE)

    add_heading("Prazos de atendimento")
    add_text(PRAZOS, body_left)

    add_heading("Investimento acessível para sua empresa")
    from app.services.proposal_pricing import _fmt

    add_text(
        f"Com planos a partir de R$ {_fmt(pricing.anual_monthly_equiv)} "
        "por mês no plano anual, sua empresa passa a contar com uma solução digital para "
        "controle de ponto, documentos e relatórios.\n"
        "A Oppi foi criada para empresas que buscam praticidade, organização e mais segurança "
        "na gestão dos colaboradores.\n"
        "Agradecemos pela oportunidade de apresentar nossa proposta comercial."
    )
    story.append(Paragraph("OPPI - Gestão • Operação • Performance", small))
    story.append(Paragraph(_escape(date_label), small))
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color="#999999", spaceBefore=8, spaceAfter=8))
    add_text(
        f"CONTRATANTE:\n{client['empresa']}\nCNPJ: {client['cnpj']}",
        body_left,
    )
    story.append(Spacer(1, 12))
    add_text(
        f"{OPPI_CONTRATADO['nome']}\nCNPJ: {OPPI_CONTRATADO['cnpj']}",
        body_left,
    )

    doc.build(story)
    return buffer.getvalue()
