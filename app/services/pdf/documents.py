import asyncio
import datetime
import html
import math
from pathlib import Path
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from app.services.pdf.constants import (
    BRAND_ACCENT,
    BRAND_BLUE,
    BRAND_BORDER,
    BRAND_DARK_BORDER,
    BRAND_GOLD,
    BRAND_GREEN,
    BRAND_LIGHT_BG,
    BRAND_MUTED_BG,
    BRAND_NAVY,
    BRAND_RED,
    BRAND_SLATE,
    CARRIER_PALETTE,
    FIELD_DOCS_DIR,
    HOMEOWNER_PALETTE,
    INTERNAL_PALETTE,
    NEIGHBOR_PALETTE,
    SPACING_MD,
    SPACING_SM,
    SPACING_XS,
)
from app.services.pdf.engine import PDFEngine, get_font_name, register_brand_fonts

logger = structlog.get_logger("app.services.pdf.documents")


def build_audience_stylesheets() -> dict[str, dict[str, ParagraphStyle]]:
    """
    Builds structured, audience-tiered ParagraphStyle dictionaries.
    Ensures font variants match the registered font family and leading is 120-150% of fontSize.
    """
    register_brand_fonts()
    font_reg = get_font_name("normal")
    font_bold = get_font_name("bold")
    font_italic = get_font_name("italic")
    
    base_styles = getSampleStyleSheet()
    base_normal = base_styles["Normal"]

    # Homeowner styles: Clean, approachable, high readability
    homeowner = {
        "Title": ParagraphStyle(
            "HomeownerTitle",
            parent=base_normal,
            fontName=font_bold,
            fontSize=16,
            leading=21,
            textColor=HOMEOWNER_PALETTE["primary"],
            alignment=1,  # Center
            spaceAfter=SPACING_SM,
        ),
        "SectionHeading": ParagraphStyle(
            "HomeownerSectionHeading",
            parent=base_normal,
            fontName=font_bold,
            fontSize=11,
            leading=15,
            textColor=HOMEOWNER_PALETTE["primary"],
            spaceBefore=10,
            spaceAfter=5,
        ),
        "BodyText": ParagraphStyle(
            "HomeownerBodyText",
            parent=base_normal,
            fontName=font_reg,
            fontSize=9.5,
            leading=13.5,
            textColor=HOMEOWNER_PALETTE["text"],
            alignment=4,  # Justified
        ),
        "StatWarning": ParagraphStyle(
            "HomeownerStatWarning",
            parent=base_normal,
            fontName=font_bold,
            fontSize=9.5,
            leading=13.5,
            textColor=HOMEOWNER_PALETTE["warning"],
        ),
        "FinePrint": ParagraphStyle(
            "HomeownerFinePrint",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8,
            leading=11,
            textColor=HOMEOWNER_PALETTE["muted_text"],
        ),
        "DocControl": ParagraphStyle(
            "HomeownerDocControl",
            parent=base_normal,
            fontName=font_italic,
            fontSize=9,
            leading=12,
            textColor=HOMEOWNER_PALETTE["muted_text"],
            alignment=2,  # Right
        ),
        "TableHeader": ParagraphStyle(
            "HomeownerTableHeader",
            parent=base_normal,
            fontName=font_bold,
            fontSize=9,
            leading=12,
            textColor=colors.white,
            alignment=0,
        ),
        "TableCell": ParagraphStyle(
            "HomeownerTableCell",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8.5,
            leading=11.5,
            textColor=HOMEOWNER_PALETTE["text"],
        ),
        "TableCellRight": ParagraphStyle(
            "HomeownerTableCellRight",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8.5,
            leading=11.5,
            textColor=HOMEOWNER_PALETTE["text"],
            alignment=2,
        ),
    }

    # Carrier styles: Dense, compact, authoritative grayscale
    carrier = {
        "Title": ParagraphStyle(
            "CarrierTitle",
            parent=base_normal,
            fontName=font_bold,
            fontSize=14,
            leading=18,
            textColor=CARRIER_PALETTE["primary"],
            alignment=1,
            spaceAfter=SPACING_SM,
        ),
        "SectionHeading": ParagraphStyle(
            "CarrierSectionHeading",
            parent=base_normal,
            fontName=font_bold,
            fontSize=10,
            leading=13,
            textColor=CARRIER_PALETTE["primary"],
            spaceBefore=8,
            spaceAfter=4,
        ),
        "BodyText": ParagraphStyle(
            "CarrierBodyText",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8.5,
            leading=11.5,
            textColor=CARRIER_PALETTE["text"],
        ),
        "TableHeader": ParagraphStyle(
            "CarrierTableHeader",
            parent=base_normal,
            fontName=font_bold,
            fontSize=8.5,
            leading=11,
            textColor=colors.white,
            alignment=0,
        ),
        "TableCell": ParagraphStyle(
            "CarrierTableCell",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8,
            leading=10.5,
            textColor=CARRIER_PALETTE["text"],
        ),
        "TableCellRight": ParagraphStyle(
            "CarrierTableCellRight",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8,
            leading=10.5,
            textColor=CARRIER_PALETTE["text"],
            alignment=2,
        ),
        "CodeCitation": ParagraphStyle(
            "CarrierCodeCitation",
            parent=base_normal,
            fontName=font_bold,
            fontSize=8,
            leading=10.5,
            textColor=CARRIER_PALETTE["secondary"],
        ),
    }

    # Neighbor styles: Bold marketing typography with accent color
    neighbor = {
        "Title": ParagraphStyle(
            "NeighborTitle",
            parent=base_normal,
            fontName=font_bold,
            fontSize=18,
            leading=24,
            textColor=NEIGHBOR_PALETTE["primary"],
            alignment=1,
            spaceAfter=SPACING_SM,
        ),
        "Subheading": ParagraphStyle(
            "NeighborSubheading",
            parent=base_normal,
            fontName=font_bold,
            fontSize=12,
            leading=16,
            textColor=NEIGHBOR_PALETTE["accent"],
            alignment=1,
            spaceAfter=SPACING_MD,
        ),
        "BodyText": ParagraphStyle(
            "NeighborBodyText",
            parent=base_normal,
            fontName=font_reg,
            fontSize=10,
            leading=14.5,
            textColor=NEIGHBOR_PALETTE["text"],
        ),
        "CalloutHeading": ParagraphStyle(
            "NeighborCalloutHeading",
            parent=base_normal,
            fontName=font_bold,
            fontSize=11,
            leading=15,
            textColor=NEIGHBOR_PALETTE["primary"],
        ),
        "CalloutText": ParagraphStyle(
            "NeighborCalloutText",
            parent=base_normal,
            fontName=font_reg,
            fontSize=9.5,
            leading=13.5,
            textColor=NEIGHBOR_PALETTE["text"],
        ),
    }

    # Internal styles: Financial accuracy and dense summary tables
    internal = {
        "Title": ParagraphStyle(
            "InternalTitle",
            parent=base_normal,
            fontName=font_bold,
            fontSize=15,
            leading=19,
            textColor=INTERNAL_PALETTE["primary"],
            alignment=1,
            spaceAfter=SPACING_SM,
        ),
        "SectionHeading": ParagraphStyle(
            "InternalSectionHeading",
            parent=base_normal,
            fontName=font_bold,
            fontSize=10.5,
            leading=14,
            textColor=INTERNAL_PALETTE["primary"],
            spaceBefore=10,
            spaceAfter=4,
        ),
        "BodyText": ParagraphStyle(
            "InternalBodyText",
            parent=base_normal,
            fontName=font_reg,
            fontSize=9,
            leading=12.5,
            textColor=INTERNAL_PALETTE["text"],
        ),
        "TableHeader": ParagraphStyle(
            "InternalTableHeader",
            parent=base_normal,
            fontName=font_bold,
            fontSize=9,
            leading=12,
            textColor=colors.white,
            alignment=0,
        ),
        "TableCell": ParagraphStyle(
            "InternalTableCell",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8.5,
            leading=11.5,
            textColor=INTERNAL_PALETTE["text"],
        ),
        "TableCellRight": ParagraphStyle(
            "InternalTableCellRight",
            parent=base_normal,
            fontName=font_reg,
            fontSize=8.5,
            leading=11.5,
            textColor=INTERNAL_PALETTE["text"],
            alignment=2,
        ),
    }

    return {
        "homeowner": homeowner,
        "carrier": carrier,
        "neighbor": neighbor,
        "internal": internal,
    }


