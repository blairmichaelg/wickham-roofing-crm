"""
Google Gemini AI service wrapper.

Wraps the google-genai SDK to:
- Accept translated (human-readable) job data as context
- Apply strict prompt templates for specific cognitive tasks
- Return structured JSON decisions
- Handle API errors and rate limits gracefully

V3 additions:
- _call_with_backoff: Exponential backoff for free-tier rate limiting (429)
- analyze_roof_photo: Multimodal damage detection with flat PhotoAnalysis schema

SDK Migration: Moved from deprecated google-generativeai to google-genai.
The new SDK uses a Client() pattern with client.models.generate_content().
"""

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Literal, Any

import structlog
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.core.database import log_ai_usage
from app.core.inspection_models import PhotoAnalysis
from app.core.supplement_models import DiscrepancyReport, StatementOfLoss

logger = structlog.get_logger("app.services.ai_service")


class DocumentData(BaseModel):
    """DocumentData definition."""
    materials: list[str] = []
    total_cost: float = 0.0


class Decision(BaseModel):
    """Decision definition."""
    action: Literal["generate_document", "update_status", "ignore", "error"]
    reasoning: str
    document_data: DocumentData


class BatchPhotoAnalysis(BaseModel):
    """Container for batch analysis of roof photos."""
    analyses: list[PhotoAnalysis]


from abc import ABC, abstractmethod


class AiClient(ABC):
    @abstractmethod
    async def analyze_job_data(self, payload: dict) -> dict: ...
    
    @abstractmethod
    async def classify_carrier(self, file_name_or_part: str | genai_types.Part, job_id: str | None = None) -> str: ...

    @abstractmethod
    async def extract_sol_from_pdf(self, pdf_path: str | Path, job_id: str | None = None) -> StatementOfLoss: ...

    @abstractmethod
    async def generate_supplement_narrative(self, report: DiscrepancyReport, codes: str) -> str: ...
    
    @abstractmethod
    async def analyze_roof_photo(self, file_path: str | Path, original_filename: str | None = None, job_id: str | None = None) -> PhotoAnalysis: ...
    
    @abstractmethod
    async def analyze_roof_photos_batch(self, file_paths: list[str | Path], original_filenames: list[str] | None = None, job_id: str | None = None) -> list[PhotoAnalysis]: ...

    @abstractmethod
    async def generate_text(self, system_prompt: str, user_prompt: str, job_id: str | None = None, operation_type: str = "generate_text") -> str: ...

    @abstractmethod
    async def extract_sol_structured_data(self, prompt: str) -> str: ...

    @abstractmethod
    async def upload_media_file(self, file_path: str) -> str: ...
    
    @abstractmethod
    async def get_file_status(self, file_name: str) -> str: ...
    
    @abstractmethod
    async def delete_file(self, file_name: str) -> None: ...

