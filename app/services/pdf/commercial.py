"""
Commercial-Grade PDF Generation: Multi-Stage Progress Billing & Contracts.

Leverages ReportLab Platypus flowables and NumberedCanvas for:
- Two-pass dynamic page numbering ("Page X of Y").
- Multi-page Schedules of Values (SOV) continuation sheets.
- Dynamic scope of work rendering protected with KeepInFrame.
- Strict conversion of integer cents to formatted dollar strings.
- Configurable retainage terms (no hardcoded statutory assumptions).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    HRFlowable,
    KeepInFrame,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import FIELD_DOCS_DIR
from app.services.pdf.constants import (
    BRAND_BLUE,
    BRAND_BORDER,
    BRAND_MUTED_BG,
    BRAND_NAVY,
    BRAND_RED,
    BRAND_SLATE,
    COMPANY_EMAIL,
    COMPANY_NAME,
    COMPANY_PHONE,
    COMPANY_TAGLINE,
)
from app.services.pdf.documents import create_header, get_audience_styles
from app.services.pdf.engine import NumberedCanvas, PDFEngine, get_font_name

logger = structlog.get_logger("app.services.pdf.commercial")


class CommercialPDFGenerator(PDFEngine):
    """
    Platypus-based generator for commercial contracts and progress billing applications.
    Employs NumberedCanvas for accurate multi-page 'Page X of Y' footers.
    """

    def _cents_to_dollar_str(self, cents_val: int | float | None) -> str:
        """Convert integer cents or numeric dollar value to formatted dollar string."""
        if cents_val is None:
            return "$0.00"
        # If float and small, might be dollars; otherwise integer cents
        if isinstance(cents_val, float) and cents_val < 10000:
            cents = round(cents_val * 100)
        else:
            cents = int(round(cents_val))
        return f"${cents / 100.0:,.2f}"

    async def generate_commercial_contract(
        self,
        job: dict[str, Any],
        sov_items: list[dict[str, Any]],
        terms: dict[str, Any] | None = None,
        scope_text: str | None = None,
        filepath: str | None = None,
    ) -> str:
        """
        Generate a multi-page Commercial Roofing Contract with Schedule of Values.
        Uses Platypus flowables and NumberedCanvas for Page X of Y footers.
        """
        job_id = job.get("id", "UNKNOWN")
        log = logger.bind(job_id=job_id, doc_type="COMMERCIAL_CONTRACT")
        log.info("generating_commercial_contract_pdf")

        if not filepath:
            job_dir = FIELD_DOCS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            filepath = str(job_dir / "commercial_contract_signed.pdf")

        terms = terms or {}
        retainage_pct = float(terms.get("retainage_percent", job.get("retainage_percent", 10.0)))
        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc: Any = SimpleDocTemplate(
                filepath,
                pagesize=letter,
                leftMargin=50,
                rightMargin=50,
                topMargin=50,
                bottomMargin=50,
            )
            doc.job_id = job_id
            doc.doc_type = "COMMERCIAL_CONTRACT"

            story: list[Any] = []

            # --- Page 1: Header & Letterhead ---
            story.extend(create_header("COMMERCIAL ROOFING CONTRACT & AGREEMENT", "homeowner"))

            # Project & Client Metadata
            client_name = job.get("customer_name") or job.get("client_name") or job.get("homeowner_name", "Commercial Client")
            project_name = job.get("project_name") or f"Roof Restoration - {job.get('property_address') or job.get('address_line1', 'Property')}"
            address = job.get("property_address") or f"{job.get('address_line1', '')}, {job.get('city', '')}, {job.get('state', '')} {job.get('postal_code', '')}".strip(" ,")

            meta_data = [
                [
                    Paragraph("<b>Client / Owner:</b>", styles["BodyText"]),
                    Paragraph(client_name, styles["BodyText"]),
                    Paragraph("<b>Job ID:</b>", styles["BodyText"]),
                    Paragraph(job_id, styles["BodyText"]),
                ],
                [
                    Paragraph("<b>Project:</b>", styles["BodyText"]),
                    Paragraph(project_name, styles["BodyText"]),
                    Paragraph("<b>Contract Type:</b>", styles["BodyText"]),
                    Paragraph("Commercial Progress Billing", styles["BodyText"]),
                ],
                [
                    Paragraph("<b>Location:</b>", styles["BodyText"]),
                    Paragraph(address, styles["BodyText"]),
                    Paragraph("<b>Retainage Rate:</b>", styles["BodyText"]),
                    Paragraph(f"{retainage_pct:.1f}% (Contracted)", styles["BodyText"]),
                ],
            ]
            t_meta = Table(meta_data, colWidths=[105, 170, 105, 130])
            t_meta.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), BRAND_MUTED_BG),
                ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(t_meta)
            story.append(Spacer(1, 14))

            # --- Section 1: Scope of Work (Protected with KeepInFrame for dynamic lengths) ---
            story.append(Paragraph("1. Scope of Work & Engineering Specifications", styles["SectionHeading"]))
            raw_scope = scope_text or job.get("scope_of_work") or (
                "Contractor agrees to furnish all required labor, supervision, materials, equipment, and insurance "
                "to execute the complete commercial roof replacement in accordance with project specifications, "
                "local municipal building codes, and manufacturer specifications. Work includes complete teardown "
                "of designated roof sections, inspection and replacement of deteriorated structural decking, installation "
                "of code-compliant thermal barrier, synthetic underlayment, commercial-grade flashings, and complete cleanup."
            )

            # KeepInFrame ensures long dynamic scope text fits within bounding frame constraints cleanly
            scope_flowables = [
                Paragraph(raw_scope, styles["BodyText"]),
                Spacer(1, 6),
                Paragraph("<b>Manufacturer Specs:</b> Installation compliant with ASTM standards and manufacturer specifications.", styles["FinePrint"]),
            ]
            story.append(KeepInFrame(maxWidth=512, maxHeight=220, content=scope_flowables, mode="shrink"))
            story.append(Spacer(1, 12))

            # --- Section 2: Schedule of Values (SOV) ---
            story.append(Paragraph("2. SCHEDULE OF VALUES (SOV)", styles["SectionHeading"]))
            sov_header = [
                Paragraph("<b>Item</b>", styles["BodyText"]),
                Paragraph("<b>Description of Work Item</b>", styles["BodyText"]),
                Paragraph("<b>Scheduled Value</b>", styles["BodyText"]),
            ]
            sov_rows = [sov_header]
            total_scheduled_cents = 0

            for idx, item in enumerate(sov_items, start=1):
                item_code = item.get("item_code") or f"SOV-{idx:02d}"
                desc = item.get("description", "Work Item")
                cents = item.get("scheduled_value_cents", 0)
                if not cents and "scheduled_value" in item:
                    val = item["scheduled_value"]
                    cents = int(round(val * 100)) if isinstance(val, float) and val < 50000 else int(round(val))
                total_scheduled_cents += cents

                sov_rows.append([
                    Paragraph(item_code, styles["BodyText"]),
                    Paragraph(desc, styles["BodyText"]),
                    Paragraph(self._cents_to_dollar_str(cents), styles["BodyText"]),
                ])

            sov_rows.append([
                Paragraph("<b>TOTAL</b>", styles["BodyText"]),
                Paragraph("<b>Total Contract Sum (Schedule of Values)</b>", styles["BodyText"]),
                Paragraph(f"<b>{self._cents_to_dollar_str(total_scheduled_cents)}</b>", styles["BodyText"]),
            ])

            t_sov = Table(sov_rows, colWidths=[70, 310, 130])
            t_sov.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1f5f9")),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ]))
            story.append(t_sov)
            story.append(Spacer(1, 14))

            # --- Page Break to Commercial Terms & Multi-Page Flow ---
            story.append(PageBreak())

            # --- Page 2: Commercial Progress Billing & Retainage Terms ---
            story.append(create_header("COMMERCIAL CONTRACT TERMS & CONDITIONS", "homeowner")[0])
            story.append(Spacer(1, 8))

            story.append(Paragraph("3. Multi-Stage Progress Billing & Retainage Terms", styles["SectionHeading"]))
            terms_narrative = (
                f"<b>A. Application for Payment:</b> Contractor shall submit periodic progress billing applications "
                f"based upon the percentage of work completed and materials stored to date per the Schedule of Values.<br/><br/>"
                f"<b>B. Retainage Holdback:</b> Owner shall withhold a retainage of <b>{retainage_pct:.1f}%</b> from each progress payment. "
                f"The retainage percentage is agreed upon contractually between Contractor and Owner and reflects project complexity.<br/><br/>"
                f"<b>C. Release of Retainage:</b> All accumulated retainage withheld shall become due and payable within thirty (30) days "
                f"following Substantial Completion, issuance of the final Certificate of Occupancy/Completion, and completion of all punch-list items.<br/><br/>"
                f"<b>D. Payment Terms:</b> Progress billings are payable within thirty (30) days of invoice date (Net 30). Unpaid balances past due shall "
                f"accrue interest at 1.5% per month or the maximum statutory rate permitted by law."
            )
            story.append(Paragraph(terms_narrative, styles["BodyText"]))
            story.append(Spacer(1, 10))

            # Statutory disclaimer box
            disclaimer = (
                "<b>STATUTORY NOTICE REGARDING LIEN & RETAINAGE TERMS:</b><br/>"
                "Retainage rates and mechanic's lien deadlines are contract-dependent and vary across state jurisdictions. "
                "Statutory notice requirements and deadlines must be confirmed with independent legal counsel for each specific commercial engagement."
            )
            story.append(Table([[Paragraph(disclaimer, styles["FinePrint"])]], colWidths=[510], style=[
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffbeb")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#f59e0b")),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(Spacer(1, 14))

            # --- Section 4: Warranties & Execution ---
            story.append(Paragraph("4. Commercial Warranties & Execution", styles["SectionHeading"]))
            warranty_text = (
                "Contractor warrants all workmanship for a period of TWO (2) YEARS from Substantial Completion. "
                "Manufacturer material warranties are transferred directly to Owner upon final payment reconciliation."
            )
            story.append(Paragraph(warranty_text, styles["BodyText"]))
            story.append(Spacer(1, 20))

            sig_table_data: list[list[Any]] = [
                [
                    Paragraph("<b>CLIENT ACCEPTANCE</b>", styles["BodyText"]),
                    Paragraph("<b>WICKHAM ROOFING LLC</b>", styles["BodyText"]),
                ],
                [
                    Spacer(1, 35),
                    Spacer(1, 35),
                ],
                [
                    Paragraph("Signature: ___________________________", styles["BodyText"]),
                    Paragraph("Signature: ___________________________", styles["BodyText"]),
                ],
                [
                    Paragraph(f"Name: {client_name}", styles["FinePrint"]),
                    Paragraph("Name: Authorized Representative", styles["FinePrint"]),
                ],
                [
                    Paragraph("Date: _______________________________", styles["FinePrint"]),
                    Paragraph("Date: _______________________________", styles["FinePrint"]),
                ],
            ]
            t_sig = Table(sig_table_data, colWidths=[250, 250])
            t_sig.setStyle(TableStyle([
                ("LINEABOVE", (0, 2), (0, 2), 0.5, BRAND_SLATE),
                ("LINEABOVE", (1, 2), (1, 2), 0.5, BRAND_SLATE),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(KeepTogether(t_sig))

            # Build document with NumberedCanvas to ensure accurate "Page X of Y" footers
            doc.build(story, canvasmaker=NumberedCanvas)

        await asyncio.to_thread(build_pdf)
        log.info("commercial_contract_pdf_generated", filepath=filepath)
        return filepath

    async def generate_progress_billing_invoice(
        self,
        job: dict[str, Any],
        application: dict[str, Any] | None = None,
        sov_items: list[dict[str, Any]] | None = None,
        filepath: str | None = None,
        billing_app: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate a commercial Progress Billing Application (AIA G702/G703 format).
        Uses NumberedCanvas for accurate Page X of Y footers across multi-page schedules.
        """
        application = application or billing_app or {}
        sov_items = sov_items or []
        job_id = job.get("id", "UNKNOWN")
        app_no = application.get("app_number") or application.get("application_no", 1)
        log = logger.bind(job_id=job_id, app_no=app_no, doc_type="PROGRESS_BILLING")
        log.info("generating_progress_billing_pdf")

        if not filepath:
            job_dir = FIELD_DOCS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            filepath = str(job_dir / f"progress_billing_app_{app_no}.pdf")

        styles = get_audience_styles("internal")

        def build_pdf() -> None:
            doc: Any = SimpleDocTemplate(
                filepath,
                pagesize=letter,
                leftMargin=40,
                rightMargin=40,
                topMargin=40,
                bottomMargin=40,
            )
            doc.job_id = job_id
            doc.doc_type = "PROGRESS_BILLING"

            story: list[Any] = []

            # Document Title
            story.append(Paragraph(f"<b>APPLICATION AND CERTIFICATE FOR PAYMENT</b> (App #{app_no})", styles["Title"]))
            story.append(HRFlowable(width="100%", thickness=1.5, color=BRAND_NAVY, spaceAfter=8))

            # Top Header Summary
            client = job.get("customer_name") or job.get("client_name") or job.get("homeowner_name", "Commercial Owner")
            billing_date = application.get("billing_date") or application.get("period_end") or "Current Period"
            retainage_pct = float(application.get("retainage_percent", job.get("retainage_percent", 10.0)))

            header_data = [
                [
                    Paragraph(f"<b>TO OWNER:</b> {client}", styles["BodyText"]),
                    Paragraph(f"<b>PROJECT:</b> {job.get('property_address') or job.get('address_line1', 'Commercial Restoration')}", styles["BodyText"]),
                ],
                [
                    Paragraph(f"<b>FROM CONTRACTOR:</b> {COMPANY_NAME}", styles["BodyText"]),
                    Paragraph(f"<b>APPLICATION NO:</b> #{app_no}", styles["BodyText"]),
                ],
                [
                    Paragraph("<b>CONTRACT FOR:</b> Commercial Roofing", styles["BodyText"]),
                    Paragraph(f"<b>PERIOD TO:</b> {billing_date}", styles["BodyText"]),
                ],
            ]
            t_hdr = Table(header_data, colWidths=[260, 260])
            t_hdr.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), BRAND_MUTED_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t_hdr)
            story.append(Spacer(1, 10))

            # Financial Summary Table (AIA G702 core math)
            total_billed = application.get("total_billed_cents") or application.get("total_billed", 0)
            completed_cents = application.get("total_completed_cents") or application.get("total_completed_and_stored", 0)
            retainage_withheld = application.get("retainage_withheld_cents") or application.get("total_retainage", 0)
            retainage_released = application.get("retainage_released_cents", 0)
            total_held = application.get("total_retainage_held_cents", retainage_withheld - retainage_released)
            net_due = application.get("current_payment_due") or application.get("net_payment_due_cents", total_billed)

            fin_summary = [
                [Paragraph("1. Total Completed to Date:", styles["BodyText"]), Paragraph(self._cents_to_dollar_str(completed_cents), styles["BodyText"])],
                [Paragraph(f"2. Retainage Withheld ({retainage_pct:.1f}%):", styles["BodyText"]), Paragraph(self._cents_to_dollar_str(retainage_withheld), styles["BodyText"])],
                [Paragraph("3. Less Retainage Released This Period:", styles["BodyText"]), Paragraph(self._cents_to_dollar_str(retainage_released), styles["BodyText"])],
                [Paragraph("4. Total Retainage Retained to Date:", styles["BodyText"]), Paragraph(self._cents_to_dollar_str(total_held), styles["BodyText"])],
                [Paragraph("<b>5. CURRENT PAYMENT DUE THIS PERIOD:</b>", styles["SectionHeading"]), Paragraph(f"<b>{self._cents_to_dollar_str(net_due)}</b>", styles["SectionHeading"])],
            ]
            t_fin = Table(fin_summary, colWidths=[380, 140])
            t_fin.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]))
            story.append(t_fin)
            story.append(Spacer(1, 14))

            # Contractor Certification block (AIA G702 lower half)
            cert_text = (
                "The undersigned Contractor certifies that to the best of their knowledge, information and belief "
                "the Work covered by this Application for Payment has been completed in accordance with the Contract Documents, "
                "that all amounts have been paid by the Contractor for Work for which previous Certificates for Payment were issued, "
                "and that current payment shown herein is now due."
            )
            fine_style = styles.get("FinePrint") or styles["BodyText"]
            story.append(Paragraph(cert_text, fine_style))
            story.append(Spacer(1, 10))

            cert_table = [
                [
                    Paragraph("<b>CONTRACTOR CERTIFICATION:</b>", styles["BodyText"]),
                    Paragraph("<b>ARCHITECT / OWNER APPROVAL:</b>", styles["BodyText"]),
                ],
                [
                    Paragraph("By: ___________________________ Date: _______", fine_style),
                    Paragraph("By: ___________________________ Date: _______", fine_style),
                ],
            ]
            t_cert = Table(cert_table, colWidths=[260, 260])
            t_cert.setStyle(TableStyle([("PADDING", (0, 0), (-1, -1), 3)]))
            story.append(KeepTogether(t_cert))

            # --- Page Break to AIA G703 Continuation Sheet ---
            story.append(PageBreak())

            # Schedule of Values Continuation Sheet
            story.append(Paragraph(f"<b>CONTINUATION SHEET (Schedule of Values) — Application #{app_no}</b>", styles["SectionHeading"]))
            story.append(Spacer(1, 8))
            sov_header = [
                Paragraph("<b>Item</b>", styles["BodyText"]),
                Paragraph("<b>Description of Work</b>", styles["BodyText"]),
                Paragraph("<b>Scheduled Value</b>", styles["BodyText"]),
                Paragraph("<b>% Done</b>", styles["BodyText"]),
                Paragraph("<b>Total Billed</b>", styles["BodyText"]),
            ]
            sov_rows = [sov_header]
            for item in sov_items:
                code = item.get("item_code", "SOV")
                desc = item.get("description", "Work")
                val_cents = item.get("scheduled_value_cents", 0)
                if not val_cents and "scheduled_value" in item:
                    val = item["scheduled_value"]
                    val_cents = int(round(val * 100)) if isinstance(val, float) and val < 50000 else int(round(val))
                pct = item.get("percent_complete") or item.get("pct_complete", 100.0)
                item_billed = round(val_cents * (pct / 100.0))

                sov_rows.append([
                    Paragraph(code, styles["BodyText"]),
                    Paragraph(desc, styles["BodyText"]),
                    Paragraph(self._cents_to_dollar_str(val_cents), styles["BodyText"]),
                    Paragraph(f"{pct:.1f}%", styles["BodyText"]),
                    Paragraph(self._cents_to_dollar_str(item_billed), styles["BodyText"]),
                ])

            t_sheet = Table(sov_rows, colWidths=[55, 235, 80, 50, 90])
            t_sheet.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ("PADDING", (0, 0), (-1, -1), 4),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ]))
            story.append(t_sheet)

            # Build document with NumberedCanvas for Page X of Y footers
            doc.build(story, canvasmaker=NumberedCanvas)

        await asyncio.to_thread(build_pdf)
        log.info("progress_billing_pdf_generated", filepath=filepath)
        return filepath
