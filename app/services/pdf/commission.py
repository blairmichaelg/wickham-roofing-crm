import asyncio
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.platypus import (
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

logger = structlog.get_logger("app.services.pdf")
from app.services.pdf.constants import FIELD_DOCS_DIR
from app.services.pdf.engine import PDFEngine


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
        job_id   = job.get("id", "UNKNOWN")
        job_dir  = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(
            job_dir / "Commission_Statement.pdf"
        )

        def build_pdf() -> None:
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            doc   = self._get_doc_template(
                filepath, job_id=job_id, doc_type="COMMISSION_STATEMENT"
            )
            story: list[Any] = []

            story.append(Paragraph(
                "INDEPENDENT CONTRACTOR "
                "COMMISSION STATEMENT",
                self.custom_styles["Title"]
            ))
            story.append(Spacer(1, 8))
            story.append(
                self._build_metadata_table(job)
            )
            story.append(Spacer(1, 16))

            story.append(Paragraph(
                f"<b>Canvasser:</b> "
                f"{commission_data['canvasser_name']}",
                self.custom_styles["BodyText"]
            ))
            story.append(Spacer(1, 10))

            rows = [
                ["Line Item", "Amount"],
                ["Total Contract Revenue",
                 f"${commission_data['revenue_val']:,.2f}"],
                ["Less: Material Cost",
                 f"(${commission_data['material_cost_val']:,.2f})"],
                ["Less: Labor Cost",
                 f"(${commission_data['labor_cost_val']:,.2f})"],
                ["Less: Overhead",
                 f"(${commission_data['overhead_amount']:,.2f})"],
                ["Gross Profit",
                 f"${commission_data['gross_profit']:,.2f}"],
                ["Commission Rate",
                 f"{commission_data['commission_pct']*100:.1f}%"],
                ["COMMISSION EARNED",
                 f"${commission_data['commission_amount']:,.2f}"],
            ]
            t = Table(rows, colWidths=[330, 180])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1e3a8a")),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,0), 10),
                ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#166534")),
                ('TEXTCOLOR', (0,-1), (-1,-1), colors.white),
                ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
                ('FONTSIZE', (0,-1), (-1,-1), 11),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
                ('ALIGN', (1,0), (1,-1), 'RIGHT'),
                ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.HexColor("#f8fafc"), colors.white]),
                ('PADDING', (0,0), (-1,-1), 7),
            ]))
            story.append(t)
            story.append(Spacer(1, 24))

            story.append(
                self._build_signature_block(
                    title1=(
                        "Authorized Signature — "
                        "Wickham Roofing"
                    ),
                    title2="Date"
                )
            )
            story.append(Spacer(1, 12))
            story.append(
                self._build_signature_block(
                    title1=(
                        "Contractor Signature — "
                        f"{commission_data['canvasser_name']}"
                    ),
                    title2="Date Received"
                )
            )
            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath


