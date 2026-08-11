import asyncio
import datetime
from pathlib import Path

import structlog
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from app.core.supplement_models import MaterialBOM

logger = structlog.get_logger("app.services.pdf")
from app.services.pdf.constants import FIELD_DOCS_DIR
from app.services.pdf.engine import PDFEngine


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

        def build_pdf():
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            import datetime as _dt
            doc = self._get_doc_template(filepath,
                                         job_id=job_id)
            story = []

            story.append(Paragraph(
                "ROOFING REPLACEMENT QUOTE",
                self.custom_styles["Title"]
            ))
            story.append(Spacer(1, 12))
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 6))

            # Measured area summary
            story.append(Paragraph(
                f"<b>Measured Roof Area:</b> "
                f"{billable_squares:.2f} squares "
                f"(includes 10% waste factor)",
                self.custom_styles["BodyText"]
            ))
            story.append(Spacer(1, 16))

            # 3-Tier options table
            story.append(Paragraph(
                "Select Your Roofing System:",
                self.custom_styles["SectionHeading"]
            ))

            header = ["Option", "System", "Description",
                      "Total Price"]
            rows = [header]
            labels = ["A", "B", "C"]
            for i, tier in enumerate(tiers):
                rows.append([
                    labels[i],
                    tier["name"],
                    tier["description"],
                    f"${tier['total_price']:,.2f}"
                ])

            t = Table(rows, colWidths=[30, 130, 220, 90])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,0), 10),
                ('ROWBACKGROUNDS', (0,1), (-1,-1),
                 [colors.lightblue, colors.white,
                  colors.lightgrey]),
                ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('PADDING', (0,0), (-1,-1), 8),
                ('FONTNAME', (0,1), (0,-1), 'Helvetica-Bold'),
                ('FONTSIZE', (3,1), (3,-1), 11),
                ('ALIGN', (3,0), (3,-1), 'RIGHT'),
            ]))
            story.append(t)
            story.append(Spacer(1, 20))

            # What's included block
            story.append(Paragraph(
                "All Options Include:",
                self.custom_styles["SectionHeading"]
            ))
            included = [
                "Complete tear-off and disposal of existing roofing materials",
                "New synthetic underlayment and heavy-duty drip edge installation",
                "Re-flashing of all roof penetrations, pipe boots, and valleys",
                "Explicit 1-Year Workmanship Warranty on all labor and craft quality",
                "Complete haul-away and magnet sweep job site cleanup",
            ]
            for item in included:
                story.append(Paragraph(
                    f"✓  {item}",
                    self.custom_styles["BodyText"]
                ))
            story.append(Spacer(1, 16))

            # Quote validity disclaimer
            story.append(self._box_warning(
                "Quote Validity",
                f"This quote is valid for 30 days from "
                f"{_dt.date.today().isoformat()}. "
                f"Prices subject to material cost changes. "
                f"Does not include permit fees or code-required "
                f"decking replacement.",
                colors.darkgrey
            ))
            story.append(Spacer(1, 24))

            # Acceptance signature
            story.append(Paragraph(
                "To Accept This Quote:",
                self.custom_styles["SectionHeading"]
            ))
            story.append(Paragraph(
                "Circle your selected option (A / B / C) "
                "and sign below.",
                self.custom_styles["BodyText"]
            ))
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
        
        def build_pdf():
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            doc = self._get_doc_template(filepath, top_margin=120, job_id="MONTHLY", doc_type="MONTHLY_SUMMARY")
            story = []
            
            story.append(Paragraph(f"Monthly Financial Summary - {year}-{month:02d}", self.custom_styles["Title"]))
            story.append(Spacer(1, 20))
            
            jobs = get_monthly_financials(month, year)
            
            if not jobs:
                story.append(Paragraph("No INVOICED or CLOSED jobs found for this period.", self.custom_styles["BodyText"]))
                doc.build(story)
                return
            
            total_rev = 0.0
            total_cogs = 0.0
            total_comm = 0.0
            
            # Details Table
            table_data = [["Job ID", "Homeowner", "Revenue", "Costs", "Margin"]]
            
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
                
                cogs = mat + lab + ((mat+lab)*oh_val)
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
                ('BACKGROUND', (0,0), (0,-1), colors.lightgrey),
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                ('PADDING', (0,0), (-1,-1), 6)
            ]))
            
            story.append(Paragraph("Executive Summary", self.custom_styles["SectionHeading"]))
            story.append(st)
            story.append(Spacer(1, 20))
            
            # Details Block
            story.append(Paragraph("Job Details", self.custom_styles["SectionHeading"]))
            
            dt = Table(table_data, colWidths=[80, 150, 80, 80, 80])
            dt.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.grey),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('ALIGN', (0,0), (1,-1), 'LEFT'),
                ('ALIGN', (2,0), (-1,-1), 'RIGHT'), # explicit right-align Revenue, Costs, Margin
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
                ('PADDING', (0,0), (-1,-1), 6)
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

        def build_pdf():
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            doc = self._get_doc_template(filepath, job_id=job["id"], doc_type="MATERIAL_PO")
            story = []
            
            # --- 1. Order Details ---
            po_number = f"PO-{job['id'][:8].upper()}-{datetime.date.today().isoformat()}"
            story.append(Paragraph(f"<b>PO Number:</b> {po_number}", self.custom_styles["BodyText"]))
            story.append(Paragraph(f"<b>Supplier:</b> {supplier_name}", self.custom_styles["BodyText"]))
            story.append(Paragraph("<b>Supplier Account #:</b> Wickham Roofing Commercial Account", self.custom_styles["BodyText"]))
            story.append(Paragraph(f"<b>Order Date:</b> {datetime.date.today().isoformat()}", self.custom_styles["BodyText"]))
            story.append(Paragraph(f"<b>Requested Delivery Date:</b> {delivery_date}", self.custom_styles["BodyText"]))
            story.append(Spacer(1, 12))
            
            story.append(Paragraph("Delivery Information:", self.custom_styles["SectionHeading"]))
            story.append(Paragraph(f"<b>Homeowner:</b> {job['homeowner_name']}", self.custom_styles["BodyText"]))
            story.append(Paragraph(f"<b>Address:</b> {job['address_line1']}, {job['city']}, {job['state']} {job['postal_code']}", self.custom_styles["BodyText"]))
            story.append(Paragraph(f"<b>Claim #:</b> {job.get('claim_number', 'N/A')}", self.custom_styles["BodyText"]))
            story.append(Spacer(1, 18))
            
            # --- 2. BOM Table ---
            story.append(Paragraph("Material Bill of Quantities:", self.custom_styles["SectionHeading"]))
            
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
            
            # Build alternating backgrounds, but explicitly style subheaders
            row_colors = [('BACKGROUND', (0, i), (-1, i), colors.whitesmoke if i % 2 == 1 else colors.white) for i in range(1, len(table_data))]
            
            t = Table(table_data, colWidths=[200, 100, 100])
            base_style = [
                ('BACKGROUND', (0,0), (-1,0), colors.grey),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (0,-1), 'LEFT'),
                ('ALIGN', (1,0), (-1,-1), 'RIGHT'), # right align numeric columns
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0,0), (-1,0), 8),
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
                
                # Subheaders
                ('BACKGROUND', (0,1), (-1,1), colors.lightgrey),
                ('FONTNAME', (0,1), (-1,1), 'Helvetica-Bold'),
                ('BACKGROUND', (0,5), (-1,5), colors.lightgrey),
                ('FONTNAME', (0,5), (-1,5), 'Helvetica-Bold'),
                ('BACKGROUND', (0,8), (-1,8), colors.lightgrey),
                ('FONTNAME', (0,8), (-1,8), 'Helvetica-Bold'),
            ]
            t.setStyle(TableStyle(base_style + row_colors))
            story.append(t)
            story.append(Spacer(1, 20))
            
            # --- 3. Special Instructions ---
            story.append(Paragraph("Special Instructions:", self.custom_styles["SectionHeading"]))
            story.append(Paragraph("Deliver to driveway; no yard entry with loaded truck.", self.custom_styles["BodyText"]))
            story.append(Spacer(1, 20))
            story.append(Paragraph("<b>Total Estimated Cost:</b> $___________", self.custom_styles["SectionHeading"]))
            
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

        # Create a secure temporary file that persists until manually deleted
        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "estimate.pdf")  # Close so ReportLab can write to it

        def build_pdf():
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            doc = self._get_doc_template(filepath, job_id=job_id, doc_type="ESTIMATE")
            story = []
            
            # Styles
            normal_style = self.styles["Normal"]
            
            # Custom Legal Style
            legal_style = ParagraphStyle(
                name="LegalDisclaimer",
                parent=self.styles["Normal"],
                fontSize=8,
                leading=10,
                textColor=colors.dimgrey,
            )
            
            # --- 1. Metadata ---
            story.append(Paragraph("<b>Roofing Estimate</b>", self.styles["Heading2"]))
            story.append(Paragraph(f"<b>Job ID:</b> {job_id}", normal_style))
            story.append(Spacer(1, 12))
            
            # --- 3. Materials ---
            story.append(Paragraph("<b>Scope of Work / Materials:</b>", normal_style))
            story.append(Spacer(1, 6))
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
                        
                t_data = [["Material", "Quantity"]] + clean_materials
                t = Table(t_data, colWidths=[350, 100])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
                    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
                    ('PADDING', (0,0), (-1,-1), 6)
                ]))
                story.append(t)
            else:
                story.append(Paragraph("No materials specified.", normal_style))
            story.append(Spacer(1, 12))
            
            # --- 4. Total Cost ---
            total_cost = data.get("total_cost", 0.0)
            total_style = ParagraphStyle(
                name="TotalCost",
                parent=normal_style,
                fontSize=14,
                fontName="Helvetica-Bold",
                alignment=2 # 2=TA_RIGHT
            )
            story.append(Paragraph(f"Total Cost: ${total_cost:,.2f}", total_style))
            story.append(Paragraph("(Includes Labor, Material Waste, and Applicable Taxes)", self.custom_styles["FinePrint"]))
            story.append(Spacer(1, 40))
            
            # --- 5. Legal Terms & Disclaimers Boilerplate ---
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey, spaceBefore=0, spaceAfter=12))
            legal_text = (
                "<b>Scope of Work:</b> This estimate covers explicitly listed materials and applications. "
                "Any hidden structural rot, decking damage, or code upgrades discovered during tear-off "
                "will be handled via a supplemental change order.<br/><br/>"
                "<b>Workmanship Warranty:</b> Wickham Roofing LLC provides an explicit <b>1-Year Workmanship Warranty</b> "
                "on all labor and installation from project completion. Material warranties are provided directly by the manufacturer.<br/><br/>"
                "<b>Payment Terms:</b> All balances are due upon job completion. Unpaid invoices past 30 days "
                "are subject to standard financing interest rates as specified by corporate policy."
            )
            story.append(Paragraph(legal_text, legal_style))
            
            doc.build(story)

        try:
            # Run the synchronous ReportLab generation in a thread
            await asyncio.to_thread(build_pdf)
            log.info("pdf_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("pdf_generation_failed", error=str(exc))
            raise


