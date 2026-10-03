"""PDF comercial Ponto Eletrônico Oppi — ReportLab (padrão visual do sistema)."""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path

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
    SelectedProposalPricing,
    calcular_planos_ponto,
    format_money_br,
    select_plan,
)

_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "img" / "oppi-logo.png"
_LETTERHEAD_PATH = Path(__file__).resolve().parent.parent / "static" / "img" / "proposal-letterhead.png"

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
    return f"<b>{_escape(label)}:</b> {_escape(clean)}"


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


def _resolve_logo_path() -> Path | None:
    if _LOGO_PATH.is_file():
        return _LOGO_PATH
    return None


def _resolve_letterhead_path() -> Path | None:
    if _LETTERHEAD_PATH.is_file():
        return _LETTERHEAD_PATH
    return None


def _draw_page_chrome(canvas, doc) -> None:
    """Letterhead do modelo Oppi (faixa preta + logo) em todas as páginas."""
    from reportlab.lib.pagesizes import A4

    canvas.saveState()
    page_w, page_h = A4
    footer_h = 64  # cobre a faixa do PNG (~7% da página) e elimina rebarba do texto antigo
    letterhead = _resolve_letterhead_path()
    if letterhead is not None:
        try:
            canvas.drawImage(
                str(letterhead),
                0,
                0,
                width=page_w,
                height=page_h,
                preserveAspectRatio=False,
                mask="auto",
            )
        except Exception:
            letterhead = None
    if letterhead is None:
        # Fallback: faixa preta + logo (se letterhead não existir no deploy)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.rect(0, page_h - 42, page_w, 42, fill=1, stroke=0)
        canvas.rect(0, 0, page_w, footer_h, fill=1, stroke=0)
        logo = _resolve_logo_path()
        if logo is not None:
            try:
                canvas.drawImage(
                    str(logo),
                    page_w - 52,
                    page_h - 36,
                    width=28,
                    height=28,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                pass

    # Repinta o rodapé inteiro e redesenha o endereço (sem restos do PNG).
    canvas.setFillColorRGB(0, 0, 0)
    canvas.rect(0, 0, page_w, footer_h, fill=1, stroke=0)
    canvas.setFillColorRGB(1, 1, 1)
    canvas.setFont("Helvetica", 9)
    # Centraliza o bloco de 2 linhas na faixa (y=0 = base da página).
    line_gap = 15
    mid_y = footer_h / 2 + 1  # leve ajuste óptico (glifos ficam acima da baseline)
    canvas.drawCentredString(page_w / 2, mid_y + line_gap / 2, "Rua Francisco Furtado, 117 A CEP: 08280-200")
    canvas.drawCentredString(page_w / 2, mid_y - line_gap / 2, "Cidade Líder - São Paulo")
    canvas.restoreState()


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
        leftMargin=22 * mm,
        rightMargin=24 * mm,
        topMargin=28 * mm,
        bottomMargin=28 * mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "OppiTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        alignment=TA_LEFT,
        spaceAfter=2,
        textColor="#111111",
    )
    subtitle = ParagraphStyle(
        "OppiSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        alignment=TA_LEFT,
        spaceAfter=10,
        textColor="#111111",
    )
    heading = ParagraphStyle(
        "OppiH",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        spaceBefore=10,
        spaceAfter=2,
        textColor="#111111",
    )
    body = ParagraphStyle(
        "OppiBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        alignment=TA_JUSTIFY,
        spaceAfter=4,
        textColor="#111111",
    )
    body_left = ParagraphStyle("OppiBodyLeft", parent=body, alignment=TA_LEFT, spaceAfter=2)
    feature_title = ParagraphStyle(
        "OppiFeatureTitle",
        parent=body_left,
        fontName="Helvetica-Bold",
        spaceBefore=4,
        spaceAfter=1,
    )
    small = ParagraphStyle(
        "OppiSmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
        spaceBefore=8,
        textColor="#111111",
    )
    story: list = []

    def add_heading(text: str) -> None:
        story.append(Paragraph(_escape(text), heading))
        story.append(HRFlowable(width="100%", thickness=0.6, color="#888888", spaceBefore=1, spaceAfter=6))

    def add_text(text: str, style=body) -> None:
        for line in _paragraphs(text):
            story.append(Paragraph(_escape(line), style))

    def add_labeled_block(lines: list[str | None], style=body_left) -> None:
        for line in lines:
            if line:
                story.append(Paragraph(line if "<b>" in line else _escape(line), style))

    def add_feature(name: str, desc: str) -> None:
        # Modelo: nome em negrito sem ":" + descrição na linha seguinte
        story.append(Paragraph(_escape(name), feature_title))
        story.append(Paragraph(_escape(desc), body_left))

    story.append(Paragraph("PROPOSTA COMERCIAL", title))
    story.append(Paragraph("IMPLANTAÇÃO OPERACIONAL E COMERCIAL — OPPI", subtitle))

    # Blocos no formato do modelo (sem títulos de seção "Contratante/Contratada")
    add_labeled_block(
        [
            _field_line("CONTRATANTE", client.get("razao_social") or client.get("empresa")),
            _field_line("CNPJ", client.get("documento")),
            _field_line("Rua", client.get("endereco")),
            _field_line("E-mail", client.get("email") or ""),
        ]
    )
    story.append(Spacer(1, 6))
    add_labeled_block(
        [
            f"<b>CONTRATADO:</b> {_escape(OPPI_CONTRATADA['nome'])}",
            f"<b>CNPJ:</b> {_escape(OPPI_CONTRATADA['cnpj'])}",
            _escape(OPPI_CONTRATADA["endereco"]),
            _escape(OPPI_CONTRATADA["cidade"]),
        ]
    )

    add_heading("Objetivo da plataforma")
    add_text(OBJETIVO)

    add_heading("Funcionalidades inclusas")
    story.append(Paragraph("A plataforma oferece:", body_left))
    for name, desc in FUNCIONALIDADES:
        add_feature(name, desc)

    add_heading("Proposta de valor")
    # Modelo: bullets sem hífen, frases curtas
    add_text(
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

    add_heading("Planos disponíveis")
    # Formato do PDF modelo (nome → preço → inclui)
    story.append(Paragraph("<b>Plano Mensal no Boleto</b>", body_left))
    story.append(
        Paragraph(
            _escape(
                f"{format_money_br(planos.total_mensal_boleto)} por mês até {planos.quantidade_total} colaboradores"
            ),
            body_left,
        )
    )
    story.append(
        Paragraph(
            _escape(f"Inclui acesso à plataforma para até {planos.quantidade_total} colaboradores."),
            body_left,
        )
    )
    story.append(Spacer(1, 6))
    story.append(Paragraph("<b>Plano Mensal Recorrente no Cartão</b>", body_left))
    story.append(
        Paragraph(
            _escape(f"{format_money_br(planos.total_mensal_cartao)} por mês"),
            body_left,
        )
    )
    story.append(
        Paragraph(
            _escape(f"Inclui acesso à plataforma para até {planos.quantidade_total} colaboradores."),
            body_left,
        )
    )
    story.append(Spacer(1, 6))
    story.append(Paragraph("<b>Plano Anual</b>", body_left))
    story.append(Paragraph(_escape(f"{format_money_br(planos.total_anual)} à vista"), body_left))
    story.append(
        Paragraph(
            _escape(
                f"Equivalente a {format_money_br(planos.mensal_equivalente_anual)} por mês durante 12 meses."
            ),
            body_left,
        )
    )
    story.append(
        Paragraph(
            _escape(f"Inclui acesso à plataforma para até {planos.quantidade_total} colaboradores."),
            body_left,
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            _escape(f"Colaboradores adicionais: {format_money_br(EXTRA_MENSAL)} por colaborador/mês."),
            body_left,
        )
    )
    if selected.plan_label:
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                f"<b>Plano selecionado nesta proposta:</b> {_escape(selected.plan_label)} "
                f"— valor final {format_money_br(selected.valor_final)}.",
                body_left,
            )
        )

    add_heading("Ativação da plataforma")
    add_text(
        "A ativação é realizada após o envio dos dados da empresa e confirmação do plano escolhido.\n\n"
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
        "Forma de pagamento\n\n"
        "Após o envio das informações, nossa equipe realiza o cadastro e libera o acesso à plataforma.",
        body_left,
    )

    add_heading("Suporte")
    add_text(SUPORTE)

    add_heading("Prazos de atendimento")
    add_text(
        "Retorno inicial: até 24 horas úteis.\n"
        "Correção de erro simples: até 2 dias úteis.\n"
        "Ajuste visual ou alteração de texto: de 2 a 5 dias úteis.\n"
        "Inclusão ou alteração de campo simples: de 3 a 7 dias úteis.\n"
        "Ajustes em relatórios, filtros ou gráficos: de 5 a 10 dias úteis.\n"
        "Novas funcionalidades ou integrações: prazo definido mediante orçamento.",
        body_left,
    )

    add_heading("Investimento acessível para sua empresa")
    add_text(
        f"Com planos a partir de {format_money_br(planos.mensal_equivalente_anual)} por mês no plano anual, "
        "sua empresa passa a contar com uma solução digital para controle de ponto, documentos e relatórios.\n\n"
        "A Oppi foi criada para empresas que buscam praticidade, organização e mais "
        "segurança na gestão dos colaboradores.\n\n"
        "Agradecemos pela oportunidade de apresentar nossa proposta comercial.\n\n"
        "OPPI - Gestão • Operação • Performance"
    )
    story.append(Paragraph(_escape(date_label), small))
    story.append(Spacer(1, 20))

    story.append(Paragraph("______________________", body_left))
    add_labeled_block(
        [
            "<b>CONTRATANTE:</b>",
            _field_line("CNPJ", client.get("documento")),
        ]
    )
    story.append(Spacer(1, 16))
    story.append(Paragraph("______________________", body_left))
    add_labeled_block(
        [
            f"<b>{_escape(OPPI_CONTRATADA['nome'])}</b>",
            f"<b>CNPJ:</b> {_escape(OPPI_CONTRATADA['cnpj'])}",
        ]
    )

    doc.build(story, onFirstPage=_draw_page_chrome, onLaterPages=_draw_page_chrome)
    return buffer.getvalue()