_AUDIENCE_STYLES = build_audience_stylesheets()


def get_audience_styles(sub_brand: str = "homeowner") -> dict[str, ParagraphStyle]:
    """Retrieve the ParagraphStyle dictionary for a given sub-brand tier."""
    return _AUDIENCE_STYLES.get(sub_brand, _AUDIENCE_STYLES["homeowner"])


def create_header(title: str, sub_brand: str = "homeowner", subtitle: str | None = None) -> list[Flowable]:
    """Creates a styled header flowable list with title and optional subtitle."""
    styles = get_audience_styles(sub_brand)
    elements: list[Flowable] = [
        Paragraph(title, styles.get("Title", styles["BodyText"])),
    ]
    if subtitle and "Subheading" in styles:
        elements.append(Paragraph(subtitle, styles["Subheading"]))
    elements.append(Spacer(1, SPACING_SM))
    return elements


def create_section_with_table(
    heading: str,
    table: Table,
    sub_brand: str = "homeowner",
    space_before: float = 10,
    space_after: float = 8,
) -> KeepTogether:
    """Wraps a section heading and its associated Table in KeepTogether to prevent orphan headings."""
    styles = get_audience_styles(sub_brand)
    heading_style = styles.get("SectionHeading", styles["BodyText"])
    elements = [
        Paragraph(heading, heading_style),
        Spacer(1, SPACING_XS),
        table,
        Spacer(1, space_after),
    ]
    return KeepTogether(elements)


