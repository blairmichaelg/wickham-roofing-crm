import hashlib
import html
import os
from pathlib import Path
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.services.pdf.constants import (
    BRAND_BLUE,
    BRAND_NAVY,
    BRAND_SLATE,
    COMPANY_EMAIL,
    COMPANY_NAME,
    COMPANY_PHONE,
    COMPANY_TAGLINE,
    FONT_INTER_BOLD,
    FONT_INTER_BOLD_ITALIC,
    FONT_INTER_ITALIC,
    FONT_INTER_REGULAR,
)

logger = structlog.get_logger("app.services.pdf.engine")

_FONT_BOOTSTRAPPED = False
_PRIMARY_FONT_FAMILY = "Helvetica"


def register_brand_fonts() -> str:
    """
    Centralized font-registration bootstrapper.
    Attempts to register Inter TTF font variants and family for XML <b>/<i> tag support.
    Falls back gracefully to standard PostScript 'Helvetica' if TTF files are missing or corrupt.
    """
    global _FONT_BOOTSTRAPPED, _PRIMARY_FONT_FAMILY
    if _FONT_BOOTSTRAPPED:
        return _PRIMARY_FONT_FAMILY

    try:
        if (
            FONT_INTER_REGULAR.exists()
            and FONT_INTER_BOLD.exists()
            and FONT_INTER_ITALIC.exists()
            and FONT_INTER_BOLD_ITALIC.exists()
        ):
            pdfmetrics.registerFont(TTFont("Inter", str(FONT_INTER_REGULAR)))
            pdfmetrics.registerFont(TTFont("Inter-Bold", str(FONT_INTER_BOLD)))
            pdfmetrics.registerFont(TTFont("Inter-Italic", str(FONT_INTER_ITALIC)))
            pdfmetrics.registerFont(TTFont("Inter-BoldItalic", str(FONT_INTER_BOLD_ITALIC)))
            pdfmetrics.registerFontFamily(
                "Inter",
                normal="Inter",
                bold="Inter-Bold",
                italic="Inter-Italic",
                boldItalic="Inter-BoldItalic",
            )
            _PRIMARY_FONT_FAMILY = "Inter"
            logger.info("brand_fonts_registered_success", family="Inter")
        else:
            _PRIMARY_FONT_FAMILY = "Helvetica"
            logger.debug(
                "brand_font_files_not_found_using_fallback",
                fallback="Helvetica",
                expected_dir=str(FONT_INTER_REGULAR.parent),
            )
    except Exception as exc:
        _PRIMARY_FONT_FAMILY = "Helvetica"
        logger.warning("brand_font_registration_failed_fallback_used", error=str(exc), fallback="Helvetica")

    _FONT_BOOTSTRAPPED = True
    return _PRIMARY_FONT_FAMILY


def get_font_name(variant: str = "normal") -> str:
    """
    Helper to resolve the active font name variant.
    Variants: 'normal', 'bold', 'italic', 'bold_italic'.
    """
    family = register_brand_fonts()
    if family == "Inter":
        variants = {
            "normal": "Inter",
            "bold": "Inter-Bold",
            "italic": "Inter-Italic",
            "bold_italic": "Inter-BoldItalic",
        }
        return variants.get(variant, "Inter")
    # Standard Helvetica fallback
    variants_helv = {
        "normal": "Helvetica",
        "bold": "Helvetica-Bold",
        "italic": "Helvetica-Oblique",
        "bold_italic": "Helvetica-BoldOblique",
    }
    return variants_helv.get(variant, "Helvetica")


