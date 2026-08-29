import asyncio
from pathlib import Path
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.platypus import (
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.services.pdf.constants import (
    BRAND_BORDER,
    BRAND_GREEN,
    BRAND_LIGHT_BG,
    BRAND_MUTED_BG,
    BRAND_NAVY,
    BRAND_SLATE,
    FIELD_DOCS_DIR,
    INTERNAL_PALETTE,
)
from app.services.pdf.documents import create_financial_table, create_header, get_audience_styles
from app.services.pdf.engine import PDFEngine

logger = structlog.get_logger("app.services.pdf.commission")


class CommissionGenerator(PDFEngine):
    async def generate_commission_statement(
        self,
        job: dict,
        commission_data: dict
    ) -> str:
        """
        Generate Commission Statement functionality.
        
        Args:
            job (dict): job parameter.
            commission_data (dict): commission_data parameter.
        
        Returns:
            str: The resulting output.
        """
        job_id = job.get("id", "UNKNOWN")
        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "Commission_Statement.pdf")

        styles = get_audience_styles("internal")

        def build_pdf() -> None:
            doc = self._get_doc_template(
                filepath, job_id=job_id, doc_type="COMMISSION_STATEMENT"
            )
            story: list[Any] = []

            story.extend(create_header("INDEPENDENT CONTRACTOR COMMISSION STATEMENT", "internal"))

            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 14))

            canvasser_p = Paragraph(
                f"<b>Canvasser / Field Representative:</b> {commission_data['canvasser_name']}",
                styles["BodyText"]
            )
            story.append(canvasser_p)
            story.append(Spacer(1, 10))

            rows = [
                ["Financial Line Item", "Amount"],
                ["Total Contract Revenue", f"${commission_data['revenue_val']:,.2f}"],
                ["Less: Material Cost", f"(${commission_data['material_cost_val']:,.2f})"],
                ["Less: Labor Cost", f"(${commission_data['labor_cost_val']:,.2f})"],
                ["Less: Overhead", f"(${commission_data['overhead_amount']:,.2f})"],
                ["Gross Profit", f"${commission_data['gross_profit']:,.2f}"],
                ["Commission Rate", f"{commission_data['commission_pct']*100:.1f}%"],
                ["COMMISSION EARNED", f"${commission_data['commission_amount']:,.2f}"],
            ]

            t = Table(rows, colWidths=[330, 180])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BRAND_SLATE),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9.5),
                ('BACKGROUND', (0, -1), (-1, -1), BRAND_GREEN),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 10.5),
                ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [BRAND_LIGHT_BG, BRAND_MUTED_BG]),
                ('PADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(t)
            story.append(Spacer(1, 20))

            story.append(
                self._build_signature_block(
                    title1="Authorized Signature — Wickham Roofing",
                    title2="Date"
                )
            )
            story.append(Spacer(1, 10))
            story.append(
                self._build_signature_block(
                    title1=f"Contractor Signature — {commission_data['canvasser_name']}",
                    title2="Date Received"
                )
            )
            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath



