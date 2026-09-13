"""
app/services/pdf/evidence_packet.py — Supplement Evidence Packet PDF Generator.
Produces a professional, versioned evidence packet documenting field exhibits,
tied to deterministic claim discrepancies for insurance supplement review.
"""

from __future__ import annotations

import datetime
import html
import os
import uuid
from pathlib import Path
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from app.config import FIELD_DOCS_DIR
from app.core.database import get_db_connection, insert_job_document
from app.services.evidence_matrix import EVIDENCE_CATEGORIES, get_job_evidence_exhibits
from app.services.pdf.constants import (
    BRAND_ACCENT,
    BRAND_BLUE,
    BRAND_BORDER,
    BRAND_LIGHT_BG,
    BRAND_MUTED_BG,
    BRAND_NAVY,
    BRAND_SLATE,
    COMPANY_ADDRESS,
    COMPANY_EMAIL,
    COMPANY_NAME,
    COMPANY_PHONE,
    COMPANY_TAGLINE,
)
from app.services.pdf.engine import PDFEngine, register_brand_fonts

logger = structlog.get_logger("app.services.pdf.evidence_packet")


class EvidencePacketGenerator(PDFEngine):
    """
    Builds the Supplement Evidence Packet v1 PDF.
    """

    def generate_packet_sync(
        self,
        job_id: str,
        version: int = 1,
        included_only: bool = False,
        db_path: str = "data/wickham_crm.db",
    ) -> str:
        """
        Synchronous generator for the evidence packet PDF.
        """
        register_brand_fonts()

        out_dir = Path(FIELD_DOCS_DIR) / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"evidence_packet_v{version}.pdf"
        out_path = out_dir / filename

        # 1. Fetch Job Metadata
        job_info: dict[str, Any] = {}
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, homeowner_name, address_line1, city, state, postal_code,
                       claim_number, insurer_name, policy_type, status,
                       inspector_name, canvasser_name, created_at
                FROM jobs WHERE id = ?
                """,
                (job_id,),
            )
            row = cursor.fetchone()
            if row:
                job_info = {
                    "id": row[0],
                    "homeowner_name": row[1] or "Homeowner",
                    "address": f"{row[2] or ''}, {row[3] or ''}, {row[4] or ''} {row[5] or ''}".strip(" ,"),
                    "claim_number": row[6] or "Pending / Unassigned",
                    "carrier": row[7] or "Carrier File Pending",
                    "policy_number": row[8] or "On File",
                    "status": row[9] or "UNKNOWN",
                    "rep": row[10] or row[11] or "Wickham Technical Team",
                    "created_at": row[12],
                }
            else:
                job_info = {
                    "id": job_id,
                    "homeowner_name": "Homeowner",
                    "address": "Address on file",
                    "claim_number": "Pending",
                    "carrier": "Carrier on file",
                    "policy_number": "On File",
                    "status": "IN_REVIEW",
                    "rep": "Wickham Technical Team",
                    "created_at": None,
                }

        # 2. Fetch Evidence Exhibits
        exhibits = get_job_evidence_exhibits(
            job_id,
            included_only=included_only,
            db_path=db_path,
        )

        # 3. Fetch Discrepancies if available
        discrepancies: list[dict[str, Any]] = []
        with get_db_connection(db_path) as conn:
            c = conn.cursor()
            try:
                c.execute(
                    """
                    SELECT line_item_code, description, carrier_qty, required_qty,
                           discrepancy_reason, code_reference
                    FROM supplement_discrepancies
                    WHERE job_id = ?
                    LIMIT 20
                    """,
                    (job_id,),
                )
                for r in c.fetchall():
                    discrepancies.append({
                        "code": r[0],
                        "desc": r[1],
                        "carrier_qty": r[2],
                        "required_qty": r[3],
                        "reason": r[4],
                        "code_ref": r[5],
                    })
            except Exception:
                # Table may not have records or exist
                pass

        # 4. Build Story
        doc = SimpleDocTemplate(
            str(out_path),
            pagesize=letter,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        normal = styles["Normal"]

        section_style = ParagraphStyle(
            "SectionHeader",
            parent=normal,
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=17,
            textColor=BRAND_BLUE,
            spaceAfter=6,
        )
        body_style = ParagraphStyle(
            "BodyText",
            parent=normal,
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=BRAND_NAVY,
        )
        disclaimer_style = ParagraphStyle(
            "Disclaimer",
            parent=normal,
            fontName="Helvetica-Oblique",
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#64748b"),
        )

        story: list[Any] = []
        now_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

        # Header Block
        header_data = [
            [
                Paragraph(f"<b>{COMPANY_NAME}</b><br/>{COMPANY_TAGLINE}", body_style),
                Paragraph(
                    f"<b>SUPPLEMENT EVIDENCE PACKET</b><br/>"
                    f"Version {version}.0 &bull; Generated {now_str}",
                    ParagraphStyle("HR", parent=body_style, alignment=2),
                ),
            ]
        ]
        t_head = Table(header_data, colWidths=[3.5 * inch, 4.0 * inch])
        t_head.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t_head)
        story.append(HRFlowable(width="100%", thickness=2, color=BRAND_BLUE, spaceAfter=12))

        # Job / Claim Identifiers Box
        info_data = [
            [
                Paragraph(f"<b>Property Owner:</b> {html.escape(job_info['homeowner_name'])}", body_style),
                Paragraph(f"<b>Insurance Carrier:</b> {html.escape(job_info['carrier'])}", body_style),
            ],
            [
                Paragraph(f"<b>Property Address:</b> {html.escape(job_info['address'])}", body_style),
                Paragraph(f"<b>Claim Number:</b> {html.escape(job_info['claim_number'])}", body_style),
            ],
            [
                Paragraph(f"<b>Job Identifier:</b> {html.escape(job_info['id'])}", body_style),
                Paragraph(f"<b>Policy Number:</b> {html.escape(job_info['policy_number'])}", body_style),
            ],
            [
                Paragraph(f"<b>Prepared By:</b> {html.escape(job_info['rep'])}", body_style),
                Paragraph(f"<b>Audit Status:</b> {html.escape(job_info['status'])}", body_style),
            ],
        ]
        t_info = Table(info_data, colWidths=[3.75 * inch, 3.75 * inch])
        t_info.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT_BG),
            ("BOX", (0, 0), (-1, -1), 1, BRAND_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(t_info)
        story.append(Spacer(1, 10))

        # Compliance Notice / Review Disclaimer
        disclaimer_text = (
            "<b>PREPARED FOR CARRIER REVIEW &amp; RECONCILIATION.</b><br/>"
            "This evidence matrix documents physical site conditions and forensic observations verified by "
            "Wickham Roofing LLC. This document does not constitute a coverage guarantee, claim approval, or "
            "legal representation. All quantities, line items, and building-code citations are derived from "
            "deterministic calculation rules and verified site inspection data."
        )
        t_disc = Table([[Paragraph(disclaimer_text, disclaimer_style)]], colWidths=[7.5 * inch])
        t_disc.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_MUTED_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(t_disc)
        story.append(Spacer(1, 14))

        # Evidence Index Table
        story.append(Paragraph("<b>1. Evidence Exhibits Index</b>", section_style))
        index_rows: list[list[Any]] = [
            [
                Paragraph("<b>Ex #</b>", body_style),
                Paragraph("<b>Category</b>", body_style),
                Paragraph("<b>Location / Component</b>", body_style),
                Paragraph("<b>Observation Summary</b>", body_style),
                Paragraph("<b>Status</b>", body_style),
            ]
        ]
        if exhibits:
            for ex in exhibits:
                obs_snip = ex["observation"][:65] + ("..." if len(ex["observation"]) > 65 else "")
                loc = ex.get("location_roof_area") or "General Roof Field"
                index_rows.append([
                    Paragraph(f"<b>Ex-{ex['exhibit_number']:02d}</b>", body_style),
                    Paragraph(html.escape(ex["category_display"]), body_style),
                    Paragraph(html.escape(loc), body_style),
                    Paragraph(html.escape(obs_snip), body_style),
                    Paragraph(html.escape(ex.get("status", "draft").upper()), body_style),
                ])
        else:
            index_rows.append([
                Paragraph("—", body_style),
                Paragraph("No exhibits recorded yet", body_style),
                Paragraph("—", body_style),
                Paragraph("No field evidence items attached to this job.", body_style),
                Paragraph("—", body_style),
            ])

        t_idx = Table(index_rows, colWidths=[0.8 * inch, 1.8 * inch, 1.6 * inch, 2.5 * inch, 0.8 * inch])
        t_idx.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
        ]))
        story.append(t_idx)
        story.append(Spacer(1, 14))

        # Deterministic Discrepancies Reconciliation Table (if present)
        if discrepancies:
            story.append(Paragraph("<b>2. Deterministic Claim Discrepancies</b>", section_style))
            disc_rows: list[list[Any]] = [
                [
                    Paragraph("<b>Code</b>", body_style),
                    Paragraph("<b>Description</b>", body_style),
                    Paragraph("<b>Carrier Qty</b>", body_style),
                    Paragraph("<b>Required Qty</b>", body_style),
                    Paragraph("<b>Code / Variance Reason</b>", body_style),
                ]
            ]
            for d in discrepancies:
                disc_rows.append([
                    Paragraph(f"<b>{html.escape(str(d['code']))}</b>", body_style),
                    Paragraph(html.escape(str(d["desc"])), body_style),
                    Paragraph(str(d.get("carrier_qty", "0")), body_style),
                    Paragraph(f"<b>{d.get('required_qty', '0')}</b>", body_style),
                    Paragraph(f"{html.escape(str(d.get('reason', '')))} {html.escape(str(d.get('code_ref', '')))}", body_style),
                ])
            t_disc_tbl = Table(disc_rows, colWidths=[1.1 * inch, 2.2 * inch, 0.9 * inch, 1.0 * inch, 2.3 * inch])
            t_disc_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_SLATE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
            ]))
            story.append(t_disc_tbl)
            story.append(Spacer(1, 14))

        # Exhibits Detail Section
        if exhibits:
            story.append(PageBreak())
            story.append(Paragraph("<b>3. Detailed Forensic Exhibits</b>", section_style))
            story.append(Spacer(1, 6))

            for ex in exhibits:
                ex_num = ex["exhibit_number"]
                ex_cat = ex["category_display"]
                loc = ex.get("location_roof_area") or "Roof Field / Perimeter"
                claim_ref = ex.get("proposed_claim_line_ref") or "Scope Reconciliation"
                obs = ex.get("observation") or "Observed physical condition requiring correction."
                photo_path = ex.get("photo_path")

                # Left side: Image or descriptive placeholder
                image_flowable: Any = None
                if photo_path and os.path.exists(photo_path):
                    try:
                        image_flowable = Image(photo_path, width=3.2 * inch, height=2.4 * inch)
                    except Exception as img_err:
                        logger.warning("failed_to_load_exhibit_photo", path=photo_path, error=str(img_err))

                if not image_flowable:
                    placeholder_tbl = Table(
                        [[Paragraph(f"<font color='#64748b'>[Forensic Photo Record]<br/>{html.escape(ex_cat)}<br/>{html.escape(loc)}</font>", body_style)]],
                        colWidths=[3.2 * inch],
                        rowHeights=[2.4 * inch],
                    )
                    placeholder_tbl.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT_BG),
                        ("BOX", (0, 0), (-1, -1), 1, BRAND_BORDER),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]))
                    image_flowable = placeholder_tbl

                # Right side: Metadata and observation narrative
                detail_text = (
                    f"<b>Exhibit {ex_num:02d}:</b> {html.escape(ex_cat)}<br/>"
                    f"<b>Location:</b> {html.escape(loc)}<br/>"
                    f"<b>Line Item Reference:</b> {html.escape(claim_ref)}<br/>"
                    f"<b>Review Status:</b> {html.escape(ex.get('status', 'draft').upper())}<br/>"
                    f"<b>Date Logged:</b> {html.escape(str(ex.get('created_at', ''))[:10])}<br/>"
                    f"<br/>"
                    f"<b>Field Observation:</b><br/>"
                    f"{html.escape(obs)}<br/>"
                    f"<br/>"
                    f"<font size=7 color='#64748b'>Source Provenance: Verified Field Capture &bull; Exhibits Ledger #{ex['id']}</font>"
                )
                right_para = Paragraph(detail_text, body_style)

                ex_table = Table([[image_flowable, right_para]], colWidths=[3.3 * inch, 4.2 * inch])
                ex_table.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOX", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ]))

                story.append(KeepTogether([ex_table, Spacer(1, 10)]))

        # Build Document
        doc.build(story)

        # Register in job_documents
        with get_db_connection(db_path) as conn:
            doc_id = f"doc_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO job_documents (id, job_id, filename, file_type, storage_path, visibility, category, created_at)
                VALUES (?, ?, ?, 'EVIDENCE_PACKET', ?, 'office_only', 'SUPPLEMENT', CURRENT_TIMESTAMP)
                """,
                (doc_id, job_id, filename, str(out_path)),
            )
            conn.commit()

        return str(out_path)


evidence_packet_generator = EvidencePacketGenerator()
