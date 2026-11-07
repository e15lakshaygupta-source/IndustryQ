from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, TableStyle


NAVY = colors.HexColor("#0B2C50")
NAVY_LIGHT = colors.HexColor("#173F6B")
GOLD = colors.HexColor("#D7A13D")
INK = colors.HexColor("#172A42")
MUTED = colors.HexColor("#61748A")
LINE = colors.HexColor("#D7DEE7")
PALE = colors.HexColor("#F4F7FA")
GOLD_PALE = colors.HexColor("#FFF3D6")


def premium_pdf_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PremiumPdfTitle", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=20, leading=24, textColor=NAVY, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "PremiumPdfSubtitle", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=MUTED,
        ),
        "section": ParagraphStyle(
            "PremiumPdfSection", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=9, leading=12, textColor=colors.white,
        ),
        "body": ParagraphStyle(
            "PremiumPdfBody", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8, leading=11, textColor=INK,
        ),
        "body_bold": ParagraphStyle(
            "PremiumPdfBodyBold", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=8, leading=11, textColor=NAVY,
        ),
        "small": ParagraphStyle(
            "PremiumPdfSmall", parent=base["BodyText"], fontName="Helvetica",
            fontSize=7, leading=9, textColor=MUTED,
        ),
        "small_white": ParagraphStyle(
            "PremiumPdfSmallWhite", parent=base["BodyText"], fontName="Helvetica",
            fontSize=7, leading=9, textColor=colors.white,
        ),
        "right": ParagraphStyle(
            "PremiumPdfRight", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8, leading=11, textColor=INK, alignment=TA_RIGHT,
        ),
    }


def premium_page_chrome(canvas, document, section="DOCUMENT", company_name=""):
    page_width, page_height = document.pagesize
    left = document.leftMargin
    right = page_width - document.rightMargin
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.roundRect(left, page_height - 13 * mm, 8 * mm, 8 * mm, 1.5 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    for index, height in enumerate((2.3, 3.5, 5.2, 3.2)):
        canvas.roundRect(
            left + (1.7 + index * 1.2) * mm,
            page_height - 11.5 * mm,
            .65 * mm,
            height * mm,
            .2 * mm,
            fill=1,
            stroke=0,
        )
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(left + 11 * mm, page_height - 9 * mm, "INDUSTRIQ")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 6.5)
    canvas.drawRightString(right, page_height - 8.5 * mm, section.upper())
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(1.1)
    canvas.line(left, page_height - 16 * mm, right, page_height - 16 * mm)
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(left, 13 * mm, right, 13 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 6.5)
    canvas.drawString(left, 9 * mm, company_name or "Operations, connected")
    canvas.drawRightString(right, 9 * mm, f"Page {document.page}")
    canvas.restoreState()


def premium_document(buffer, title, author="", page_size=A4, landscape_page=False):
    if landscape_page:
        page_size = landscape(page_size)
    document = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=13 * mm,
        leftMargin=13 * mm,
        topMargin=21 * mm,
        bottomMargin=19 * mm,
        title=title,
        author=author or "INDUSTRIQ",
    )
    return document


def premium_table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOX", (0, 0), (-1, -1), 0.7, GOLD),
    ])
