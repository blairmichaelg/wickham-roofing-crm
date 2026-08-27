"""
Neighbor Letter PDF Generator.

Generates a one-page storm-aware "door-drop" letter for homes adjacent
to a just-completed job. Triggered when a job reaches INSTALL_COMPLETED.

Follows the existing PDFEngine pattern used by documents.py / invoice.py.
"""
from __future__ import annotations

import asyncio
import datetime
import structlog

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, HRFlowable

from app.services.pdf.constants import COMPANY_EMAIL, COMPANY_NAME, COMPANY_PHONE, FIELD_DOCS_DIR
from app.services.pdf.engine import PDFEngine

logger = structlog.get_logger("app.services.pdf.neighbor_letter")


class NeighborLetterGenerator(PDFEngine):
    """Generates a single-page neighbor outreach letter for post-install jobsite campaigns."""

    async def generate(
        self,
        job: dict,
        storm_events: list[dict] | None = None,
    ) -> str:
        """
        Generate the neighbor letter PDF and save it to the job's document directory.

        Args:
            job: Job dict (must include address_line1, city, state, postal_code).
            storm_events: Optional list of storm event dicts for context.

        Returns:
            Absolute path string to the generated PDF.
        """
        job_id = job.get("id", "UNKNOWN")
        log = logger.bind(job_id=job_id)
        log.info("neighbor_letter_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "Neighbor_Letter.pdf")

        storm_events = storm_events or []

        def _build() -> None:
            doc = self._get_doc_template(
                filepath,
                top_margin=120,
                job_id=job_id,
                doc_type="NEIGHBOR_LETTER",
            )
            story: list = []

            # ── Salutation ──────────────────────────────────────────────────
            story.append(Paragraph("Dear Neighbor,", self.custom_styles["SectionHeading"]))
            story.append(Spacer(1, 10))

            # ── Opening paragraph: what just happened on this street ─────────
            address = job.get("address_line1", "a property on your street")
            city = job.get("city", "")
            state = job.get("state", "")
            location_str = f"{address}, {city}, {state}".strip(", ")

            opening = (
                f"We are reaching out because we recently completed a full roof replacement "
                f"at <b>{location_str}</b>. "
                "After a thorough inspection, we discovered significant storm damage — "
                "damage that is commonly found on neighboring homes following the same weather event."
            )
            story.append(Paragraph(opening, self.custom_styles["BodyText"]))
            story.append(Spacer(1, 10))

            # ── Storm context block (only if events provided) ────────────────
            if storm_events:
                ev = storm_events[0]
                etype = ev.get("event_type") or ev.get("event_types") or "storm"
                loc = ev.get("county") or ev.get("location") or "your area"
                hail = ev.get("hail_size_inches") or ev.get("max_hail_inches")
                wind = ev.get("wind_speed_mph") or ev.get("max_wind_mph")
                ts = (ev.get("report_time_utc") or ev.get("last_event_utc") or "")[:10]

                detail_parts: list[str] = []
                if hail:
                    detail_parts.append(f"{hail}-inch hail")
                if wind:
                    detail_parts.append(f"{wind} mph winds")
                detail = " and ".join(detail_parts) if detail_parts else etype

                storm_para = (
                    f"NWS storm records confirm a <b>{etype}</b> event in <b>{loc}</b>"
                    f"{' on ' + ts if ts else ''}, bringing <b>{detail}</b>. "
                    "Hail and high winds can cause granule loss, cracked shingles, and damaged flashing "
                    "that may not be visible from the ground — but can result in costly leaks and interior "
                    "water damage if left unaddressed."
                )
                story.append(Paragraph(storm_para, self.custom_styles["BodyText"]))
                story.append(Spacer(1, 10))

            # ── Free inspection offer ────────────────────────────────────────
            offer = (
                "<b>We are offering FREE roof inspections to all neighbors in this area.</b> "
                "Our licensed inspectors will photograph and document any damage at no cost "
                "to you. If damage is found, we can work directly with your insurance carrier "
                "to file a claim — so you pay only your deductible. "
                "There is no obligation, and the inspection takes only 30–45 minutes."
            )
            story.append(Paragraph(offer, self.custom_styles["BodyText"]))
            story.append(Spacer(1, 16))

            # ── Divider + CTA ────────────────────────────────────────────────
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e3a8a")))
            story.append(Spacer(1, 10))

            cta = (
                f"<b>Call or text us today to schedule your FREE inspection:</b><br/>"
                f"📞 {COMPANY_PHONE}<br/>"
                f"✉ {COMPANY_EMAIL}"
            )
            story.append(Paragraph(cta, self.custom_styles["SectionHeading"]))
            story.append(Spacer(1, 12))

            # ── Legal footer ─────────────────────────────────────────────────
            footer = (
                "Wickham Roofing LLC is a licensed and insured Georgia roofing contractor. "
                "We operate in compliance with O.C.G.A. § 33-24-59.27 (HB 423). "
                "Homeowners are responsible for their insurance deductible as required by law. "
                f"Letter generated {datetime.date.today().isoformat()}."
            )
            story.append(Paragraph(footer, self.custom_styles["FinePrint"]))

            doc.build(story)

        await asyncio.to_thread(_build)
        log.info("neighbor_letter_generation_complete", path=filepath)
        return filepath