def truncate_text_to_width(
    text: str,
    font_name: str,
    font_size: float,
    max_width: float,
    ellipsis: str = "...",
) -> str:
    """
    Checks string width against available width using pdfmetrics.stringWidth.
    Truncates and adds ellipsis if text overflows available horizontal space.
    """
    if not text:
        return ""
    try:
        width = pdfmetrics.stringWidth(text, font_name, font_size)
        if width <= max_width:
            return text
        
        # Iteratively truncate until it fits with ellipsis
        ellipsis_width = pdfmetrics.stringWidth(ellipsis, font_name, font_size)
        target_width = max_width - ellipsis_width
        if target_width <= 0:
            return ellipsis

        for i in range(len(text) - 1, 0, -1):
            sub = text[:i]
            if pdfmetrics.stringWidth(sub, font_name, font_size) <= target_width:
                return sub + ellipsis
        return ellipsis
    except Exception:
        return text


class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas for ReportLab that computes total page count (Page X of Y).
    Collects page states during showPage() and renders final footers upon save().
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count: int) -> None:
        self.saveState()
        font_reg = get_font_name("normal")
        self.setFont(font_reg, 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Footer format: Hash & Title Left, Page X of Y Right
        doc_type = getattr(self, "_doc_type", "DOC")
        job_id = getattr(self, "_job_id", "N/A")
        doc_hash = hashlib.sha256(f"{job_id}|{doc_type}".encode()).hexdigest()[:12]
        
        self.drawString(50, 30, f"Wickham Roofing LLC — Document Hash: {doc_hash}")
        self.drawRightString(560, 30, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


class PDFEngine:
    def __init__(self) -> None:
        register_brand_fonts()
        self.styles = getSampleStyleSheet()
        self._build_custom_styles()
        logger.info("pdf_generator_initialized")

    def _build_custom_styles(self) -> None:
        base_normal = self.styles["Normal"]
        font_reg = get_font_name("normal")
        font_bold = get_font_name("bold")
        font_italic = get_font_name("italic")

        self.custom_styles = {
            "Title": ParagraphStyle(
                "Title",
                parent=self.styles["Heading1"],
                fontSize=16,
                leading=20,
                fontName=font_bold,
                alignment=1,
            ),
            "SectionHeading": ParagraphStyle(
                "SectionHeading",
                parent=self.styles["Heading2"],
                fontSize=11,
                leading=15,
                fontName=font_bold,
                spaceBefore=12,
                spaceAfter=6,
            ),
            "BodyText": ParagraphStyle(
                "BodyText",
                parent=base_normal,
                fontSize=10,
                leading=14,
                fontName=font_reg,
                alignment=4,  # TA_JUSTIFY
            ),
            "StatWarning": ParagraphStyle(
                "StatWarning",
                parent=base_normal,
                fontSize=10,
                leading=14,
                fontName=font_bold,
                textColor=colors.darkred,
            ),
            "FinePrint": ParagraphStyle(
                "FinePrint",
                parent=base_normal,
                fontSize=8,
                leading=11,
                fontName=font_reg,
                textColor=colors.dimgrey,
                alignment=4,
            ),
            "DocControl": ParagraphStyle(
                "DocControl",
                parent=base_normal,
                fontSize=10,
                leading=13,
                fontName=font_italic,
                textColor=colors.darkgrey,
                alignment=2,  # TA_RIGHT
            ),
            "Normal": base_normal,
        }

    def _universal_letterhead(self, canvas_obj: Any, doc: BaseDocTemplate) -> None:
        """Universal callback for page headers and initial letterhead."""
        canvas_obj.saveState()

        page_num = canvas_obj.getPageNumber()
        doc_type = getattr(doc, "doc_type", "UNKNOWN")
        canvas_obj._doc_type = doc_type
        canvas_obj._job_id = getattr(doc, "job_id", "N/A")

        font_bold = get_font_name("bold")
        font_reg = get_font_name("normal")

        # Draw top letterhead header on page 2+ (or on page 1 for non-custom cover docs)
        if page_num > 1 or doc_type != "HOMEOWNER_INSPECTION_REPORT":
            canvas_obj.setFont(font_bold, 14)
            canvas_obj.setFillColor(BRAND_BLUE)
            
            comp_name = truncate_text_to_width(COMPANY_NAME, font_bold, 14, 350)
            canvas_obj.drawString(50, 750, comp_name)

            canvas_obj.setFont(font_bold, 9)
            canvas_obj.setFillColor(BRAND_SLATE)
            canvas_obj.drawString(50, 736, COMPANY_TAGLINE)

            canvas_obj.setFont(font_reg, 9)
            canvas_obj.setFillColor(colors.HexColor("#64748b"))
            canvas_obj.drawString(50, 723, f"Ochlocknee, GA  |  Phone: {COMPANY_PHONE}  |  Email: {COMPANY_EMAIL}")

            logo_path = "app/static/logo.png"
            if os.path.exists(logo_path):
                try:
                    canvas_obj.drawImage(logo_path, 430, 712, width=130, height=52, preserveAspectRatio=True)
                except Exception as e:
                    logger.warning("letterhead_logo_render_failed", error=str(e))

            # Line under header
            canvas_obj.setStrokeColor(BRAND_BLUE)
            canvas_obj.setLineWidth(1.5)
            canvas_obj.line(50, 706, 560, 706)

        # Footer rendered via NumberedCanvas if active; fallback baseline if standard canvas
        if not isinstance(canvas_obj, NumberedCanvas):
            canvas_obj.setFont(font_reg, 8)
            canvas_obj.setFillColor(colors.HexColor("#64748b"))
            job_id = getattr(doc, "job_id", "N/A")
            doc_hash = hashlib.sha256(f"{job_id}|{doc_type}".encode()).hexdigest()[:12]
            canvas_obj.drawString(50, 30, f"Wickham Roofing LLC — Official Document Hash: {doc_hash}")
            canvas_obj.drawRightString(560, 30, f"Page {page_num}")

        canvas_obj.restoreState()

    def _build_signature_block(
        self,
        title1: str = "Homeowner Signature",
        title2: str = "Contractor Signature",
        include_witness: bool = False,
    ) -> KeepTogether:
        """Returns a KeepTogether flowable for clean signature blocks."""
        story: list = []
        story.append(Spacer(1, 14))

        data = [
            ["", ""],
            [title1, "Date"],
            ["(Printed Name)", "(MM/DD/YYYY)"],
            ["", ""],
            [title2, "Date"],
            ["(Printed Name)", "(MM/DD/YYYY)"],
        ]

        if include_witness:
            data.extend([
                ["", ""],
                ["Witness / Notary Signature", "Date"],
                ["(Printed Name)", "(MM/DD/YYYY)"],
            ])

        font_bold = get_font_name("bold")
        t = Table(data, colWidths=[350, 160])
        style = [
            ("LINEABOVE", (0, 1), (0, 1), 1.2, colors.HexColor("#334155")),
            ("LINEABOVE", (1, 1), (1, 1), 1.2, colors.HexColor("#334155")),
            ("LINEABOVE", (0, 4), (0, 4), 1.2, colors.HexColor("#334155")),
            ("LINEABOVE", (1, 4), (1, 4), 1.2, colors.HexColor("#334155")),
            ("FONTNAME", (0, 1), (-1, 1), font_bold),
            ("FONTNAME", (0, 4), (-1, 4), font_bold),
            ("FONTSIZE", (0, 1), (-1, 1), 9),
            ("FONTSIZE", (0, 4), (-1, 4), 9),
            ("PADDING", (0, 0), (-1, -1), 3),
            ("FONTSIZE", (0, 2), (1, 2), 8),
            ("TEXTCOLOR", (0, 2), (1, 2), colors.HexColor("#64748b")),
            ("FONTSIZE", (0, 5), (1, 5), 8),
            ("TEXTCOLOR", (0, 5), (1, 5), colors.HexColor("#64748b")),
            ("BOTTOMPADDING", (0, 2), (-1, 2), 16),
        ]

        if include_witness:
            style.extend([
                ("LINEABOVE", (0, 7), (0, 7), 1.2, colors.HexColor("#334155")),
                ("LINEABOVE", (1, 7), (1, 7), 1.2, colors.HexColor("#334155")),
                ("FONTNAME", (0, 7), (-1, 7), font_bold),
                ("FONTSIZE", (0, 7), (-1, 7), 9),
                ("FONTSIZE", (0, 8), (1, 8), 8),
                ("TEXTCOLOR", (0, 8), (1, 8), colors.HexColor("#64748b")),
            ])

        t.setStyle(TableStyle(style))  # type: ignore[arg-type]
        story.append(t)
        return KeepTogether(story)

    def _get_doc_template(
        self,
        filepath: str,
        top_margin: int = 130,
        job_id: str = "N/A",
        doc_type: str = "DOC",
    ) -> BaseDocTemplate:
        """Returns a BaseDocTemplate configured with a Frame that prevents overlapping with the header."""
        doc = BaseDocTemplate(filepath, pagesize=letter, leftMargin=50, rightMargin=50, topMargin=top_margin, bottomMargin=50)
        doc.job_id = job_id  # type: ignore[attr-defined]
        doc.doc_type = doc_type  # type: ignore[attr-defined]
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
        template = PageTemplate(id="standard", frames=frame, onPage=self._universal_letterhead)
        doc.addPageTemplates([template])
        return doc

    def _build_metadata_table(self, job: dict) -> Table:
        """Constructs a structured metadata table for the top of documents."""
        address = f"{job.get('address_line1', '')}, {job.get('city', '')}, {job.get('state', '')} {job.get('postal_code', '')}".strip(" ,")
        carrier = job.get("insurance_carrier") or job.get("insurer_name") or "N/A"
        claim_no = job.get("claim_number") or "N/A"
        phone = job.get("phone") or "N/A"

        data = [
            [
                Paragraph("<b>Homeowner:</b>", self.custom_styles["BodyText"]),
                Paragraph(html.escape(job.get("homeowner_name", "N/A")), self.custom_styles["BodyText"]),
                Paragraph("<b>Job ID:</b>", self.custom_styles["BodyText"]),
                Paragraph(html.escape(job.get("id", "N/A")), self.custom_styles["BodyText"]),
            ],
            [
                Paragraph("<b>Property Address:</b>", self.custom_styles["BodyText"]),
                Paragraph(html.escape(address), self.custom_styles["BodyText"]),
                Paragraph("<b>Insurance Carrier:</b>", self.custom_styles["BodyText"]),
                Paragraph(html.escape(carrier), self.custom_styles["BodyText"]),
            ],
            [
                Paragraph("<b>Claim Number:</b>", self.custom_styles["BodyText"]),
                Paragraph(html.escape(claim_no), self.custom_styles["BodyText"]),
                Paragraph("<b>Phone / Contact:</b>", self.custom_styles["BodyText"]),
                Paragraph(html.escape(phone), self.custom_styles["BodyText"]),
            ],
        ]
        t = Table(data, colWidths=[105, 170, 105, 130])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f1f5f9")),
            ("PADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return t

    def _box_warning(self, title: str, text: str, border_color: Any = None) -> Table:
        """Wraps a critical legal warning inside a styled Table box."""
        if border_color is None:
            border_color = BRAND_BLUE

        t_data = [
            [Paragraph(f"<b>{title}</b>", ParagraphStyle("WarnTitle", parent=self.custom_styles["SectionHeading"], textColor=border_color, fontSize=10, leading=13, spaceBefore=0, spaceAfter=2))],
            [Paragraph(text, self.custom_styles["StatWarning"])],
        ]
        t = Table(t_data, colWidths=[510])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 1.5, border_color),
            ("LINEBELOW", (0, 0), (0, 0), 0.8, border_color),
            ("PADDING", (0, 0), (-1, -1), 7),
        ]))
        return t
