import asyncio
import datetime
import html
from pathlib import Path
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from app.core.supplement_models import DiscrepancyReport

logger = structlog.get_logger("app.services.pdf")
from app.services.pdf.constants import FIELD_DOCS_DIR
from app.services.pdf.engine import PDFEngine


class SupplementGenerator(PDFEngine):
    async def generate_evidence_grid(self, job: Any, signature_path: str | None = None) -> str:
        """
        Generate a multi-page, professional Evidence Grid appendix for insurance adjusters & office teams.
        Layout: Strict 2-column format. Left: Photo. Right: Forensic data matrix + objective note.
        Max 2 photos per page with formal figure numbering, header metadata, and contingency authorization block.
        """
        # Safely extract job fields whether job is Pydantic model or dict
        if hasattr(job, "job_id"):
            job_id = job.job_id
            homeowner = getattr(job, "homeowner_name", "Homeowner")
            address = getattr(job, "property_address", "N/A")
            inspector = getattr(job, "inspector_name", "Wickham Roofing LLC")
            inspection_date_obj = getattr(job, "inspection_date", None)
            claim_num = getattr(job, "claim_number", None) or "Pending Assignment"
            getattr(job, "analyses", [])
            photos = getattr(job, "photos", [])
        else:
            job_id = job.get("id") or job.get("job_id", "UNKNOWN")
            homeowner = job.get("homeowner_name", "Homeowner")
            address = f"{job.get('address_line1', '')}, {job.get('city', '')}, {job.get('state', '')} {job.get('postal_code', '')}".strip(" ,")
            inspector = job.get("canvasser_name") or job.get("inspector_name") or "Wickham Roofing LLC"
            inspection_date_obj = job.get("created_at") or job.get("inspection_date")
            claim_num = job.get("claim_number") or "Pending Assignment"
            job.get("analyses", [])
            photos = job.get("photos", [])

        # Extract job status for conditional footer copy
        if hasattr(job, "status"):
            job_status = str(getattr(job, "status", "LEAD_CAPTURED") or "LEAD_CAPTURED")
        elif isinstance(job, dict):
            job_status = str(job.get("status", "LEAD_CAPTURED") or "LEAD_CAPTURED")
        else:
            job_status = "LEAD_CAPTURED"

        if isinstance(inspection_date_obj, datetime.date) or isinstance(inspection_date_obj, datetime.datetime):
            inspection_date = inspection_date_obj.strftime("%B %d, %Y")
        else:
            inspection_date = str(inspection_date_obj or datetime.date.today().strftime("%B %d, %Y"))

        log = logger.bind(job_id=job_id)
        log.info("evidence_grid_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "evidence_grid.pdf")

        # --- Comprehensive Photo & Analysis Collection ---
        photo_files = []

        # From job object / dict
        if photos:
            for p in photos:
                fp = getattr(p, "filepath", None) or getattr(p, "storage_path", None)
                if isinstance(p, dict):
                    fp = p.get("filepath") or p.get("storage_path")
                if fp and Path(fp).exists() and Path(fp) not in photo_files:
                    photo_files.append(Path(fp))

        # From disk directory
        disk_photo_dir = FIELD_DOCS_DIR.parent / "field_photos" / job_id
        if disk_photo_dir.exists():
            for f in sorted(disk_photo_dir.glob("*.*")):
                if f.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"] and f not in photo_files:
                    photo_files.append(f)

        # From database documents vault
        try:
            from app.core.database import get_job_documents
            vault_docs = get_job_documents(job_id)
            for d in vault_docs:
                if d.get("category") == "INSPECTION_PHOTO" or (d.get("file_type") and d["file_type"].startswith("image/")):
                    sp = d.get("storage_path")
                    if sp and Path(sp).exists() and Path(sp) not in photo_files:
                        photo_files.append(Path(sp))
        except Exception:
            pass

        # Look up each photo's analysis by its sha256 hash — the actual cache key.
        # This is the only reliable mapping; filename-based lookup fails because
        # Gemini may output a different filename than the disk name.
        from app.core.cache import get_cached_analysis
        from app.core.inspection_models import _compute_sha256

        photo_analysis_pairs = []
        for pf in photo_files:
            try:
                sha = _compute_sha256(pf)
                analysis = get_cached_analysis(job_id, sha)
            except Exception:
                analysis = None
            photo_analysis_pairs.append((pf, analysis))

        if not signature_path or not Path(signature_path).exists():
            sig_c = FIELD_DOCS_DIR / job_id / f"{job_id}_contingency_sig.png"
            sig_r = FIELD_DOCS_DIR / job_id / f"{job_id}_retail_contract_sig.png"
            if sig_c.exists():
                signature_path = str(sig_c)
            elif sig_r.exists():
                signature_path = str(sig_r)

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, top_margin=110, job_id=job_id, doc_type="EVIDENCE_GRID")
            story: list[Any] = []

            # Color Palette
            NAVY = colors.HexColor("#1E3A8A")
            SLATE = colors.HexColor("#475569")
            LIGHT_BLUE = colors.HexColor("#EFF6FF")
            BLUE_BORDER = colors.HexColor("#3B82F6")
            BORDER_GREY = colors.HexColor("#CBD5E1")

            # Typography Styles
            title_style = ParagraphStyle(
                "GridTitle",
                parent=self.styles["Heading1"],
                fontSize=15,
                leading=18,
                fontName="Helvetica-Bold",
                textColor=NAVY,
                spaceAfter=2
            )
            subtitle_style = ParagraphStyle(
                "GridSubtitle",
                parent=self.styles["Normal"],
                fontSize=9,
                leading=12,
                fontName="Helvetica-Bold",
                textColor=SLATE,
                spaceAfter=10
            )
            body_style = ParagraphStyle(
                "GridBody",
                parent=self.styles["Normal"],
                fontSize=8,
                leading=11,
                textColor=colors.HexColor("#1E293B")
            )
            fig_header_style = ParagraphStyle(
                "FigHeader",
                parent=self.styles["Heading3"],
                fontSize=9,
                leading=12,
                fontName="Helvetica-Bold",
                textColor=NAVY,
                spaceAfter=4
            )

            # --- 1. Document Title ---
            story.append(Paragraph("INSPECTION EVIDENCE GRID", title_style))
            story.append(Paragraph("Technical Claim Support & Forensic Photo Matrix", subtitle_style))

            # --- 2. Property & Inspection Metadata Box ---
            meta_data = [
                [
                    Paragraph(f"<b>Homeowner:</b> {homeowner}", body_style),
                    Paragraph(f"<b>Inspection Date:</b> {inspection_date}", body_style)
                ],
                [
                    Paragraph(f"<b>Property Address:</b> {address}", body_style),
                    Paragraph(f"<b>Inspector / Rep:</b> {inspector}", body_style)
                ],
                [
                    Paragraph(f"<b>Claim / Policy #:</b> {claim_num}", body_style),
                    Paragraph("<b>Building Code Standard:</b> 2021 IRC", body_style)
                ]
            ]
            meta_table = Table(meta_data, colWidths=[270, 250])
            meta_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
                ('BOX', (0,0), (-1,-1), 1, BORDER_GREY),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
                ('PADDING', (0,0), (-1,-1), 5),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ]))
            story.append(meta_table)
            story.append(Spacer(1, 10))

            # --- 3. Photos & Analysis Cards ---
            if not photo_analysis_pairs:
                story.append(Paragraph("No photo evidence records ingested.", body_style))

            photos_on_page = 0
            for idx, (photo_path, analysis) in enumerate(photo_analysis_pairs):
                if photos_on_page >= 2:
                    story.append(PageBreak())
                    photos_on_page = 0

                try:

                    from app.workers.inspection_processor import resize_for_pdf
                    
                    safe_image_buffer = resize_for_pdf(photo_path, max_width=800)
                    img = Image(safe_image_buffer, width=275, height=180, kind='proportional')

                    has_ai = analysis is not None and (
                        getattr(analysis, "damage_type", None) is not None or 
                        getattr(analysis, "confidence", None) is not None
                    )

                    if has_ai:
                        dmg_det = "Yes" if getattr(analysis, "damage_detected", False) else "No"
                        raw_type = str(getattr(analysis, "damage_type", "Observed")).replace("DamageType.", "").capitalize()
                        dmg_type = raw_type
                        raw_sev = str(getattr(analysis, "severity", "Moderate")).replace("Severity.", "").capitalize()
                        severity = raw_sev
                        hail = "Yes" if getattr(analysis, "hail_hits_visible", False) else "No"
                        creases = "Yes" if getattr(analysis, "crease_marks", False) else "No"
                        granules = "Yes" if getattr(analysis, "granule_loss", False) else "No"
                        fiberglass = "Yes" if getattr(analysis, "exposed_fiberglass", False) else "No"
                        conf = getattr(analysis, "confidence", None)
                        conf_str = f"{conf * 100:.1f}%" if isinstance(conf, (int, float)) else "Verified"
                        
                        # Use the AI's forensic narrative if available
                        raw_narrative = getattr(analysis, "forensic_narrative", None)
                        alt_explanation = getattr(analysis, "alternative_explanation", None)
                        if raw_narrative:
                            note_text = f"<b>AI Forensic Note:</b> {html.escape(raw_narrative)}"
                            if alt_explanation and conf and conf < 0.9:
                                note_text += f" <i>Alternative: {html.escape(alt_explanation)}</i>"
                        else:
                            note_text = (
                                "<b>Objective Forensic Note:</b> AI Vision analysis detected structural anomaly "
                                f"({dmg_type}, {severity} severity). Documented for carrier loss adjustment."
                            )
                    else:
                        dmg_det = "No AI Analysis"
                        dmg_type = "Field Inspection Photo"
                        severity = "Not Classified"
                        hail = "—"
                        creases = "—"
                        granules = "—"
                        fiberglass = "—"
                        conf_str = "N/A"
                        
                        note_text = (
                            "<b>Field Photo — No AI analysis available:</b> This photo was captured on-site during the "
                            f"physical property inspection of {address}. "
                            "No AI vision analysis is currently available for this photo."
                        )

                    data_rows = [
                        ["Forensic Metric", "Result / Assessment"],
                        ["Damage Detected", dmg_det],
                        ["Primary Classification", dmg_type],
                        ["Overall Severity", severity],
                        ["Hail Impacts Visible", hail],
                        ["Crease / Lift Marks", creases],
                        ["Granule De-granulation", granules],
                        ["Exposed Substrate / Mat", fiberglass],
                        ["Verification Score", conf_str],
                    ]
                    
                    data_table = Table(data_rows, colWidths=[125, 110])
                    data_table.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,0), NAVY),
                        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0,0), (-1,-1), 8),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                        ('TOPPADDING', (0,0), (-1,-1), 3),
                        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#F8FAFC"), colors.white]),
                        ('GRID', (0,0), (-1,-1), 0.5, BORDER_GREY),
                        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                    ]))

                    note_para = Paragraph(note_text, body_style)
                    note_table = Table([[note_para]], colWidths=[235])
                    note_table.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,-1), LIGHT_BLUE),
                        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#93C5FD")),
                        ('LINELEFT', (0,0), (0,0), 3, BLUE_BORDER),
                        ('PADDING', (0,0), (-1,-1), 5),
                    ]))

                    info_column = [data_table, Spacer(1, 4), note_table]
                    fig_title = f"FIGURE {idx + 1}: {photo_path.name}"

                    grid_table = Table(
                        [
                            [Paragraph(fig_title, fig_header_style), ""],
                            [img, info_column]
                        ],
                        colWidths=[285, 240]
                    )
                    grid_table.setStyle(TableStyle([
                        ('SPAN', (0,0), (1,0)),
                        ('VALIGN', (0,0), (-1,-1), 'TOP'),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
                    ]))
                    
                    story.append(grid_table)
                    photos_on_page += 1
                except Exception as e:
                    log.warning("photo_render_skipped", filename=photo_path.name, error=str(e))
                    continue

            # --- 4. Client Authorization & Contingency Attestation Block ---
            story.append(Spacer(1, 10))
            auth_title_style = ParagraphStyle(
                "AuthTitle",
                parent=self.styles["Heading2"],
                fontSize=10,
                fontName="Helvetica-Bold",
                textColor=NAVY,
                spaceAfter=4
            )
            # Conditional footer — wording depends on whether agreement is signed
            _is_signed = job_status not in ("LEAD_CAPTURED",)
            if _is_signed:
                auth_heading = "EXECUTED CONTINGENCY & CLAIM AUTHORIZATION"
                legal_attestation = (
                    "This Evidence Grid constitutes a technical appendix to the official homeowner inspection report. "
                    "The property owner has executed a Contingency Agreement authorizing Wickham Roofing LLC as their designated "
                    "contractor to perform forensic inspections, document physical loss, and present verified evidence to insurance carriers."
                )
            else:
                auth_heading = "PRE-INSPECTION EVIDENCE PACK — CONTINGENCY AGREEMENT NOT YET SIGNED"
                legal_attestation = (
                    "This Evidence Pack is a pre-inspection storm history and photo documentation record assembled for "
                    "presentation to the property owner. The Contingency Agreement has NOT yet been signed. "
                    "This document is for informational purposes only and does not constitute an executed contractor authorization."
                )
            story.append(Paragraph(auth_heading, auth_title_style))
            
            auth_content: list[Any] = [Paragraph(legal_attestation, body_style)]
            
            if _is_signed and signature_path and Path(signature_path).exists():
                try:
                    sig_img = Image(str(signature_path), width=220, height=55, kind='proportional')
                    auth_content.append(Spacer(1, 4))
                    auth_content.append(sig_img)
                    auth_content.append(Paragraph(f"<b>Digitally Signed:</b> {homeowner} | <b>Date:</b> {inspection_date}", body_style))
                except Exception as sig_err:
                    log.error("signature_render_failed", error=str(sig_err))

            auth_table = Table([[auth_content]], colWidths=[520])
            auth_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
                ('BOX', (0,0), (-1,-1), 1, BORDER_GREY),
                ('PADDING', (0,0), (-1,-1), 8),
            ]))
            story.append(auth_table)

            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("evidence_grid_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("evidence_grid_generation_failed", error=str(exc))
            raise


    async def generate_inspection_letter(self, job: dict, ev_data: dict, inspection_summary: dict) -> str:
        """Generate a formal inspection letter combining measurements and photo evidence."""
        job_id = job.get("id", "UNKNOWN")
        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "inspection_letter.pdf")
        
        def build_pdf() -> None:
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            doc = self._get_doc_template(filepath, top_margin=120, job_id=job_id, doc_type="INSPECTION_LETTER")
            story: list[Any] = []
            
            story.append(Paragraph("FORMAL ROOF INSPECTION REPORT", self.custom_styles["Title"]))
            story.append(Spacer(1, 20))
            
            # Metadata with new inspector fields
            address = f"{job.get('address_line1', '')}, {job.get('city', '')}, {job.get('state', '')} {job.get('postal_code', '')}"
            meta_data = [
                ["Job ID:", job.get("id", "N/A")],
                ["Homeowner:", job.get("homeowner_name", "N/A")],
                ["Property Address:", address],
                ["Inspector:", job.get("inspector_name") or "Wickham Roofing Field Inspector"],
                ["Inspection Date:", job.get("inspection_date") or "TBD"]
            ]
            
            t = Table(meta_data, colWidths=[120, 380])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (0,-1), colors.lightgrey),
                ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                ('PADDING', (0,0), (-1,-1), 6),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ]))
            story.append(t)
            story.append(Spacer(1, 20))
            
            story.append(Paragraph("Measurement Summary", self.custom_styles["SectionHeading"]))
            total_sf = ev_data.get("total_area_sf", 0)
            sq = f"{total_sf / 100.0:.1f}" if isinstance(total_sf, (int, float)) and total_sf > 0 else "N/A"
            ridge = ev_data.get("ridge_lf", "N/A")
            valleys = ev_data.get("valley_lf", "N/A")
            eaves = ev_data.get("eaves_lf", "N/A")
            
            meas_data = [
                ["Measurement Type", "Value"],
                ["Total Squares", f"{sq} SQ"],
                ["Ridges", f"{ridge} LF"],
                ["Valleys", f"{valleys} LF"],
                ["Eaves (Drip Edge)", f"{eaves} LF"]
            ]
            meas_table = Table(meas_data, colWidths=[250, 150])
            meas_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
                ('PADDING', (0,0), (-1,-1), 6)
            ]))
            story.append(meas_table)
            story.append(Spacer(1, 15))
            
            story.append(Paragraph("Photo Evidence Summary", self.custom_styles["SectionHeading"]))
            damage_count = inspection_summary.get("damage_count", 0)
            predominant = inspection_summary.get("predominant_damage_type", "None detected")
            severity = inspection_summary.get("severity", "Unknown")
            
            photo_data = [
                ["Metric", "Assessment"],
                ["Detected Damage Count", str(damage_count)],
                ["Predominant Damage Type", str(predominant)],
                ["Overall Severity", str(severity)]
            ]
            photo_table = Table(photo_data, colWidths=[250, 150])
            photo_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
                ('PADDING', (0,0), (-1,-1), 6)
            ]))
            story.append(photo_table)
            story.append(Spacer(1, 15))
            
            if "notes" in inspection_summary:
                story.append(Paragraph(f"Notes: {inspection_summary['notes']}", self.custom_styles["BodyText"]))
                story.append(Spacer(1, 15))
            
            if job.get("inspection_notes"):
                story.append(Paragraph("Inspector Notes", self.custom_styles["SectionHeading"]))
                story.append(Paragraph(job["inspection_notes"], self.custom_styles["BodyText"]))
                story.append(Spacer(1, 15))
            
            legal_text = (
                "This report constitutes a preliminary assessment of apparent roof conditions on the date of inspection. "
                "It does not serve as an engineering report, nor does it guarantee insurance coverage."
            )
            story.append(self._box_warning("Disclaimer", legal_text, colors.darkred))
            story.append(Spacer(1, 20))
            
            story.append(self._build_signature_block(title1="Inspector Signature", title2="Homeowner Acknowledgment"))
            
            doc.build(story)
            
        await asyncio.to_thread(build_pdf)
        return filepath


    async def generate_rebuttal_letter(
        self,
        job: dict,
        denial_text: str,
        rebuttal_narrative: str
    ) -> str:
        """
        Generate a formal Rebuttal Letter PDF.
        Returns the permanent vault path.
        """
        job_id = job.get("id", "UNKNOWN")
        log = logger.bind(job_id=job_id)
        log.info("rebuttal_pdf_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "Rebuttal_Letter.pdf")

        def build_pdf() -> None:
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            import html as _html
            doc = self._get_doc_template(filepath,
                                         job_id=job_id)
            story: list[Any] = []

            story.append(Paragraph(
                "SUPPLEMENT REBUTTAL LETTER",
                self.custom_styles["Title"]
            ))
            story.append(Spacer(1, 12))
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 16))

            story.append(Paragraph(
                "<b>CARRIER DENIAL SUMMARY (VERBATIM):</b>",
                self.custom_styles["SectionHeading"]
            ))
            denial_style = ParagraphStyle(
                "DenialQuote",
                parent=self.styles["Normal"],
                fontSize=9,
                leftIndent=20,
                rightIndent=20,
                textColor=colors.darkred,
                backColor=colors.lightyellow,
                borderPad=6,
            )
            for line in denial_text.split("\n"):
                if line.strip():
                    story.append(Paragraph(
                        _html.escape(line.strip()),
                        denial_style
                    ))
            story.append(Spacer(1, 16))

            story.append(Paragraph(
                "<b>CONTRACTOR REBUTTAL:</b>",
                self.custom_styles["SectionHeading"]
            ))
            story.append(HRFlowable(
                width="100%", thickness=0.5,
                color=colors.black, spaceAfter=10
            ))
            narrative_style = ParagraphStyle(
                "RebuttalBody",
                parent=self.styles["Normal"],
                fontSize=10,
                leading=14,
                spaceBefore=4,
                spaceAfter=4,
            )
            for para in rebuttal_narrative.split("\n"):
                if para.strip():
                    story.append(Paragraph(
                        _html.escape(para.strip()),
                        narrative_style
                    ))

            story.append(Spacer(1, 30))
            story.append(self._build_signature_block(
                title1="Authorized Contractor Representative",
                title2="Date"
            ))
            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("rebuttal_pdf_generation_complete",
                     filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("rebuttal_pdf_generation_failed",
                      error=str(exc))
            raise


    async def generate_escalation_letter(
        self,
        job: dict,
        days_elapsed: int,
        narrative: str,
    ) -> str:
        """
        Generate a formal Second Request / Notice of Intent to Appraise PDF.

        Args:
            job: Full job record dict.
            days_elapsed: Number of days since supplement was submitted.
            narrative: AI-generated letter body text.

        Returns:
            str: Absolute path to the generated PDF file.
        """
        job_id = job.get("id", "UNKNOWN")
        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "Escalation_Demand_Letter.pdf")

        def build_pdf() -> None:
            """
            Build Pdf functionality.
            
            Returns:
                Any: The resulting output.
            """
            doc = self._get_doc_template(filepath, job_id=job_id, doc_type="ESCALATION_LETTER")
            story: list[Any] = []

            story.append(
                Paragraph(
                    "SECOND REQUEST \u2014 NOTICE OF INTENT TO APPRAISE",
                    self.custom_styles["Title"],
                )
            )
            story.append(Spacer(1, 8))
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 16))

            # Red SLA warning banner
            warning_table = Table(
                [[
                    f"\u26a0  {days_elapsed} DAYS WITHOUT CARRIER RESPONSE "
                    f"\u2014 APPRAISAL PENDING"
                ]],
                colWidths=[450],
            )
            warning_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.darkred),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 13),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ])
            )
            story.append(warning_table)
            story.append(Spacer(1, 12))

            story.append(
                Paragraph("VIA ELECTRONIC SUBMISSION", self.custom_styles["BodyText"])
            )
            story.append(Spacer(1, 12))

            for paragraph in narrative.split("\n\n"):
                if paragraph.strip():
                    story.append(
                        Paragraph(paragraph.strip(), self.custom_styles["BodyText"])
                    )
                    story.append(Spacer(1, 10))

            story.append(Spacer(1, 24))
            story.append(
                self._build_signature_block(
                    title1="Authorized Representative \u2014 Wickham Roofing",
                    title2="Date",
                )
            )
            doc.build(story)

        await asyncio.to_thread(build_pdf)
        return filepath

    async def generate_supplement_pdf(self, report: DiscrepancyReport, narrative: str, job: dict, db_context: dict) -> str:
        """
        Generate an official Insurance Supplement Request PDF including discrepancy breakdown, building code citations, AI narrative, and carrier SLA notice.
        Returns the absolute filepath to the temporary PDF.
        """
        job_id = job.get("id", "UNKNOWN")
        log = logger.bind(job_id=job_id)
        log.info("supplement_pdf_generation_started")

        job_dir = FIELD_DOCS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(job_dir / "Supplement_Request.pdf")

        def build_pdf() -> None:
            doc = self._get_doc_template(filepath, top_margin=120, job_id=job_id, doc_type="SUPPLEMENT")
            story: list[Any] = []
            
            # Styles
            normal_style = self.custom_styles["BodyText"]
            section_style = self.custom_styles["SectionHeading"]
            narrative_style = ParagraphStyle(
                name="SupplementNarrative",
                parent=normal_style,
                fontSize=9.5,
                leading=13.5,
                spaceBefore=4,
                spaceAfter=4,
            )
            legal_style = ParagraphStyle(
                name="LegalDisclaimer",
                parent=normal_style,
                fontSize=8,
                leading=10,
                textColor=colors.HexColor("#4b5563"),
            )
            
            # --- 1. Title & Header ---
            story.append(Paragraph("OFFICIAL INSURANCE SUPPLEMENT REQUEST", self.custom_styles["Title"]))
            story.append(Paragraph("TECHNICAL LINE-ITEM & BUILDING CODE DISCREPANCY REPORT", ParagraphStyle("SubTitle", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica-Bold", alignment=1, textColor=colors.HexColor("#1e3a8a"))))
            story.append(Spacer(1, 14))
            
            # --- 2. Metadata ---
            story.append(self._build_metadata_table(job))
            story.append(Spacer(1, 12))
            
            # --- 3. Executive Summary ---
            story.append(Paragraph("Executive Supplement Summary", section_style))
            exec_summary_text = (
                "This document presents an official insurance supplement request submitted by Wickham Roofing LLC on behalf of the insured property owner. "
                "The itemized breakdown below outlines quantity, structural, and International Residential Code (IRC) variances between the carrier's initial estimate "
                "and the actual scope of work required to perform a complete, code-compliant restoration to pre-loss condition."
            )
            story.append(Paragraph(exec_summary_text, normal_style))
            story.append(Spacer(1, 12))
            
            # --- 4. Discrepancy Table ---
            story.append(Paragraph("Summary of Mathematical & Quantity Variances", section_style))
            story.append(Spacer(1, 4))
            
            table_data = [["Category / Item", "EV Measurement", "Carrier Est. (SoL)", "Variance", "Xactimate Code"]]
            for d in report.discrepancies:
                ev_str = f"{d.ev_value:.2f}" if isinstance(d.ev_value, (int, float)) else str(d.ev_value) if d.ev_value is not None else "N/A"
                sol_str = f"{d.sol_value:.2f}" if isinstance(d.sol_value, (int, float)) else str(d.sol_value) if d.sol_value is not None else "N/A"
                var_str = f"{d.variance:.2f}" if isinstance(d.variance, (int, float)) else str(d.variance) if d.variance is not None else "N/A"
                table_data.append([
                    d.category,
                    ev_str,
                    sol_str,
                    var_str,
                    d.xactimate_code if d.xactimate_code else "N/A",
                ])
                
            if len(table_data) > 1:
                t = Table(table_data, colWidths=[140, 90, 100, 80, 90])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1e3a8a")),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                    ('ALIGN', (0,0), (0,-1), 'LEFT'),
                    ('ALIGN', (1,0), (-2,-1), 'RIGHT'),
                    ('ALIGN', (-1,0), (-1,-1), 'CENTER'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0,0), (-1,0), 9),
                    ('BOTTOMPADDING', (0,0), (-1,0), 7),
                    ('TOPPADDING', (0,0), (-1,0), 7),
                    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#f8fafc"), colors.white]),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
                    ('PADDING', (0,0), (-1,-1), 5),
                ]))
                story.append(t)
            else:
                story.append(Paragraph("No line-item variances recorded.", normal_style))
            story.append(Spacer(1, 14))
            
            # --- 5. Itemized Supplemental Scope & Valuation Schedule ---
            story.append(Paragraph("Itemized Supplemental Scope & Valuation Schedule", section_style))
            story.append(Spacer(1, 4))
            
            try:
                from app.core.database import get_pricing_ledger
                pricing_ledger = get_pricing_ledger()
                CODE_PRICING_MAP = {
                    "RFG STEEP": ("rfg_steep_per_sq", "SQ", "Steep Slope Safety Protocol"),
                    "RFG RIDGC+": ("rfg_ridgc_plus_per_lf", "LF", "High-Profile Ridge Cap Upgrade"),
                    "SFG GUTA": ("sfg_guta_per_lf", "LF", "Seamless Aluminum Gutter Replacement"),
                    "DMO DUMP": ("dmo_dump_per_container", "EA", "Debris Disposal Container Fee"),
                    "RFG RENAIL": ("rfg_renail_per_sq", "SQ", "Roof Decking Re-Nailing (IRC R905.2.1)"),
                    "RFG 300S": ("rfg_waste_adjustment_per_sq", "SQ", "Complex Geometry Waste Adjustment"),
                    "RFG START": ("starter_bundles", "BDL", "Starter Strip Shingles"),
                    "RFG DRIP": ("drip_edge_pieces_10ft", "PC", "Drip Edge Metal Flashing"),
                    "RFG IWS": ("ice_and_water_rolls", "RL", "Ice & Water Shield Membrane"),
                    "DMO PU": ("dmo_dump_per_container", "EA", "Debris Pickup & Haul-Off"),
                }
                
                table_cell_style = ParagraphStyle(
                    "SuppTableCell",
                    parent=self.styles["Normal"],
                    fontSize=8.5,
                    leading=11,
                    textColor=colors.HexColor("#1e293b")
                )
                
                rules = db_context.get("rules", [])
                supp_table_data: list[list[Any]] = [["Item Description", "Code", "Quantity", "Unit Rate", "Total Amount"]]
                total_supp_val = 0.0
                
                for r in rules:
                    code = r.get("required_child_code", "")
                    if code in CODE_PRICING_MAP:
                        pkey, unit_label, default_desc = CODE_PRICING_MAP[code]
                        unit_rate = pricing_ledger.get(pkey, 0.0)
                        qty = float(r.get("quantity_delta") or 1.0)
                        line_amt = qty * unit_rate
                        total_supp_val += line_amt
                        supp_table_data.append([
                            Paragraph(default_desc, table_cell_style),
                            code,
                            f"{qty:.2f} {unit_label}",
                            f"${unit_rate:.2f}",
                            f"${line_amt:,.2f}"
                        ])
                
                if len(supp_table_data) > 1:
                    supp_table_data.append(["Total Supplemental Valuation", "", "", "", f"${total_supp_val:,.2f}"])
                    st_table = Table(supp_table_data, colWidths=[180, 75, 75, 75, 95])
                    st_table.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1e3a8a")),
                        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                        ('ALIGN', (0,0), (1,-1), 'LEFT'),
                        ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
                        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0,0), (-1,-1), 8.5),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
                        ('TOPPADDING', (0,0), (-1,-1), 5),
                        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.HexColor("#f8fafc"), colors.white]),
                        ('LINEABOVE', (0,-1), (-1,-1), 1, colors.HexColor("#1e3a8a")),
                        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
                        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
                        ('PADDING', (0,0), (-1,-1), 5),
                    ]))
                    story.append(st_table)
                else:
                    story.append(Paragraph("No itemized supplemental line items.", normal_style))
            except Exception as e:
                log.error("supp_pricing_table_build_failed", error=str(e))
            story.append(Spacer(1, 14))
            
            # --- 6. Building Code & Manufacturer Specifications ---
            story.append(Paragraph("Building Code & Manufacturer Mandates", section_style))
            story.append(Spacer(1, 4))
            
            try:
                ice_barrier_required = db_context.get("ice_barrier_required", False)
                jurisdiction = db_context.get("jurisdiction_code_version", "2021_IRC")
                rules = db_context.get("rules", [])
                
                for r in rules:
                    ctype = r["citation_type"]
                    ctext = r["citation_text"]
                    climate_dependent = bool(r["climate_dependent"])
                    
                    if climate_dependent and not ice_barrier_required:
                        continue
                    
                    if ctype == "IRC":
                        framed = f"<b>Pursuant to {jurisdiction.replace('_', ' ')}:</b> {ctext}"
                    elif ctype == "MFG_SPEC":
                        framed = f"<b>Per Manufacturer Installation Warranty Requirements:</b> {ctext}"
                    else:
                        framed = f"<b>Policy Standard:</b> {ctext}"
                    story.append(Paragraph(f"&bull; {framed}", narrative_style))
                
                weather = db_context.get("weather")
                if weather:
                    story.append(Spacer(1, 4))
                    story.append(Paragraph(f"<b>NOAA Weather Event Verification:</b> {weather['magnitude']} in {weather['event_type']} documented on {weather['loss_date'][:10]}", legal_style))
                    
            except Exception as e:
                log.error("pdf_db_context_read_failed", error=str(e))

            story.append(Spacer(1, 8))
            
            # --- 6. Technical AI Narrative ---
            story.append(Paragraph("Technical Justification Narrative", section_style))
            for p in narrative.split("\n"):
                if p.strip():
                    story.append(Paragraph(html.escape(p.strip()), narrative_style))
            story.append(Spacer(1, 14))
            
            # --- 7. SLA Warning & 1-Year Workmanship Warranty ---
            sla_warranty_text = (
                "<b>14-DAY CARRIER RESPONSE NOTICE:</b> Prompt processing of this supplemental request is required under Georgia Insurance Regulations. "
                "Failure to approve essential structural building code components exposes the property to secondary moisture intrusion.<br/><br/>"
                "<b>WORKMANSHIP GUARANTEE:</b> All supplemental scope items executed by Wickham Roofing LLC are backed by our explicit "
                "<b>1-Year Workmanship Warranty</b> upon final project completion."
            )
            story.append(self._box_warning("CARRIER NOTICE & WORKMANSHIP WARRANTY", sla_warranty_text, colors.HexColor("#1e3a8a")))
            story.append(Spacer(1, 18))
            
            # --- 8. Signature ---
            story.append(self._build_signature_block(title1="Authorized Contractor Representative — Wickham Roofing LLC", title2="Date"))
            
            doc.build(story)

        try:
            await asyncio.to_thread(build_pdf)
            log.info("supplement_pdf_generation_complete", filepath=filepath)
            return filepath
        except Exception as exc:
            log.error("supplement_pdf_generation_failed", error=str(exc))
            raise


