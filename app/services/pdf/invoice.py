import asyncio
import datetime
from pathlib import Path
from typing import Any, cast

import structlog
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.core.supplement_models import MaterialBOM
from app.services.compliance import is_post_denial_invoicing_locked
from app.services.pdf.constants import (
    BRAND_BORDER,
    BRAND_LIGHT_BG,
    BRAND_MUTED_BG,
    BRAND_NAVY,
    BRAND_SLATE,
    FIELD_DOCS_DIR,
    HOMEOWNER_PALETTE,
    INTERNAL_PALETTE,
)
from app.services.pdf.documents import (
    create_financial_table,
    create_header,
    get_audience_styles,
)
from app.services.pdf.engine import PDFEngine

logger = structlog.get_logger("app.services.pdf.invoice")


class InvoiceGenerator(PDFEngine):
    async def generate_retail_quote(
        self,
        job: dict,
        billable_squares: float,
        tiers: list[dict]
    ) -> str:
        """
        Generate a 3-tier retail roofing quote PDF.
        Returns the permanent vault path.
        """
        job_id = job.get("id", "UNKNOWN")
        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "Retail_Quote.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job_id, doc_type="RETAIL_QUOTE")
            story: list[Any] = []

            story.extend(create_header("ROOFING REPLACEMENT QUOTE", "homeowner", subtitle="Professional Tiered Valuation & Specification"))
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 10))

            # Measured area summary
            area_text = (
                f"<b>Measured Roof Area:</b> {billable_squares:.2f} squares "
                f"(includes standard geometry waste factor)"
            )
            story.append(Paragraph(area_text, styles["BodyText"]))
            story.append(Spacer(1, 12))

            # 3-Tier options table
            story.append(Paragraph("Select Your Roofing System Option:", styles["SectionHeading"]))
            story.append(Spacer(1, 4))

            table_cell_style = ParagraphStyle(
                "QuoteTableCell",
                parent=styles["BodyText"],
                fontSize=8.5,
                leading=11,
                textColor=colors.HexColor("#1e293b")
            )

            header = ["Option", "System", "Description", "Total Price"]
            rows: list[list[Any]] = [header]
            labels = ["A", "B", "C"]
            for i, tier in enumerate(tiers):
                rows.append([
                    labels[i] if i < len(labels) else str(i + 1),
                    Paragraph(f"<b>{tier['name']}</b>", table_cell_style),
                    Paragraph(tier["description"], table_cell_style),
                    f"${tier['total_price']:,.2f}"
                ])

            t = Table(rows, colWidths=[45, 125, 230, 110])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BRAND_NAVY),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9.5),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (1, 0), (2, -1), 'LEFT'),
                ('ALIGN', (3, 0), (3, -1), 'RIGHT'),  # strict right-align currency
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND_LIGHT_BG, BRAND_MUTED_BG]),
                ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('PADDING', (0, 0), (-1, -1), 6),
                ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (3, 1), (3, -1), 10),
            ]))
            story.append(t)
            story.append(Spacer(1, 14))

            # What's included block
            story.append(Paragraph("All Options Include:", styles["SectionHeading"]))
            story.append(Spacer(1, 4))
            included = [
                "Complete tear-off and disposal of existing roofing materials",
                "New synthetic underlayment and heavy-duty drip edge installation",
                "Re-flashing of all roof penetrations, pipe boots, and valleys",
                "Explicit 1-Year Workmanship Warranty on all labor and craft quality",
                "Complete haul-away and magnet sweep job site cleanup",
            ]
            for item in included:
                story.append(Paragraph(f"✓ &nbsp;{item}", styles["BodyText"]))
                story.append(Spacer(1, 2))
            story.append(Spacer(1, 12))

            # Quote validity disclaimer
            validity_text = (
                f"This quote is valid for 30 days from {datetime.date.today().isoformat()}. "
                "Prices subject to material supplier adjustments. Does not include permit fees or code-required decking replacement."
            )
            story.append(self._box_warning("Quote Validity & Scope Terms", validity_text, BRAND_SLATE))
            story.append(Spacer(1, 14))

            # Acceptance signature
            story.append(Paragraph("To Accept This Quote:", styles["SectionHeading"]))
            story.append(Paragraph("Select your option (A / B / C) and sign below:", styles["BodyText"]))
            story.append(Spacer(1, 4))
            story.append(self._build_signature_block(
                title1="Homeowner Signature & Option Selection",
                title2="Date"
            ))

            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath

    async def generate_monthly_financial_summary(self, month: int, year: int) -> str:
        """Generate a professional PDF summary for the specified month."""
        from app.core.database import get_monthly_financials
        
        log = logger.bind(month=month, year=year)
        log.info("monthly_summary_generation_started")
        
        filepath = str(FIELD_DOCS_DIR / f"Monthly_Financial_Summary_{year}_{month:02d}.pdf")
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        styles = get_audience_styles("internal")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, top_margin=120, job_id="MONTHLY", doc_type="MONTHLY_SUMMARY")
            story: list[Any] = []
            
            story.extend(create_header(f"Monthly Financial Summary — {year}-{month:02d}", "internal"))
            
            jobs = get_monthly_financials(month, year)
            
            if not jobs:
                story.append(Paragraph("No INVOICED or CLOSED jobs found for this period.", styles["BodyText"]))
                doc.build(story)
                return
            
            total_rev = 0.0
            total_cogs = 0.0
            total_comm = 0.0
            
            # Details Table
            table_data = [["Job ID", "Homeowner", "Revenue", "COGS", "Gross Margin"]]
            
            for j in jobs:
                rev = j.get("revenue_cents")
                mat = j.get("material_cost_cents")
                lab = j.get("labor_cost_cents")

                if rev is None or mat is None or lab is None:
                    raise ValueError(
                        f"Job {j.get('id', 'UNKNOWN')} missing critical financial field "
                        f"(revenue_cents={rev}, material_cost_cents={mat}, labor_cost_cents={lab}). "
                        "Resolve in database before generating financial report."
                    )

                rev = rev / 100.0
                mat = mat / 100.0
                lab = lab / 100.0

                oh_pct = j.get("overhead_pct", 0.0)
                comm_pct = j.get("canvasser_commission_pct", 0.0)
                
                oh_val = oh_pct if oh_pct < 1 else (oh_pct / 100.0)
                comm_val = comm_pct if comm_pct < 1 else (comm_pct / 100.0)
                
                cogs = mat + lab + ((mat + lab) * oh_val)
                comm = rev * comm_val
                margin = rev - cogs - comm
                
                total_rev += rev
                total_cogs += cogs
                total_comm += comm
                
                table_data.append([
                    j["id"][:8], 
                    j["homeowner_name"], 
                    f"${rev:,.2f}", 
                    f"${cogs:,.2f}", 
                    f"${margin:,.2f}"
                ])
                
            total_margin = total_rev - total_cogs - total_comm
            
            # Summary Block
            summary_data = [
                ["Total Revenue:", f"${total_rev:,.2f}"],
                ["Total COGS:", f"${total_cogs:,.2f}"],
                ["Total Commissions:", f"${total_comm:,.2f}"],
                ["Total Gross Margin:", f"${total_margin:,.2f}"]
            ]
            
            st = Table(summary_data, colWidths=[150, 150], hAlign='LEFT')
            st.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), BRAND_LIGHT_BG),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ('PADDING', (0, 0), (-1, -1), 6)
            ]))
            
            story.append(Paragraph("Executive Summary", styles["SectionHeading"]))
            story.append(st)
            story.append(Spacer(1, 16))
            
            # Details Block
            story.append(Paragraph("Job Details Breakdown", styles["SectionHeading"]))
            story.append(Spacer(1, 4))
            
            dt = Table(table_data, colWidths=[80, 150, 90, 90, 100])
            dt.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BRAND_SLATE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('ALIGN', (0, 0), (1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),  # strict right-align Revenue, Costs, Margin
                ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND_LIGHT_BG, BRAND_MUTED_BG]),
                ('PADDING', (0, 0), (-1, -1), 6)
            ]))
            story.append(dt)
            
            doc.build(story)
            
        await asyncio.to_thread(build_pdf)
        log.info("monthly_summary_generation_complete", filepath=filepath)
        return filepath

    async def generate_material_po(self, job: dict, bom: MaterialBOM, supplier_name: str, delivery_date: str) -> str:
        """
        Generate a Material Purchase Order PDF for the supplier.
        Returns the absolute filepath to the saved PDF.
        """
        log = logger.bind(job_id=job["id"], supplier=supplier_name)
        log.info("material_po_generation_started")

        filepath = str(FIELD_DOCS_DIR / job["id"] / f"PO_{supplier_name.replace(' ', '_')}.pdf")
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

        styles = get_audience_styles("internal")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job["id"], doc_type="MATERIAL_PO")
            story: list[Any] = []
            
            story.extend(create_header("MATERIAL PURCHASE ORDER", "internal", subtitle=f"Supplier: {supplier_name}"))
            
            # --- 1. Order Details ---
            po_number = f"PO-{job['id'][:8].upper()}-{datetime.date.today().isoformat()}"
            po_meta = [
                ["PO Number:", po_number, "Order Date:", datetime.date.today().isoformat()],
                ["Supplier:", supplier_name, "Delivery Date:", delivery_date],
                ["Property:", f"{job['address_line1']}, {job['city']}, {job['state']}", "Claim #:", job.get("claim_number", "N/A")],
            ]
            t_meta = Table(po_meta, colWidths=[90, 170, 90, 160])
            t_meta.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), BRAND_LIGHT_BG),
                ('BACKGROUND', (2, 0), (2, -1), BRAND_LIGHT_BG),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ('PADDING', (0, 0), (-1, -1), 5),
                ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ]))
            story.append(t_meta)
            story.append(Spacer(1, 14))
            
            # --- 2. BOM Table ---
            story.append(Paragraph("Material Bill of Quantities:", styles["SectionHeading"]))
            story.append(Spacer(1, 4))
            
            table_data = [["Material Type", "Quantity", "Unit"]]
            
            table_data.append(["Field System", "", ""])
            table_data.append(["  Field Shingles", str(bom.field_shingle_bundles), "Bundles"])
            table_data.append(["  Starter Shingles", str(bom.starter_bundles), "Bundles"])
            table_data.append(["  Hip & Ridge", str(bom.ridge_cap_bundles), "Bundles"])
            
            table_data.append(["Underlayments", "", ""])
            table_data.append(["  Ice & Water Shield", str(bom.ice_water_rolls), "Rolls"])
            table_data.append(["  Synthetic Underlayment", str(bom.underlayment_rolls), "Rolls"])
            
            table_data.append(["Metal & Trim", "", ""])
            table_data.append(["  Drip Edge (10ft)", str(bom.drip_edge_pieces), "Pieces"])
            
            t = Table(table_data, colWidths=[270, 120, 120])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BRAND_SLATE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),  # right-align numeric columns
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND_LIGHT_BG, BRAND_MUTED_BG]),
                
                # Subheaders
                ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor("#e2e8f0")),
                ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 5), (-1, 5), colors.HexColor("#e2e8f0")),
                ('FONTNAME', (0, 5), (-1, 5), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 8), (-1, 8), colors.HexColor("#e2e8f0")),
                ('FONTNAME', (0, 8), (-1, 8), 'Helvetica-Bold'),
                ('PADDING', (0, 0), (-1, -1), 5),
            ]))
            story.append(t)
            story.append(Spacer(1, 14))
            
            # --- 3. Special Instructions ---
            story.append(Paragraph("Delivery Instructions:", styles["SectionHeading"]))
            story.append(Paragraph("Deliver materials to driveway; no heavy yard entry without prior contractor clearance.", styles["BodyText"]))
            story.append(Spacer(1, 14))
            story.append(Paragraph("<b>Supplier Verification & Signature:</b> ___________________________ &nbsp;&nbsp; <b>Date:</b> _________", styles["BodyText"]))
            
            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("material_po_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("material_po_generation_failed", error=str(exc))
            raise

    async def generate_estimate_pdf(self, data: dict, job_id: str) -> str:
        """
        Generate a PDF estimate from AI-structured data and return the absolute filepath.
        Uses a secure temporary file that the caller should clean up when done.
        """
        log = logger.bind(job_id=job_id)
        log.info("pdf_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "estimate.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job_id, doc_type="ESTIMATE")
            story: list[Any] = []
            
            story.extend(create_header("ROOFING ESTIMATE & SCOPE SPECIFICATION", "homeowner", subtitle=f"Job Identifier: {job_id}"))
            story.append(Spacer(1, 8))
            
            # --- Scope of Work / Materials ---
            story.append(Paragraph("Scope of Work & Material Specifications:", styles["SectionHeading"]))
            story.append(Spacer(1, 4))
            materials = data.get("materials", [])
            
            mat_map = {
                "field_shingle_bundles": "Field Shingles (Bundles)",
                "starter_bundles": "Starter Shingles (Bundles)",
                "ridge_cap_bundles": "Ridge Cap (Bundles)",
                "ice_water_rolls": "Ice & Water Shield (Rolls)",
                "underlayment_rolls": "Synthetic Underlayment (Rolls)",
                "drip_edge_pieces": "Drip Edge (Pieces)",
                "vents_count": "Roof Vents (Count)",
                "nails_boxes": "Nails (Boxes)",
                "sealant_tubes": "Sealant (Tubes)"
            }
            
            if materials:
                clean_materials = []
                for m in materials:
                    m_str = str(m)
                    if ":" in m_str:
                        key, val = m_str.split(":", 1)
                        clean_key = mat_map.get(key.strip(), key.strip().replace("_", " ").title())
                        clean_materials.append([clean_key, val.strip()])
                    else:
                        clean_materials.append([m_str, "1"])
                        
                t_data = [["Material Specification", "Quantity / Unit"]] + clean_materials
                t = Table(t_data, colWidths=[380, 130])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), BRAND_NAVY),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                    ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                    ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [BRAND_LIGHT_BG, BRAND_MUTED_BG]),
                    ('PADDING', (0, 0), (-1, -1), 6)
                ]))
                story.append(t)
            else:
                story.append(Paragraph("No materials specified.", styles["BodyText"]))
            story.append(Spacer(1, 14))
            
            # --- Total Cost ---
            total_cost = data.get("total_cost", 0.0)
            total_style = ParagraphStyle(
                name="TotalCost",
                parent=styles["BodyText"],
                fontSize=13,
                leading=16,
                fontName="Helvetica-Bold",
                textColor=BRAND_NAVY,
                alignment=2  # 2=TA_RIGHT
            )
            story.append(Paragraph(f"Estimated Valuation Total: ${total_cost:,.2f}", total_style))
            story.append(Paragraph("(Includes Labor, Material Waste, and Applicable Sales Taxes)", styles["FinePrint"]))
            story.append(Spacer(1, 20))
            
            # --- Statutory Deductible & Legal Terms ---
            deductible_disclosure = (
                "<b>GEORGIA STATUTORY DEDUCTIBLE DISCLOSURE (O.C.G.A. § 33-24-59.27 / HB 423):</b> "
                "It is unlawful under Georgia law for a contractor to pay, rebate, waive, or promise to waive all or any part of an insurance deductible. "
                "The property owner is legally obligated to pay the deductible amount specified in their insurance policy directly to the contractor upon project completion.<br/><br/>"
                "<b>Scope of Work & Change Orders:</b> This estimate covers explicitly listed materials and applications. "
                "Any hidden structural rot, decking damage, or code upgrades discovered during tear-off will be handled via a supplemental change order.<br/><br/>"
                "<b>Workmanship Warranty:</b> Wickham Roofing LLC provides an explicit <b>1-Year Workmanship Warranty</b> "
                "on all labor and installation from project completion. Material warranties are provided directly by the manufacturer.<br/><br/>"
                "<b>Payment Terms:</b> All balances are due upon job completion. Unpaid invoices past 30 days are subject to standard financing interest rates."
            )
            story.append(self._box_warning("STATUTORY DISCLOSURES & TERMS OF ESTIMATE", deductible_disclosure, BRAND_NAVY))
            
            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("pdf_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("pdf_generation_failed", error=str(exc))
            raise

    async def generate_final_invoice(self, job: dict, invoice_data: dict) -> str:
        """
        Generate a professional final invoice PDF.
        Enforces Georgia statutory post-denial invoicing lock (O.C.G.A. § 10-1-393.12).
        """
        job_id = job.get("id", "UNKNOWN")
        
        # Check post-denial lock
        locked, reason, deadline = is_post_denial_invoicing_locked(job_id)
        if locked:
            raise ValueError(
                f"Cannot generate final invoice: Job {job_id} is subject to a 5-business-day post-denial invoicing lock under O.C.G.A. § 10-1-393.12 ({reason})."
            )

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "Final_Invoice.pdf")

        styles = get_audience_styles("homeowner")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, job_id=job_id, doc_type="INVOICE")
            story: list[Any] = []

            inv_number = invoice_data.get("invoice_number", f"INV-{job_id[:8].upper()}")
            story.extend(create_header("FINAL RESTORATION INVOICE", "homeowner", subtitle=f"Invoice #{inv_number}"))
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 10))

            items = invoice_data.get("items", [
                {"description": "Roof Replacement per Insurance Scope", "amount": invoice_data.get("total_amount", 0.0)}
            ])
            
            rows = [["Line Item Description", "Amount"]]
            subtotal = 0.0
            for item in items:
                amt = float(item.get("amount", 0.0))
                subtotal += amt
                rows.append([item.get("description", "Roofing Service"), f"${amt:,.2f}"])
            
            deductible = float(invoice_data.get("deductible_amount", 0.0))
            payments_applied = float(invoice_data.get("payments_applied", 0.0))
            balance_due = subtotal - payments_applied

            if deductible > 0:
                rows.append(["Homeowner Deductible Responsibility (O.C.G.A. § 33-24-59.27)", f"${deductible:,.2f}"])
            if payments_applied > 0:
                rows.append(["Less: Insurance & Prior Payments Applied", f"(${payments_applied:,.2f})"])
            rows.append(["TOTAL BALANCE DUE UPON RECEIPT", f"${balance_due:,.2f}"])

            t = Table(rows, colWidths=[380, 130])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BRAND_NAVY),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9.5),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [BRAND_LIGHT_BG, BRAND_MUTED_BG]),
                ('BACKGROUND', (0, -1), (-1, -1), BRAND_NAVY),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 10.5),
                ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ('PADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(t)
            story.append(Spacer(1, 14))

            # Statutory Deductible Notice
            deductible_disclosure = (
                "<b>GEORGIA STATUTORY DEDUCTIBLE DISCLOSURE (O.C.G.A. § 33-24-59.27 / HB 423):</b> "
                "It is unlawful under Georgia law for a contractor to pay, rebate, waive, or promise to waive all or any part of an insurance deductible. "
                "The property owner is legally obligated to pay the deductible amount specified in their insurance policy directly to the contractor upon project completion.<br/><br/>"
                "<b>Workmanship Warranty:</b> All restoration work completed by Wickham Roofing LLC is backed by our explicit <b>1-Year Workmanship Warranty</b>."
            )
            story.append(self._box_warning("STATUTORY NOTICE & PAYMENT TERMS", deductible_disclosure, BRAND_NAVY))
            story.append(Spacer(1, 14))

            story.append(self._build_signature_block(title1="Authorized Contractor Signature", title2="Date"))
            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath



