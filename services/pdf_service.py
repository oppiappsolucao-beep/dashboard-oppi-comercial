from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.settings import settings


def generate_proposal_pdf(
    proposal_data: dict,
    tenant_data: dict,
    client_data: dict,
    items: list[dict],
    output_name: str | None = None,
) -> str:
    settings.proposals_dir.mkdir(parents=True, exist_ok=True)
    filename = output_name or f"proposta_{proposal_data.get('proposal_code', datetime.utcnow().strftime('%Y%m%d%H%M%S'))}.pdf"
    output_path = settings.proposals_dir / filename

    doc = SimpleDocTemplate(str(output_path), pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], textColor=colors.HexColor("#6D28D9"))
    normal = styles["Normal"]
    story = []

    story.append(Paragraph(tenant_data.get("company_name", "Oppi CRM Comercial"), title_style))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(f"<b>Proposta:</b> {proposal_data.get('title', '')}", normal))
    story.append(Paragraph(f"<b>Código:</b> {proposal_data.get('proposal_code', '')}", normal))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("<b>Cliente</b>", styles["Heading2"]))
    story.append(Paragraph(client_data.get("company_name", "—"), normal))
    story.append(Paragraph(client_data.get("email", ""), normal))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("<b>Escopo e serviços</b>", styles["Heading2"]))
    table_data = [["Descrição", "Qtd", "Unitário", "Total"]]
    for item in items:
        table_data.append([
            item.get("description", ""),
            str(item.get("quantity", 1)),
            f"R$ {item.get('unit_value', 0):,.2f}",
            f"R$ {item.get('total_value', 0):,.2f}",
        ])
    table = Table(table_data, colWidths=[8 * cm, 2 * cm, 3 * cm, 3 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6D28D9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph(f"<b>Total:</b> R$ {proposal_data.get('total_value', 0):,.2f}", normal))
    story.append(Paragraph(f"<b>Validade:</b> {proposal_data.get('validity_date', '—')}", normal))
    story.append(Paragraph(f"<b>Forma de pagamento:</b> {proposal_data.get('payment_terms', '—')}", normal))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("Termos e condições conforme acordado entre as partes.", normal))
    story.append(Spacer(1, 1.5 * cm))
    story.append(Paragraph("Assinatura: ________________________________", normal))

    doc.build(story)
    return str(output_path)
