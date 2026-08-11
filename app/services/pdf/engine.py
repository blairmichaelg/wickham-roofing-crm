import hashlib
import html
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
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

logger = structlog.get_logger("app.services.pdf")
from app.services.pdf.constants import COMPANY_EMAIL, COMPANY_NAME, COMPANY_PHONE


class PDFEngine:
    def __init__(self) -> None:
        self.styles = getSampleStyleSheet()
        self._build_custom_styles()
        logger.info("pdf_generator_initialized")


    def _build_custom_styles(self) -> None:
        base_normal = self.styles["Normal"]
        self.custom_styles = {
            "Title": ParagraphStyle(
                "Title", parent=self.styles["Heading1"], fontSize=16, fontName="Helvetica-Bold", alignment=1
            ),
            "SectionHeading": ParagraphStyle(
                "SectionHeading", parent=self.styles["Heading2"], fontSize=11, fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=6
            ),
            "BodyText": ParagraphStyle(
                "BodyText", parent=base_normal, fontSize=10, alignment=4 # 4=TA_JUSTIFY
            ),
            "StatWarning": ParagraphStyle(
                "StatWarning", parent=base_normal, fontSize=10, fontName="Helvetica-Bold", textColor=colors.darkred
            ),
            "FinePrint": ParagraphStyle(
                "FinePrint", parent=base_normal, fontSize=8, textColor=colors.dimgrey, alignment=4
            ),
            "DocControl": ParagraphStyle(
                "DocControl", parent=base_normal, fontSize=10, fontName="Helvetica-Oblique", textColor=colors.darkgrey, alignment=2 # 2=TA_RIGHT
            ),
            "Normal": base_normal,
        }


    def _universal_letterhead(self, canvas: Any, doc: BaseDocTemplate) -> None:
        """Universal callback for page headers and footers."""
        canvas.saveState()

        page_num = canvas.getPageNumber()
        doc_type = getattr(doc, 'doc_type', 'UNKNOWN')

        # Draw top letterhead header on page 2+ (or on page 1 for non-custom cover docs)
        if page_num > 1 or doc_type != "HOMEOWNER_INSPECTION_REPORT":
            canvas.setFont("Helvetica-Bold", 14)
            canvas.setFillColor(colors.HexColor("#1e3a8a"))
            canvas.drawString(50, 750, COMPANY_NAME)

            canvas.setFont("Helvetica-Bold", 9)
            canvas.setFillColor(colors.HexColor("#4b5563"))
            canvas.drawString(50, 736, "Residential & Commercial Storm Restoration Specialists")

            canvas.setFont("Helvetica", 9)
            canvas.setFillColor(colors.HexColor("#6b7280"))
            canvas.drawString(50, 723, f"Ochlocknee, GA  |  Phone: {COMPANY_PHONE}  |  Email: {COMPANY_EMAIL}")

            import os
            logo_path = "app/static/logo.png"
            if os.path.exists(logo_path):
                try:
                    # Page width=612pt. Right margin=560pt. Placing left edge at 430pt gives 130pt logo width cleanly inside margins.
                    canvas.drawImage(logo_path, 430, 712, width=130, height=52, preserveAspectRatio=True)
                except Exception as e:
                    logger.warning("letterhead_logo_render_failed", error=str(e))

            # Line under header
            canvas.setStrokeColor(colors.HexColor("#1e3a8a"))
            canvas.setLineWidth(1.5)
            canvas.line(50, 706, 560, 706)

        # Footer (all pages)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        job_id = getattr(doc, 'job_id', 'N/A')
        doc_hash = hashlib.sha256(f"{job_id}|{doc_type}".encode()).hexdigest()[:12]
        canvas.drawString(50, 30, f"Wickham Roofing LLC — Official Document Hash: {doc_hash}")
        canvas.drawRightString(560, 30, f"Page {page_num}")

        canvas.restoreState()


    def _build_signature_block(self, title1: str = "Homeowner Signature", title2: str = "Contractor Signature", include_witness: bool = False) -> KeepTogether:
        """Returns a KeepTogether flowable for clean signature blocks."""
        story: list = []
        story.append(Spacer(1, 14))

        # Two columns: Signature and Date
        data = [
            ["", ""],
            [title1, "Date"],
            ["(Printed Name)", "(MM/DD/YYYY)"],
            ["", ""],
            [title2, "Date"],
            ["(Printed Name)", "(MM/DD/YYYY)"]
        ]

        if include_witness:
            data.extend([
                ["", ""],
                ["Witness / Notary Signature", "Date"],
                ["(Printed Name)", "(MM/DD/YYYY)"]
            ])

        t = Table(data, colWidths=[350, 160])
        style = [
            ('LINEABOVE', (0,1), (0,1), 1.2, colors.HexColor("#334155")),
            ('LINEABOVE', (1,1), (1,1), 1.2, colors.HexColor("#334155")),
            ('LINEABOVE', (0,4), (0,4), 1.2, colors.HexColor("#334155")),
            ('LINEABOVE', (1,4), (1,4), 1.2, colors.HexColor("#334155")),
            ('FONTNAME', (0,1), (-1,1), 'Helvetica-Bold'),
            ('FONTNAME', (0,4), (-1,4), 'Helvetica-Bold'),
            ('FONTSIZE', (0,1), (-1,1), 9),
            ('FONTSIZE', (0,4), (-1,4), 9),
            ('PADDING', (0,0), (-1,-1), 3),
            ('FONTSIZE', (0,2), (1,2), 8),
            ('TEXTCOLOR', (0,2), (1,2), colors.HexColor("#64748b")),
            ('FONTSIZE', (0,5), (1,5), 8),
            ('TEXTCOLOR', (0,5), (1,5), colors.HexColor("#64748b")),
            ('BOTTOMPADDING', (0,2), (-1,2), 16),
        ]

        if include_witness:
            style.extend([
                ('LINEABOVE', (0,7), (0,7), 1.2, colors.HexColor("#334155")),
                ('LINEABOVE', (1,7), (1,7), 1.2, colors.HexColor("#334155")),
                ('FONTNAME', (0,7), (-1,7), 'Helvetica-Bold'),
                ('FONTSIZE', (0,7), (-1,7), 9),
                ('FONTSIZE', (0,8), (1,8), 8),
                ('TEXTCOLOR', (0,8), (1,8), colors.HexColor("#64748b")),
            ])

        t.setStyle(TableStyle(style)) # type: ignore[arg-type]
        story.append(t)

        return KeepTogether(story)


    def _get_doc_template(self, filepath: str, top_margin: int = 130, job_id: str = "N/A", doc_type: str = "DOC") -> BaseDocTemplate:
        """Returns a BaseDocTemplate configured with a Frame that prevents overlapping with the header."""
        doc = BaseDocTemplate(filepath, pagesize=letter, leftMargin=50, rightMargin=50, topMargin=top_margin, bottomMargin=50)
        doc.job_id = job_id # type: ignore[attr-defined]
        doc.doc_type = doc_type # type: ignore[attr-defined]
        # letter height is 792. Leave space at the top.
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
        template = PageTemplate(id='standard', frames=frame, onPage=self._universal_letterhead)
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
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
            ('BACKGROUND', (0,0), (0,-1), colors.HexColor("#f1f5f9")),
            ('BACKGROUND', (2,0), (2,-1), colors.HexColor("#f1f5f9")),
            ('PADDING', (0,0), (-1,-1), 5),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        return t


    def _box_warning(self, title: str, text: str, border_color: Any = None) -> Table:
        """Wraps a critical legal warning inside a styled Table box."""
        if border_color is None:
            border_color = colors.HexColor("#1e3a8a")
        
        t_data = [
            [Paragraph(f"<b>{title}</b>", ParagraphStyle("WarnTitle", parent=self.custom_styles["SectionHeading"], textColor=border_color, fontSize=10, leading=13, spaceBefore=0, spaceAfter=2))],
            [Paragraph(text, self.custom_styles["StatWarning"])]
        ]
        t = Table(t_data, colWidths=[510])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
            ('BOX', (0,0), (-1,-1), 1.5, border_color),
            ('LINEBELOW', (0,0), (0,0), 0.8, border_color),
            ('PADDING', (0,0), (-1,-1), 7),
        ]))
        return t