class GeminiClient(AiClient):
    """
    Gemini AI integration for cognitive processing of CRM data.

    Uses the google-genai unified SDK with:
    - Strict JSON output via response_mime_type
    - Low temperature for deterministic responses
    - Pydantic schema enforcement on AI output
    """
    async def extract_sol_structured_data(self, prompt: str) -> str:
        response = await asyncio.to_thread(
            self._call_with_backoff,
            self.client.models.generate_content,
            model=self.model_name,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            )
        )
        return response.text



    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = genai.Client(api_key=self.settings.gemini_api_key)
        self.model_name = "gemini-2.5-flash"
        logger.info("ai_service_initialized", model=self.model_name)

    def _get_file_size_and_bytes(self, file_path: str | Path) -> tuple[int, bytes]:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        data = p.read_bytes()
        return len(data), data

    async def upload_media_file(self, file_path: str) -> str:
        uploaded_file = await asyncio.to_thread(self._call_with_backoff, self.client.files.upload, file=file_path)
        return uploaded_file.name

    async def get_file_status(self, file_name: str) -> str:
        file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=file_name)
        return file_info.state.name

    async def delete_file(self, file_name: str) -> None:
        try:
            await asyncio.to_thread(self._call_with_backoff, self.client.files.delete, name=file_name)
        except Exception as e:
            logger.warning("gemini_file_cleanup_failed", file_name=file_name, error=str(e))


    def _call_with_backoff(self, func, *args, max_retries: int = 5, **kwargs):
        """
        Rate-limit-aware wrapper for Gemini API calls.

        Catches 429 RESOURCE_EXHAUSTED errors and retries with exponential
        backoff + jitter. Essential for free-tier quota protection when
        processing 40+ roof photos sequentially.

        Args:
            func: The callable (e.g., self.client.models.generate_content).
            *args: Positional args forwarded to func.
            max_retries: Maximum retry attempts before raising. Default 5.
            **kwargs: Keyword args forwarded to func.

        Returns:
            The return value of func(*args, **kwargs).

        Raises:
            RuntimeError: If all retries are exhausted.
            Exception: Any non-rate-limit error is re-raised immediately.
        """
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e).lower()
                # Trap 429 Rate Limits and 5xx Transient Network Errors
                is_transient = any(
                    err in error_str
                    for err in ["429", "resource_exhausted", "503", "504", "deadlineexceeded", "timeouterror"]
                )
                if is_transient:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "transient_error_backoff",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        wait_seconds=round(wait, 2),
                        error_snippet=error_str[:50]
                    )
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(
            f"Gemini API transient error exceeded after {max_retries} retries."
        )

    async def analyze_job_data(self, payload: dict) -> dict:
        """
        Analyze the translated CRM payload using Gemini and return a structured decision.
        """
        log = logger.bind(jnid=payload.get("id"))
        log.info("ai_analysis_started")

        prompt = f"""
You are an expert roofing estimator and workflow orchestrator for Wickham Roofing.
Analyze the following CRM job data and determine the next action.

CRM Data:
{json.dumps(payload, indent=2)}

You MUST output a valid JSON object matching exactly this schema:
{{
  "action": "generate_document" | "update_status" | "ignore",
  "reasoning": "A brief explanation of why you chose this action.",
  "document_data": {{
    "materials": ["Item 1", "Item 2"],
    "total_cost": 0.0
  }}
}}

Rules:
- If there is enough information to generate an estimate (e.g., measurements, scope of work in notes), set action to "generate_document" and populate document_data.
- If the data is incomplete or requires review, set action to "update_status".
- Otherwise, set action to "ignore".
"""

        try:
            # Run the synchronous API call in an executor to avoid blocking the event loop
            response = await asyncio.to_thread(
                self._call_with_backoff,
                self.client.models.generate_content,
                model=self.model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )

            result_text = response.text
            decision_obj = Decision.model_validate_json(result_text)
            decision = decision_obj.model_dump()

            usage = getattr(response.usage_metadata, "total_token_count", 0)
            if usage > 0:
                await asyncio.to_thread(log_ai_usage, payload.get("id"), usage, self.model_name, "analyze_job_data")

            log.info(
                "ai_analysis_complete",
                action=decision.get("action"),
                reasoning=decision.get("reasoning"),
            )
            return decision

        except ValidationError as exc:
            log.error(
                "ai_schema_validation_error",
                error=str(exc),
                response_text=response.text if "response" in locals() else None,
            )
            return {
                "action": "error",
                "reasoning": f"Schema Validation Error: {exc!s}",
                "document_data": {},
            }
        except Exception as exc:
            log.error("ai_unexpected_error", error=str(exc))
            return {
                "action": "error",
                "reasoning": f"Unexpected error: {exc!s}",
                "document_data": {},
            }

    async def classify_carrier(self, file_name_or_part: str | genai_types.Part, job_id: str | None = None) -> str:
        """
        Classify the carrier estimating software from the PDF.
        """
        prompt = (
            "Analyze the first page or headers of this PDF and identify the estimating software used. "
            "Return ONLY a single string: 'xactimate', 'symbility', or 'unknown'."
        )
        
        if isinstance(file_name_or_part, str):
            file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=file_name_or_part)
            content_part = file_info
        else:
            content_part = file_name_or_part
        
        response = await asyncio.to_thread(
            self._call_with_backoff,
            self.client.models.generate_content,
            model=self.model_name,
            contents=[content_part, prompt],
            config=genai_types.GenerateContentConfig(
                response_mime_type="text/plain",
                temperature=0.0,
            ),
        )
        result = response.text.strip().lower()
        usage = getattr(response.usage_metadata, "total_token_count", 0)
        if usage > 0:
            log_ai_usage(job_id, usage, self.model_name, "classify_carrier")
        if result in ("xactimate", "symbility"):
            return result
        return "unknown"

    def _get_sol_prompt(self, source_system: str) -> str:
        if source_system == "xactimate":
            return """
            You are an expert Xactimate estimator. Analyze this Statement of Loss (SoL) document.
            Extract ONLY the line items located under the "Roof" grouping (ignore any other rooms, general demolition, or recap tables).
            Pay special attention to descriptions that wrap across multiple lines (e.g., "Remove 3 tab 25 yr. composition shingle roofing - incl. felt").
            If a quantity, unit of measure, or price is blank or missing, you MUST return null, not guess or hallucinate.
            DO NOT infer or calculate quantities. Extract the exact numerical value printed in the quantity column.
            If Overhead and Profit (O&P) is not explicitly listed in the summaries, set overhead_and_profit_included to false.
            
            Also extract:
            - claim_number and carrier_name.
            - For each line item: trade, code, description, quantity, unit_of_measure, unit_price, tax, claimed_rcv, depreciation, acv, page.
            - Roof geometry: pitch, total_squares, eaves_lf, valleys_lf, rakes_lf.
            - Shingle details: shingle_type (e.g. "3-tab", "architectural", "laminated", "wood", etc.) and shingle_color (e.g. "Charcoal", "Weathered Wood", "Slate", etc.) if mentioned in the line items, material specifications, or document notes.
            - Claim financials: gross_rcv, total_depreciation, deductible, net_claim.
            """
        elif source_system == "symbility":
            return """
            You are an expert Symbility estimator. Analyze this Statement of Loss (SoL) document.
            Extract ONLY the line items located under the "Roof" grouping.
            Symbility formats line items differently. Explicitly look for phrases like "Includes 10% waste on quantity" in the item notes.
            If you find a waste percentage in the notes, map that float (e.g., 0.10) to the waste_percent_included field.
            DO NOT infer or calculate quantities. Extract the exact numerical value printed in the quantity column.
            If a quantity, unit of measure, or price is blank or missing, you MUST return null.
            
            Also extract:
            - claim_number and carrier_name.
            - For each line item: trade, code, description, quantity, unit_of_measure, unit_price, tax, claimed_rcv, depreciation, acv, page.
            - Roof geometry: pitch, total_squares, eaves_lf, valleys_lf, rakes_lf.
            - Shingle details: shingle_type (e.g. "3-tab", "architectural", "laminated", "wood", etc.) and shingle_color (e.g. "Charcoal", "Weathered Wood", "Slate", etc.) if mentioned in the line items, material specifications, or document notes.
            - Claim financials: gross_rcv, total_depreciation, deductible, net_claim.
            """
        else:
            return """
            Analyze this roofing Statement of Loss document.
            Extract ONLY the line items related to roof replacement.
            DO NOT infer or calculate quantities. Extract the exact numerical value printed in the quantity column.
            If a quantity, unit of measure, or price is blank or missing, you MUST return null.
            
            Also extract:
            - claim_number and carrier_name.
            - For each line item: trade, code, description, quantity, unit_of_measure, unit_price, tax, claimed_rcv, depreciation, acv, page.
            - Roof geometry: pitch, total_squares, eaves_lf, valleys_lf, rakes_lf.
            - Shingle details: shingle_type (e.g. "3-tab", "architectural", "laminated", "wood", etc.) and shingle_color (e.g. "Charcoal", "Weathered Wood", "Slate", etc.) if mentioned in the line items, material specifications, or document notes.
            - Claim financials: gross_rcv, total_depreciation, deductible, net_claim.
            """

    async def extract_sol_from_pdf(self, pdf_path: str | Path, job_id: str | None = None) -> StatementOfLoss:
        """
        Multimodal extraction of a Statement of Loss PDF.
        Supports both inline data (for files < 15MB) and Gemini File API (fallback for >= 15MB).
        Enforces structured extraction using the StatementOfLoss Pydantic schema.
        """
        log = logger.bind(pdf_path=str(pdf_path))
        log.info("sol_extraction_started")

        p_path = Path(pdf_path)
        is_local = p_path.exists()
        file_size = 0
        file_bytes = b""
        
        if is_local:
            try:
                file_size, file_bytes = await asyncio.to_thread(self._get_file_size_and_bytes, p_path)
            except Exception as exc:
                log.error("sol_file_not_readable", error=str(exc))
                raise

        if is_local and file_size < 15728640:
            log.info("sol_extraction_inline_mode", size_bytes=file_size)
            part = genai_types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")
            try:
                source_system = await self.classify_carrier(part, job_id)
                prompt = self._get_sol_prompt(source_system)

                response = await asyncio.to_thread(
                    self._call_with_backoff,
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=[part, prompt],
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=StatementOfLoss,
                        temperature=0.1,
                        max_output_tokens=8192,
                    ),
                )
                
                parsed = response.parsed
                parsed.source_system = source_system

                usage = getattr(response.usage_metadata, "total_token_count", 0)
                if usage > 0:
                    await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "sol_extraction")

                log.info("sol_extraction_complete")
                return parsed

            except Exception as exc:
                log.error("sol_extraction_failed", error=str(exc))
                raise
        else:
            log.info("sol_extraction_files_api_mode", size_bytes=file_size)
            uploaded_file = await asyncio.to_thread(self._call_with_backoff, self.client.files.upload, file=str(pdf_path))
            try:
                file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=uploaded_file.name)
                max_wait = 60
                elapsed = 0
                while file_info.state.name == "PROCESSING":
                    if elapsed >= max_wait:
                        raise TimeoutError("Gemini file processing timed out.")
                    await asyncio.sleep(2)
                    elapsed += 2
                    file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=uploaded_file.name)
                
                if file_info.state.name == "FAILED":
                    raise RuntimeError("File processing failed on Gemini servers.")

                source_system = await self.classify_carrier(uploaded_file.name, job_id)
                prompt = self._get_sol_prompt(source_system)

                response = await asyncio.to_thread(
                    self._call_with_backoff,
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=[file_info, prompt],
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=StatementOfLoss,
                        temperature=0.1,
                        max_output_tokens=8192,
                    ),
                )
                
                parsed = response.parsed
                parsed.source_system = source_system

                usage = getattr(response.usage_metadata, "total_token_count", 0)
                if usage > 0:
                    await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "sol_extraction")

                log.info("sol_extraction_complete")
                return parsed

            except Exception as exc:
                log.error("sol_extraction_failed", error=str(exc))
                raise
            finally:
                try:
                    await asyncio.to_thread(self.client.files.delete, name=uploaded_file.name)
                except Exception as exc:
                    log.warning("sol_file_cleanup_failed", error=str(exc))

    async def generate_supplement_narrative(self, report: DiscrepancyReport, codes: str) -> str:
        """
        Generate a professional, assertive supplement request narrative.
        Uses the deterministic discrepancies and raw XML building codes as context.
        """
        log = logger.bind(job_id=report.job_id)
        log.info("supplement_narrative_started")

        prompt = f"""
        You are an expert, assertive roofing contractor writing a "Defensive Summary" justification for an insurance desk adjuster.
        
        You have analyzed the EagleView measurement report and the Carrier's Statement of Loss and found the following numerical shortages.
        You MUST explicitly state the mathematical shortages found in the report below.
        You MUST reference the specific Xactimate codes (e.g. RFG 300S, RFG IWS, FEE O&P) associated with the discrepancies so the adjuster can easily input them.
        Only cite the building codes provided below if they directly relate to the identified discrepancies.
        You MUST use the exact `code_citation` string provided as a bolded header before quoting the building code. Do not hallucinate or alter the citation.
        
        --- DISCREPANCY REPORT ---
        {report.model_dump_json(indent=2)}
        
        --- BUILDING CODES ---
        {codes}
        
        Write a concise, 2-paragraph Defensive Summary designed to definitively prove the shortages and remove friction for the adjuster to approve the Xactimate line items. Do not use placeholders for the company name, just use "Wickham Roofing LLC". Do not include a date or address block at the top, just jump straight into the narrative.
        """

        try:
            response = await asyncio.to_thread(
                self._call_with_backoff,
                self.client.models.generate_content,
                model=self.model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.3,
                ),
            )
            
            usage = getattr(response.usage_metadata, "total_token_count", 0)
            if usage > 0:
                await asyncio.to_thread(log_ai_usage, report.job_id, usage, self.model_name, "generate_supplement_narrative")
            
            log.info("supplement_narrative_complete")
            return response.text
        except Exception as exc:
            log.warning("supplement_narrative_fallback_used", error=str(exc))
            # Deterministic Defensive Summary fallback
            lines = [
                "<b>Wickham Roofing LLC - Supplement Justification & Defensive Summary</b>",
                "<br/>Upon detailed engineering audit of the carrier's Statement of Loss against third-party measurement reports and local building codes, several mandatory structural and quantity discrepancies were identified.",
                "To ensure the roof restoration meets IRC manufacturer installation specifications and local building code compliance, the line items enumerated in the attached discrepancy schedule are required."
            ]
            if report.discrepancies:
                lines.append("<br/><b>Identified Line-Item Discrepancies:</b>")
                for d in report.discrepancies:
                    lines.append(f"• <b>{d.xactimate_code or 'RFG'} ({d.category}):</b> {d.description}")
            return "<br/>".join(lines)

    async def analyze_roof_photo(self, file_path: Any, original_filename: str | None = None, job_id: str | None = None) -> PhotoAnalysis:
        """
        Multimodal damage analysis of a single roof photo.
        Supports:
          1. Local file path (Path or str): uses inline mode (if < 15MB) or Files API (if >= 15MB).
          2. Remote file name (str starting with "files/"): fetches and uses Files API directly.
          3. Pre-fetched file object (e.g. MagicMock or genai_types.File): uses it directly in contents.
        """
        log = logger.bind(job_id=job_id)
        
        is_local = False
        is_remote_name = False
        file_info = None
        
        if isinstance(file_path, (str, Path)):
            p_path = Path(file_path)
            if str(file_path).startswith("files/") or not p_path.exists():
                is_remote_name = True
            else:
                is_local = True
        else:
            file_info = file_path

        orig_name = original_filename or (Path(file_path).name if is_local or is_remote_name else "photo.jpg")

        prompt = (
            f"You are Wickham Roofing's senior forensic roofing inspector creating photographic documentation for an inspection report. "
            f"Examine this photo (File: {orig_name}) carefully.\n\n"
            f"Zero-Shot Chain of Thought analysis:\n"
            f"Let's think step by step.\n"
            f"1. First, describe the texture and color of the anomalies in the photo. Identify if it shows a roof slope, shingle close-up, pipe vent boot, valley, etc.\n"
            f"2. Second, compare those anomalies against standard hail impact signatures (e.g., circular bruising, exposed fiberglass) or wind creasing.\n"
            f"3. Third, classify the damage type and severity, and estimate your confidence score (0-100) and alternative explanation (if any).\n"
            f"4. Finally, write a 1-2 sentence 'forensic_narrative' caption that is 100% ACCURATE and grounded solely in visually verifiable data. "
            f"Do NOT invent or hallucinate defects (such as pipe boot leaks or hail hits) if they are not visible in the image. "
            f"If the photo shows a clean slope or normal condition, state that clearly and professionally.\n\n"
            f"For the 'filename' schema field, output exactly: {orig_name}"
        )

        if is_local:
            try:
                file_size, file_bytes = await asyncio.to_thread(self._get_file_size_and_bytes, p_path)
            except Exception as exc:
                log.error("photo_file_not_readable", file_path=str(p_path), error=str(exc))
                raise

            if file_size < 15728640:
                part = genai_types.Part.from_bytes(data=file_bytes, mime_type="image/jpeg")
                response = await asyncio.to_thread(
                    self._call_with_backoff,
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=[part, prompt],
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=PhotoAnalysis,
                        temperature=0.1,
                    ),
                )
                usage = getattr(response.usage_metadata, "total_token_count", 0)
                if usage > 0:
                    await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "photo_analysis")
                return response.parsed  # type: ignore
            else:
                uploaded_name = await self.upload_media_file(str(p_path))
                try:
                    file_status = await self.get_file_status(uploaded_name)
                    while file_status == "PROCESSING":
                        await asyncio.sleep(2)
                        file_status = await self.get_file_status(uploaded_name)
                        
                    if file_status == "FAILED":
                        raise RuntimeError(f"Photo processing failed on Gemini servers: {p_path}")

                    file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=uploaded_name)
                    response = await asyncio.to_thread(
                        self._call_with_backoff,
                        self.client.models.generate_content,
                        model=self.model_name,
                        contents=[file_info, prompt],
                        config=genai_types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=PhotoAnalysis,
                            temperature=0.1,
                        ),
                    )
                    usage = getattr(response.usage_metadata, "total_token_count", 0)
                    if usage > 0:
                        await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "photo_analysis")
                    return response.parsed  # type: ignore
                finally:
                    try:
                        await self.delete_file(uploaded_name)
                    except Exception:
                        pass
        else:
            if is_remote_name:
                file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=str(file_path))
            
            response = await asyncio.to_thread(
                self._call_with_backoff,
                self.client.models.generate_content,
                model=self.model_name,
                contents=[file_info, prompt],
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PhotoAnalysis,
                    temperature=0.1,
                ),
            )
            usage = getattr(response.usage_metadata, "total_token_count", 0)
            if usage > 0:
                await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "photo_analysis")
            return response.parsed  # type: ignore

    async def analyze_roof_photos_batch(
        self,
        file_paths: list[Any],
        original_filenames: list[str] | None = None,
        job_id: str | None = None
    ) -> list[PhotoAnalysis]:
        """
        Multimodal damage analysis of multiple roof photos in a single Gemini request.
        Supports:
          1. List of local file paths: inline batching or Files API mode.
          2. List of remote file names (strings starting with 'files/'): uses Files API.
          3. List of pre-fetched file objects/mocks: uses them directly.
        """
        log = logger.bind(job_id=job_id, count=len(file_paths))
        log.info("photo_analysis_batch_started")

        orig_names = original_filenames or []
        if not orig_names:
            for p in file_paths:
                if isinstance(p, (str, Path)):
                    orig_names.append(Path(p).name)
                else:
                    orig_names.append("photo.jpg")

        all_local = True
        all_remote_name = True
        file_data_list = []
        total_size = 0
        
        for p in file_paths:
            if isinstance(p, (str, Path)):
                p_path = Path(p)
                if str(p).startswith("files/") or not p_path.exists():
                    all_local = False
                else:
                    all_remote_name = False
            else:
                all_local = False
                all_remote_name = False

        prompt = (
            "You are Wickham Roofing's senior forensic roofing inspector creating photographic documentation for an insurance claim.\n\n"
            "Above you have been provided with multiple roof inspection photos, each labeled with its filename in brackets (e.g. [Photo: img_001.jpg]).\n"
            "Analyze EACH photo INDEPENDENTLY and produce a UNIQUE, ACCURATE assessment for that specific photo. "
            "Do NOT copy or repeat an analysis — each photo must have its own distinct findings based on what is actually visible.\n\n"
            "For each photo, perform a Zero-Shot Chain of Thought analysis:\n"
            "Step 1: Identify the photo type (slope overview, shingle close-up, ridge cap, valley, vent boot, flashing, etc.).\n"
            "Step 2: Describe all visible anomalies — their texture, color, shape, and location. "
            "Look for hail impact bruising (circular dark marks with granule displacement), wind crease lines, "
            "exposed fiberglass mat, granule loss patterns, or missing/lifted shingles.\n"
            "Step 3: Classify the primary damage type ('hail', 'wind', 'mechanical', 'aging', 'none') and severity ('none', 'minor', 'moderate', 'severe').\n"
            "Step 4: Estimate your confidence (0-100). If not 100%, provide an alternative explanation "
            "(e.g., manufacturer blistering, normal weathering, mechanical scraping).\n"
            "Step 5: Write a 1-2 sentence 'forensic_narrative' that is 100% grounded in what is VISUALLY PRESENT. "
            "Do NOT hallucinate, invent, or assume damage that is not clearly visible. "
            "If a photo shows a clean or undamaged surface, state that clearly.\n\n"
            "Set the 'filename' field for each result to the exact filename label shown before that photo.\n"
            "Ensure the output JSON contains one PhotoAnalysis entry per photo, in the same order as presented.\n"
            "Each entry MUST reflect that specific photo's actual condition — not a generalized or repeated assessment."
        )

        if all_local:
            inline_possible = True
            for file_path in file_paths:
                try:
                    f_size, f_bytes = await asyncio.to_thread(self._get_file_size_and_bytes, Path(file_path))
                    total_size += f_size
                    if f_size >= 15728640 or total_size >= 20000000:
                        inline_possible = False
                    file_data_list.append((f_bytes, f_size))
                except Exception as e:
                    log.error("photo_batch_file_error", file_path=str(file_path), error=str(e))
                    inline_possible = False
                    break

            if inline_possible and file_data_list:
                log.info("photo_analysis_batch_inline_mode")
                contents = []
                for (f_bytes, _), orig_name in zip(file_data_list, orig_names):
                    contents.append(f"[Photo: {orig_name}]")
                    part = genai_types.Part.from_bytes(data=f_bytes, mime_type="image/jpeg")
                    contents.append(part)
                contents.append(prompt)

                try:
                    response = await asyncio.to_thread(
                        self._call_with_backoff,
                        self.client.models.generate_content,
                        model=self.model_name,
                        contents=contents,
                        config=genai_types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=BatchPhotoAnalysis,
                            temperature=0.1,
                        ),
                    )
                    usage = getattr(response.usage_metadata, "total_token_count", 0)
                    if usage > 0:
                        await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "photo_analysis_batch")
                    batch_result = response.parsed  # type: ignore
                    return batch_result.analyses
                except Exception as batch_err:
                    log.warning("batch_inline_analysis_failed_falling_back_to_sequential", error=str(batch_err))
                    results = []
                    for file_path, orig_name in zip(file_paths, orig_names):
                        try:
                            res = await self.analyze_roof_photo(file_path, orig_name, job_id)
                            results.append(res)
                        except Exception as seq_err:
                            log.error("photo_analysis_sequential_error", file_path=str(file_path), error=str(seq_err))
                    return results
            else:
                log.info("photo_analysis_batch_files_api_mode")
                uploaded_names = []
                try:
                    for file_path in file_paths:
                        uploaded_name = await self.upload_media_file(str(file_path))
                        uploaded_names.append(uploaded_name)
                    
                    for name in uploaded_names:
                        file_status = await self.get_file_status(name)
                        while file_status == "PROCESSING":
                            await asyncio.sleep(2)
                            file_status = await self.get_file_status(name)
                        if file_status == "FAILED":
                            raise RuntimeError(f"Processing failed for file {name}")

                    contents = []
                    for name, orig_name in zip(uploaded_names, orig_names):
                        file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=name)
                        contents.append(f"[Photo: {orig_name}]")
                        contents.append(file_info)
                    contents.append(prompt)

                    response = await asyncio.to_thread(
                        self._call_with_backoff,
                        self.client.models.generate_content,
                        model=self.model_name,
                        contents=contents,
                        config=genai_types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=BatchPhotoAnalysis,
                            temperature=0.1,
                        ),
                    )
                    usage = getattr(response.usage_metadata, "total_token_count", 0)
                    if usage > 0:
                        await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "photo_analysis_batch")
                    batch_result = response.parsed  # type: ignore
                    return batch_result.analyses
                except Exception as batch_err:
                    log.warning("batch_files_api_analysis_failed_falling_back_to_sequential", error=str(batch_err))
                    results = []
                    for file_path, orig_name in zip(file_paths, orig_names):
                        try:
                            res = await self.analyze_roof_photo(file_path, orig_name, job_id)
                            results.append(res)
                        except Exception as seq_err:
                            log.error("photo_analysis_sequential_error", file_path=str(file_path), error=str(seq_err))
                    return results
                finally:
                    for name in uploaded_names:
                        try:
                            await self.delete_file(name)
                        except Exception:
                            pass
        else:
            log.info("photo_analysis_batch_remote_or_objects_mode")
            contents = []
            for p, orig_name in zip(file_paths, orig_names):
                contents.append(f"[Photo: {orig_name}]")
                if isinstance(p, str) and p.startswith("files/"):
                    file_info = await asyncio.to_thread(self._call_with_backoff, self.client.files.get, name=p)
                else:
                    file_info = p
                contents.append(file_info)
            contents.append(prompt)

            response = await asyncio.to_thread(
                self._call_with_backoff,
                self.client.models.generate_content,
                model=self.model_name,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=BatchPhotoAnalysis,
                    temperature=0.1,
                ),
            )
            usage = getattr(response.usage_metadata, "total_token_count", 0)
            if usage > 0:
                await asyncio.to_thread(log_ai_usage, job_id, usage, self.model_name, "photo_analysis_batch")
            batch_result = response.parsed  # type: ignore
            return batch_result.analyses

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        job_id: str | None = None,
        operation_type: str = "generate_text",
    ) -> str:
        """
        Generic plain-text generation method.

        Sends a system prompt + user prompt to Gemini and returns
        the raw text response. Used by escalation_processor and
        any worker that needs unstructured narrative output.

        Args:
            system_prompt: Instruction context for the model.
            user_prompt: The specific request content.
            job_id: Optional job ID for usage logging.
            operation_type: Label for the AI usage log entry.

        Returns:
            str: The model's text response, stripped of whitespace.
        """
        log = logger.bind(job_id=job_id, operation=operation_type)
        log.info("generate_text_started")

        contents = [
            system_prompt + "\n\n" + user_prompt
        ]

        try:
            response = await asyncio.to_thread(
                self._call_with_backoff,
                self.client.models.generate_content,
                model=self.model_name,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    temperature=0.4,
                ),
            )
            usage = getattr(response.usage_metadata, "total_token_count", 0)
            if usage > 0:
                await asyncio.to_thread(
                    log_ai_usage, job_id, usage, self.model_name, operation_type
                )
            log.info("generate_text_complete")
            return response.text.strip()
        except Exception as exc:
            log.error("generate_text_failed", error=str(exc))
            raise


def get_ai_client() -> AiClient:
    return GeminiClient()