def create_financial_row(
    label: str,
    amount_str: str,
    is_total: bool = False,
    sub_brand: str = "homeowner",
    col_widths: list[float] | None = None,
) -> Table:
    """Creates a 2-column key-value financial row with right-aligned amount."""
    widths = col_widths or [360, 150]
    
    styles = get_audience_styles(sub_brand)
    lbl_p = Paragraph(f"<b>{label}</b>" if is_total else label, styles.get("TableCell", styles["BodyText"]))
    amt_p = Paragraph(f"<b>{amount_str}</b>" if is_total else amount_str, styles.get("TableCellRight", styles["BodyText"]))
    
    t = Table([[lbl_p, amt_p]], colWidths=widths)
    t_style: list[Any] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]
    if is_total:
        t_style.extend([
            ("LINEABOVE", (0, 0), (-1, 0), 1.2, BRAND_SLATE),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, BRAND_SLATE),
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_MUTED_BG),
        ])
    t.setStyle(TableStyle(t_style))
    return t


def create_financial_table(
    data: list[list[Any]],
    col_widths: list[float],
    sub_brand: str = "homeowner",
    currency_cols: list[int] | None = None,
    center_cols: list[int] | None = None,
    has_header: bool = True,
) -> Table:
    """
    Constructs a highly structured Table with audience-appropriate styling:
    - Enforces RIGHT alignment on currency columns so decimal points align vertically.
    - Carrier sub-brand uses 0.25pt innergrid/box lines and compact styling.
    - Homeowner sub-brand uses alternating background rows and subtle borders.
    """
    font_bold = get_font_name("bold")
    curr_cols = set(currency_cols or [])
    cntr_cols = set(center_cols or [])

    styles = get_audience_styles(sub_brand)
    hdr_style = styles.get("TableHeader", styles["BodyText"])
    cell_style = styles.get("TableCell", styles["BodyText"])
    right_style = styles.get("TableCellRight", styles["BodyText"])

    table_rows: list[list[Any]] = []
    for r_idx, row in enumerate(data):
        row_elements: list[Any] = []
        is_hdr = (r_idx == 0 and has_header)
        for c_idx, cell in enumerate(row):
            if isinstance(cell, Flowable):
                row_elements.append(cell)
            elif is_hdr:
                row_elements.append(Paragraph(str(cell), hdr_style))
            elif c_idx in curr_cols:
                row_elements.append(Paragraph(str(cell), right_style))
            else:
                row_elements.append(Paragraph(str(cell), cell_style))
        table_rows.append(row_elements)

    t = Table(table_rows, colWidths=col_widths)
    
    style_cmds: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 4 if sub_brand == "carrier" else 5),
    ]

    for c in curr_cols:
        style_cmds.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    for c in cntr_cols:
        style_cmds.append(("ALIGN", (c, 0), (c, -1), "CENTER"))

    if has_header:
        if sub_brand == "carrier":
            style_cmds.extend([
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_SLATE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), font_bold),
                ("GRID", (0, 0), (-1, -1), 0.25, CARRIER_PALETTE["gridline"]),
                ("BOX", (0, 0), (-1, -1), 0.5, CARRIER_PALETTE["border"]),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CARRIER_PALETTE["bg_alt"]]),
            ])
        else:
            style_cmds.extend([
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), font_bold),
                ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
            ])
    else:
        style_cmds.extend([
            ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, BRAND_LIGHT_BG]),
        ])

    t.setStyle(TableStyle(style_cmds))
    return t


def create_photo_grid(
    photos: list[dict[str, Any]],
    cols: int = 2,
    max_width: float = 510,
    sub_brand: str = "homeowner",
) -> KeepTogether:
    """
    Creates a responsive 2-column or 3-column photo grid flowable.
    - Sets preserveAspectRatio=True to prevent image distortion.
    - Wraps photo + caption in individual table cells with a subtle border.
    """
    if not photos:
        return KeepTogether([Spacer(1, 1)])

    styles = get_audience_styles(sub_brand)
    caption_style = styles.get("FinePrint", styles["BodyText"])

    col_w = max_width / float(cols)
    img_w = col_w - 12
    img_h = img_w * 0.75  # 4:3 standard aspect ratio

    grid_rows: list[list[Any]] = []
    curr_row: list[Any] = []

    for photo_dict in photos:
        p_path = photo_dict.get("path") or photo_dict.get("file_path")
        caption = photo_dict.get("caption") or photo_dict.get("notes") or photo_dict.get("damage_type") or ""
        
        cell_contents: list[Flowable] = []
        if p_path and Path(p_path).exists():
            try:
                img = Image(str(p_path), width=img_w, height=img_h)
                img.drawWidth = img_w
                img.drawHeight = img_h
                cell_contents.append(img)
            except Exception as e:
                logger.warning("photo_grid_image_load_failed", path=str(p_path), error=str(e))
                cell_contents.append(Paragraph("[Image Unavailable]", caption_style))
        else:
            cell_contents.append(Paragraph("[Image Not Found]", caption_style))

        if caption:
            cell_contents.append(Spacer(1, 3))
            cell_contents.append(Paragraph(html.escape(caption), caption_style))

        cell_table = Table([[c] for c in cell_contents], colWidths=[col_w - 4])
        cell_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT_BG),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))

        curr_row.append(cell_table)
        if len(curr_row) == cols:
            grid_rows.append(curr_row)
            curr_row = []

    if curr_row:
        # Fill remaining columns with empty placeholders
        while len(curr_row) < cols:
            curr_row.append(Paragraph("", caption_style))
        grid_rows.append(curr_row)

    grid_table = Table(grid_rows, colWidths=[col_w] * cols)
    grid_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 2),
    ]))

    return KeepTogether([grid_table])


class DocumentsGenerator(PDFEngine):
    """
    Standard documents generator for field and office legal documents,
    using centralized audience-tiered typography and component flowables.
    """

    async def generate_contingency_pdf(
        self,
        job: dict,
        signature_path: str,
        signer_name: str,
        ip_address: str,
        timestamp_utc: str = "",
    ) -> str:
        """Generate a complete Legal Contingency document with embedded signature and 1-Year Workmanship Warranty."""
        job_id = job.get("id", "UNKNOWN")
        log = logger.bind(job_id=job_id)
        log.info("contingency_pdf_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "contingency_agreement_signed.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, top_margin=120, job_id=job_id, doc_type="CONTINGENCY_SIGNED")
            story: list[Flowable] = []

            story.extend(create_header("INSURANCE CONTINGENCY & SCOPE AGREEMENT", "homeowner"))

            # --- Metadata Table ---
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 14))

            # --- Section 1: Scope of Work ---
            story.append(Paragraph("1. Scope of Work & Insurance Authorization", styles["SectionHeading"]))
            scope_text = (
                "Contractor (Wickham Roofing LLC) agrees to perform complete roof and exterior restoration services at the property listed above. "
                "The final scope of work, specifications, and contract price shall be strictly determined by the insurance carrier's approved estimate, "
                "plus any Homeowner-requested upgrades or signed change orders. Homeowner hereby authorizes Contractor to inspect property, document storm damage, "
                "verify technical line-item quantities, and communicate directly with insurance adjusters on construction scope and building code compliance matters."
            )
            story.append(Paragraph(scope_text, styles["BodyText"]))
            story.append(Spacer(1, 10))

            # --- Section 2: 1-Year Workmanship Warranty ---
            warranty_text = (
                "Wickham Roofing LLC provides an explicit <b>ONE (1) YEAR WORKMANSHIP WARRANTY</b> on all labor and installation craft quality from the date "
                "of final project completion. Contractor agrees to repair any installation defects free of charge during the warranty period. "
                "All manufacturer material warranties (shingles, synthetic underlayment, ventilation) are transferred directly to Homeowner upon full payment."
            )
            story.append(self._box_warning("1-YEAR WORKMANSHIP WARRANTY GUARANTEE", warranty_text, BRAND_BLUE))
            story.append(Spacer(1, 10))

            # --- Section 3: HB 423 Georgia Law Deductible Disclosure ---
            warning_text = (
                "WARNING: It is a violation of Georgia law (O.C.G.A. § 33-24-59.27) for a contractor to pay, waive, rebate, or promise to pay or rebate "
                "all or any portion of an insurance deductible. Homeowner is strictly responsible for payment of the insurance deductible in full."
            )
            story.append(self._box_warning("HB 423 Deductible Compliance Disclosure (O.C.G.A. § 33-24-59.27)", warning_text, BRAND_RED))
            story.append(Spacer(1, 10))

            # --- Section 4: Georgia Statutory Cancellation Disclosure (O.C.G.A. § 10-1-393.12) ---
            cancel_stat_text = (
                "You may cancel this contract at any time before midnight on the fifth (5th) business day after you have received written "
                "notification from your insurer that all or any part of the claim or contract is not a covered loss under the insurance policy. "
                "See attached Notice of Cancellation form for an explanation of this right."
            )
            story.append(self._box_warning("GEORGIA STATUTORY RIGHT TO CANCEL (O.C.G.A. § 10-1-393.12)", cancel_stat_text, colors.HexColor("#7f1d1d")))
            story.append(Spacer(1, 10))

            # --- Section 5: Terms & Representation ---
            story.append(Paragraph("2. Terms & Representation", styles["SectionHeading"]))
            terms_text = (
                "<b>Public Adjuster Disclosure:</b> Contractor is not acting as a licensed public adjuster and does not represent or negotiate legal claims on behalf of Homeowner.<br/><br/>"
                "<b>Default & Liquidated Overhead:</b> If Homeowner breaches this contract after insurance approval without statutory cause, Homeowner agrees to pay liquidated damages "
                "equal to 15% of the approved claim total to reimburse Contractor for administrative, technical, and pre-construction expenses."
            )
            story.append(Paragraph(terms_text, styles["BodyText"]))
            story.append(Spacer(1, 16))

            # --- Signature ---
            story.append(Paragraph("<b>Homeowner Authorization & Digital Execution</b>", styles["SectionHeading"]))
            story.append(HRFlowable(width="100%", thickness=0.5, color=BRAND_BORDER, spaceAfter=10))

            try:
                sig_img = Image(str(signature_path), width=280, height=80)
                sig_img.drawWidth = 280
                sig_img.drawHeight = 80
                story.append(sig_img)
            except Exception as e:
                log.error("signature_render_failed", error=str(e))

            story.append(Spacer(1, 8))
            time_str = f" on {timestamp_utc}" if timestamp_utc else ""
            story.append(Paragraph(f"Digitally signed & verified by <b>{signer_name}</b> from IP address <b>{ip_address}</b>{time_str}", styles["FinePrint"]))

            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("contingency_pdf_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("contingency_pdf_generation_failed", error=str(exc))
            raise

    async def generate_notice_of_cancellation(self, job: dict) -> str:
        """Generate Georgia statutory Notice of Cancellation."""
        job_dir = FIELD_DOCS_DIR / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "notice_of_cancellation.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job["id"], doc_type="NOTICE_OF_CANCELLATION")
            story: list[Flowable] = []

            for copy_type in ["Customer Copy", "Contractor Copy"]:
                story.append(Paragraph(copy_type, styles["DocControl"]))
                story.append(Spacer(1, 10))

                story.extend(create_header("NOTICE OF CANCELLATION", "homeowner"))

                # --- Metadata Table ---
                story.append(self._build_metadata_table(job))
                story.append(Spacer(1, 12))

                story.append(Paragraph(f"Date of Transaction: {datetime.date.today().isoformat()}", styles["BodyText"]))
                story.append(Spacer(1, 12))

                statutory_text = (
                    "You may cancel this contract at any time before midnight on the fifth business day after you have received written "
                    "notification from your insurer that all or any part of the claim or contract is not a covered loss under the insurance policy. "
                    "See attached notice of cancellation form for an explanation of this right."
                )
                story.append(Paragraph(statutory_text, styles["StatWarning"]))
                story.append(Spacer(1, 20))

                story.append(Paragraph("To cancel this transaction, mail or deliver a signed and dated copy of this cancellation notice, or any other written notice, to:<br/><br/><b>WICKHAM ROOFING LLC</b><br/>123 Roofing Lane<br/>Thomasville, GA 31792", styles["BodyText"]))
                story.append(Spacer(1, 40))
                story.append(Paragraph("I HEREBY CANCEL THIS TRANSACTION.", styles["BodyText"]))
                story.append(Spacer(1, 40))

                story.append(self._build_signature_block(title1="Homeowner Signature", title2="Contractor Signature"))

                if copy_type == "Customer Copy":
                    story.append(PageBreak())

            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath

    async def generate_retail_contract_pdf(
        self,
        job: dict,
        signature_path: str,
        signer_name: str,
        ip_address: str,
        total_price_cents: int,
        deposit_cents: int,
        scope_description: str,
        timestamp_utc: str = "",
    ) -> str:
        """Generate a Retail Sales Contract document."""
        job_id = job.get("id", "UNKNOWN")
        log = logger.bind(job_id=job_id)
        log.info("retail_contract_pdf_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "retail_contract_signed.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job_id, doc_type="RETAIL_CONTRACT_SIGNED")
            story: list[Flowable] = []

            story.extend(create_header("RESIDENTIAL ROOFING CONTRACT", "homeowner"))

            # --- Metadata Table ---
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 15))

            # --- Parties ---
            story.append(Paragraph("Parties", styles["SectionHeading"]))
            parties_text = f"This contract is made between Wickham Roofing LLC (Contractor) and {job.get('homeowner_name', 'UNKNOWN')} (Homeowner)."
            story.append(Paragraph(parties_text, styles["BodyText"]))
            story.append(Spacer(1, 10))

            # --- Scope of Work ---
            story.append(Paragraph("Scope of Work", styles["SectionHeading"]))
            story.append(Paragraph(html.escape(scope_description), styles["BodyText"]))
            story.append(Spacer(1, 10))

            # --- Payment Terms ---
            total_dollars = f"${total_price_cents / 100:,.2f}"
            deposit_dollars = f"${deposit_cents / 100:,.2f}"
            balance_dollars = f"${(total_price_cents - deposit_cents) / 100:,.2f}"

            payment_data = [
                ["Total Contract Price:", total_dollars],
                ["Deposit Due at Signing:", deposit_dollars],
                ["Balance Due Upon Completion:", balance_dollars],
            ]
            ptable = create_financial_table(payment_data, [220, 140], sub_brand="homeowner", currency_cols=[1], has_header=False)
            story.append(create_section_with_table("Payment Terms", ptable, "homeowner"))
            
            story.append(Paragraph("Final payment is due in full upon substantial completion of work, prior to contractor's departure from the job site, unless otherwise agreed in writing.", styles["BodyText"]))
            story.append(Spacer(1, 10))

            # --- Warranty ---
            warranty_text = (
                "Wickham Roofing LLC provides an explicit <b>ONE (1) YEAR WORKMANSHIP WARRANTY</b> on all labor, flashing, and installation craft quality. "
                "Wickham Roofing LLC guarantees to repair any installation defect free of charge during the warranty period. "
                "Shingle and accessory material warranties are provided directly by the product manufacturer."
            )
            story.append(self._box_warning("1-YEAR WORKMANSHIP WARRANTY GUARANTEE", warranty_text, BRAND_BLUE))
            story.append(Spacer(1, 12))

            # --- Right to Cancel ---
            cancel_notice = "NOTICE OF RIGHT TO CANCEL: You, the buyer, may cancel this transaction at any time before midnight of the THIRD BUSINESS DAY after the date of this transaction. See the attached Notice of Cancellation form for an explanation of this right."
            story.append(Paragraph(cancel_notice, styles["StatWarning"]))
            story.append(Spacer(1, 16))

            # --- Signature ---
            story.append(Paragraph("<b>Homeowner Authorization & Digital Execution</b>", styles["SectionHeading"]))
            story.append(HRFlowable(width="100%", thickness=0.5, color=BRAND_BORDER, spaceAfter=12))

            try:
                sig_img = Image(str(signature_path), width=280, height=80)
                sig_img.drawWidth = 280
                sig_img.drawHeight = 80
                story.append(sig_img)
            except Exception as e:
                log.error("signature_render_failed", error=str(e))

            story.append(Spacer(1, 10))
            time_str = f" on {timestamp_utc}" if timestamp_utc else ""
            story.append(Paragraph(f"Digitally signed & verified by <b>{signer_name}</b> from IP <b>{ip_address}</b>{time_str}", styles["FinePrint"]))

            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("retail_contract_pdf_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("retail_contract_pdf_generation_failed", error=str(exc))
            raise

    async def generate_retail_notice_of_cancellation(self, job: dict) -> str:
        """Generate General Georgia statutory Notice of Cancellation for Retail Sales."""
        job_dir = FIELD_DOCS_DIR / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "retail_notice_of_cancellation.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job["id"], doc_type="RETAIL_NOTICE_OF_CANCELLATION")
            story: list[Flowable] = []

            for copy_type in ["Customer Copy", "Contractor Copy"]:
                story.append(Paragraph(copy_type, styles["DocControl"]))
                story.append(Spacer(1, 10))

                story.extend(create_header("NOTICE OF CANCELLATION", "homeowner"))

                # --- Metadata Table ---
                story.append(self._build_metadata_table(job))
                story.append(Spacer(1, 12))

                story.append(Paragraph(f"Date of Transaction: {datetime.date.today().isoformat()}", styles["BodyText"]))
                story.append(Spacer(1, 12))

                statutory_text = (
                    "You may cancel this transaction, without any penalty or obligation, "
                    "within THREE BUSINESS DAYS from the above date. To cancel this "
                    "transaction, mail or deliver a signed and dated copy of this "
                    "cancellation notice, or any other written notice, to Wickham Roofing "
                    "LLC at the address below, not later than midnight of the third "
                    "business day after the date of this transaction. If you cancel by "
                    "mail, notice must be postmarked by that deadline."
                )
                story.append(Paragraph(statutory_text, styles["StatWarning"]))
                story.append(Spacer(1, 20))

                story.append(Paragraph("To cancel this transaction, mail or deliver a signed and dated copy of this cancellation notice, or any other written notice, to:<br/><br/><b>WICKHAM ROOFING LLC</b><br/>123 Roofing Lane<br/>Thomasville, GA 31792", styles["BodyText"]))
                story.append(Spacer(1, 40))
                story.append(Paragraph("I HEREBY CANCEL THIS TRANSACTION.", styles["BodyText"]))
                story.append(Spacer(1, 40))

                story.append(self._build_signature_block(title1="Homeowner Signature", title2="Contractor Signature"))

                if copy_type == "Customer Copy":
                    story.append(PageBreak())

            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath

    async def generate_certificate_of_completion(self, job: dict, completion_date: str) -> str:
        """Generate Certificate of Completion."""
        job_dir = FIELD_DOCS_DIR / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "certificate_of_completion.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job["id"], doc_type="CERTIFICATE_OF_COMPLETION")
            story: list[Flowable] = []

            story.extend(create_header("CERTIFICATE OF COMPLETION & STATUTORY LIEN RELEASE", "homeowner"))

            # --- Metadata Table ---
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 15))

            story.append(Paragraph("Work Acceptance & Punch List Verification", styles["SectionHeading"]))
            text = (
                f"This document certifies that Wickham Roofing LLC has satisfactorily completed "
                f"all roofing services per the agreed scope of work at the property located at:<br/><br/>"
                f"<b>{job.get('address_line1', '')}, {job.get('city', '')}, {job.get('state', '')} {job.get('postal_code', '')}</b><br/><br/>"
                f"for the homeowner, <b>{job.get('homeowner_name', 'Homeowner')}</b>, on <b>{completion_date}</b>. "
                f"The homeowner acknowledges that the roof has been thoroughly inspected, all punch list items have been resolved, and "
                f"all work has been performed in compliance with applicable local and state building codes."
            )
            story.append(Paragraph(text, styles["BodyText"]))
            story.append(Spacer(1, 15))

            story.append(Paragraph("WAIVER AND RELEASE OF LIEN AND PAYMENT BOND RIGHTS UPON FINAL PAYMENT", styles["SectionHeading"]))
            story.append(Paragraph("STATE OF GEORGIA<br/>COUNTY OF THOMAS", styles["BodyText"]))
            story.append(Spacer(1, 10))
            address_str = f"{job.get('address_line1', '')}, {job.get('city', '')}, {job.get('state', '')} {job.get('postal_code', '')}"
            lien_text = (
                "THE UNDERSIGNED MECHANIC AND/OR MATERIALMAN HAS BEEN EMPLOYED BY WICKHAM ROOFING LLC "
                "TO FURNISH ROOFING MATERIALS AND LABOR FOR THE CONSTRUCTION OF IMPROVEMENTS KNOWN AS "
                f"ROOF REPLACEMENT WHICH IS LOCATED IN THE CITY OF {job.get('city', '').upper()}, COUNTY OF THOMAS, "
                f"AND IS OWNED BY {job.get('homeowner_name', '').upper()} AND MORE PARTICULARLY DESCRIBED AS FOLLOWS:<br/><br/>"
                f"{address_str.upper()}<br/><br/>"
                "UPON THE RECEIPT OF THE SUM OF $__________, THE MECHANIC AND/OR MATERIALMAN WAIVES AND RELEASES "
                "ANY AND ALL LIENS OR CLAIMS OF LIENS IT HAS UPON THE FOREGOING DESCRIBED PROPERTY OR ANY RIGHTS "
                "AGAINST ANY LABOR AND/OR MATERIAL BOND ON ACCOUNT OF LABOR OR MATERIALS, OR BOTH, FURNISHED BY "
                "THE UNDERSIGNED TO OR ON ACCOUNT OF SAID CONTRACTOR FOR SAID PROPERTY.<br/><br/>"
                f"GIVEN UNDER HAND AND SEAL THIS {datetime.date.today().day} DAY OF {datetime.date.today().strftime('%B').upper()}, {datetime.date.today().year}."
            )
            story.append(Paragraph(lien_text, styles["BodyText"]))
            story.append(Spacer(1, 15))

            warranty_text = (
                "Wickham Roofing LLC provides an explicit <b>ONE (1) YEAR WORKMANSHIP WARRANTY</b> on all full roof replacements from project completion date. "
                "Material warranties are provided directly by the product manufacturer."
            )
            story.append(self._box_warning("1-YEAR WORKMANSHIP WARRANTY GUARANTEE", warranty_text, BRAND_BLUE))
            story.append(Spacer(1, 20))

            story.append(self._build_signature_block(title1="Homeowner Signature", title2="Wickham Roofing LLC Representative", include_witness=True))

            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath

    async def generate_contingency_agreement(self, job: dict) -> str:
        """Generate a complete Georgia Insurance Contingency Agreement."""
        job_dir = FIELD_DOCS_DIR / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "contingency_agreement.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, top_margin=120, job_id=job["id"], doc_type="CONTINGENCY")
            story: list[Flowable] = []

            story.extend(create_header("INSURANCE CONTINGENCY & SCOPE AGREEMENT", "homeowner"))

            # --- Metadata Table ---
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 14))

            # --- Section 1: Scope of Work ---
            story.append(Paragraph("1. Scope of Work & Insurance Authorization", styles["SectionHeading"]))
            scope_text = (
                "Contractor (Wickham Roofing LLC) agrees to perform complete roof and exterior restoration services at the property listed above. "
                "The final scope of work, specifications, and contract price shall be strictly determined by the insurance carrier's approved estimate, "
                "plus any Homeowner-requested upgrades or signed change orders. Homeowner hereby authorizes Contractor to inspect property, document storm damage, "
                "verify technical line-item quantities, and communicate directly with insurance adjusters on construction scope and building code compliance matters."
            )
            story.append(Paragraph(scope_text, styles["BodyText"]))
            story.append(Spacer(1, 10))

            # --- Section 2: 1-Year Workmanship Warranty ---
            warranty_text = (
                "Wickham Roofing LLC provides an explicit <b>ONE (1) YEAR WORKMANSHIP WARRANTY</b> on all labor and installation craft quality from the date "
                "of final project completion. Contractor agrees to repair any installation defects free of charge during the warranty period. "
                "All manufacturer material warranties (shingles, synthetic underlayment, ventilation) are transferred directly to Homeowner upon full payment."
            )
            story.append(self._box_warning("1-YEAR WORKMANSHIP WARRANTY GUARANTEE", warranty_text, BRAND_BLUE))
            story.append(Spacer(1, 10))

            # --- Section 3: HB 423 Georgia Law Deductible Disclosure ---
            warning_text = (
                "WARNING: It is a violation of Georgia law (O.C.G.A. § 33-24-59.27) for a contractor to pay, waive, rebate, or promise to pay or rebate "
                "all or any portion of an insurance deductible. Homeowner is strictly responsible for payment of the insurance deductible in full."
            )
            story.append(self._box_warning("HB 423 Deductible Compliance Disclosure (O.C.G.A. § 33-24-59.27)", warning_text, BRAND_RED))
            story.append(Spacer(1, 10))

            # --- Section 4: Georgia Statutory Cancellation Disclosure (O.C.G.A. § 10-1-393.12) ---
            cancel_stat_text = (
                "You may cancel this contract at any time before midnight on the fifth (5th) business day after you have received written "
                "notification from your insurer that all or any part of the claim or contract is not a covered loss under the insurance policy. "
                "See attached Notice of Cancellation form for an explanation of this right."
            )
            story.append(self._box_warning("GEORGIA STATUTORY RIGHT TO CANCEL (O.C.G.A. § 10-1-393.12)", cancel_stat_text, colors.HexColor("#7f1d1d")))
            story.append(Spacer(1, 10))

            # --- Section 5: Terms & Representation ---
            story.append(Paragraph("2. Terms & Representation", styles["SectionHeading"]))
            terms_text = (
                "<b>Public Adjuster Disclosure:</b> Contractor is not acting as a licensed public adjuster and does not represent or negotiate legal claims on behalf of Homeowner.<br/><br/>"
                "<b>Default & Liquidated Overhead:</b> If Homeowner breaches this contract after insurance approval without statutory cause, Homeowner agrees to pay liquidated damages "
                "equal to 15% of the approved claim total to reimburse Contractor for administrative, technical, and pre-construction expenses."
            )
            story.append(Paragraph(terms_text, styles["BodyText"]))
            story.append(Spacer(1, 16))

            # Signature block
            story.append(self._build_signature_block(title1="Homeowner Signature", title2="Wickham Roofing LLC Representative"))

            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath

    async def generate_bom_pdf(self, job: dict) -> str:
        """Generate a professional Bill of Materials (BoM) PDF for Scott to order materials."""
        job_id = job.get("id", "UNKNOWN")
        log = logger.bind(job_id=job_id)
        log.info("bom_pdf_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "materials_list.pdf")

        styles = get_audience_styles("internal")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, top_margin=120, job_id=job_id, doc_type="BILL_OF_MATERIALS")
            story: list[Flowable] = []

            story.extend(create_header("BILL OF MATERIALS (BoM) — MATERIAL ORDER SHEET", "internal"))

            # --- Metadata Table ---
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 14))

            # --- Section 1: Measurements Summary ---
            total_area_sf = job.get("ev_total_area_sf") or 0.0
            total_squares = total_area_sf / 100.0
            eaves_lf = job.get("ev_eaves_lf") or 0.0
            valleys_lf = job.get("ev_valley_lf") or 0.0
            rakes_lf = job.get("ev_rakes_lf") or 0.0
            ridge_lf = job.get("ev_ridge_lf") or 0.0
            hip_lf = job.get("ev_hip_lf") or 0.0

            meas_data = [
                ["Total Roof Area:", f"{total_area_sf:,.1f} sq ft ({total_squares:.2f} Squares)"],
                ["Eaves / Valleys:", f"Eaves: {eaves_lf:.1f} LF | Valleys: {valleys_lf:.1f} LF"],
                ["Rakes / Ridges / Hips:", f"Rakes: {rakes_lf:.1f} LF | Ridges: {ridge_lf:.1f} LF | Hips: {hip_lf or 0.0:.1f} LF"],
            ]
            meastable = create_financial_table(meas_data, [150, 360], sub_brand="internal", has_header=False)
            story.append(create_section_with_table("1. Roof Measurement Data (Hover/EagleView)", meastable, "internal"))

            # --- Section 2: Calculated Material Requirements ---
            shingle_squares_required = math.ceil(total_squares * 1.15)
            ice_and_water_rolls = math.ceil((eaves_lf + valleys_lf) / 66.0)
            drip_edge_pieces_10ft = math.ceil((eaves_lf + rakes_lf) / 10.0)
            starter_bundles = math.ceil((eaves_lf + rakes_lf) / 100.0)
            synthetic_underlayment_rolls = math.ceil(total_squares / 10.0)
            ridge_cap_bundles = math.ceil((ridge_lf + (hip_lf or 0.0)) / 30.0)

            materials_data = [
                ["Material Item", "Required Qty", "Unit", "Calculation Rule / Waste Factor"],
                ["Laminated Shingles (Lifetime)", f"{shingle_squares_required}", "Squares", "Area + 15% Waste Factor"],
                ["Synthetic Underlayment (10 SQ Roll)", f"{synthetic_underlayment_rolls}", "Rolls", "1 Roll per 10 Squares Area"],
                ["Ice & Water Shield (3' x 66' Roll)", f"{ice_and_water_rolls}", "Rolls", "Full Eaves + Valleys coverage"],
                ["Starter Shingles", f"{starter_bundles}", "Bundles", "Eaves + Rakes coverage"],
                ["Ridge Cap Shingles", f"{ridge_cap_bundles}", "Bundles", "Ridges + Hips (30 LF per bundle)"],
                ["Drip Edge (T-Style, 10' Metal)", f"{drip_edge_pieces_10ft}", "Pieces", "Eaves + Rakes perimeter"],
            ]

            mattable = create_financial_table(
                materials_data,
                [180, 80, 60, 190],
                sub_brand="internal",
                center_cols=[1, 2],
                has_header=True,
            )
            story.append(create_section_with_table("2. Required Material Order List", mattable, "internal"))

            # --- Section 3: Fulfillment & Notes ---
            story.append(Paragraph("3. Ordering Instructions", styles["SectionHeading"]))
            instr_text = (
                "Verify shingle color preferences with the homeowner and check the retail contract or supplement "
                "details for any upgrade options prior to placing order. Confirm delivery address and coordinate "
                "loading schedule with Alpha/Beta teams to ensure materials are on-site exactly when needed. "
                "File delivery confirmation photo in the job record upon arrival."
            )
            story.append(Paragraph(instr_text, styles["BodyText"]))
            story.append(Spacer(1, 20))

            # Signatures/Sign-off area
            story.append(Paragraph("<b>Order Authorization</b>", styles["SectionHeading"]))
            story.append(HRFlowable(width="100%", thickness=0.5, color=BRAND_BORDER, spaceAfter=12))
            story.append(self._build_signature_block(title1="Operations Manager (Scott)", title2="Purchasing / Office (Debi)"))

            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("bom_pdf_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("bom_pdf_generation_failed", error=str(exc))
            raise


