"""
Gemini API low-level transport client.

Responsibilities:
- Authentication & SDK lifecycle initialization.
- Rate limiting protection with exponential backoff and jitter for free-tier quotas.
- Raw file upload, status inspection, and deletion lifecycle.
- Pure transport calls with zero business domain logic.
"""

import asyncio
import random
import time
from pathlib import Path
from typing import Any, cast

import structlog
from google import genai
from google.genai import types as genai_types

from app.config import get_settings
from app.core.database import log_ai_usage

logger = structlog.get_logger("app.services.ai.client")


class GeminiTransportClient:
    """
    Low-level transport wrapper for google-genai SDK.
    Handles network retries, backoff, and file management.
    """

    def __init__(self, api_key: str | None = None, model_name: str = "gemini-3.5-flash") -> None:
        self.settings = get_settings()
        key = api_key or self.settings.gemini_api_key
        self.client = genai.Client(api_key=key)
        self.model_name = model_name
        logger.info("gemini_transport_initialized", model=self.model_name)

    def _call_with_backoff(
        self, func: Any, *args: Any, max_retries: int = 5, **kwargs: Any
    ) -> Any:
        """
        Rate-limit-aware wrapper for Gemini API calls.
        Catches 429 RESOURCE_EXHAUSTED and 5xx transient network errors with exponential backoff.
        """
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e).lower()
                is_transient = any(
                    err in error_str
                    for err in [
                        "429",
                        "resource_exhausted",
                        "503",
                        "504",
                        "deadlineexceeded",
                        "timeouterror",
                    ]
                )
                if is_transient:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "transient_error_backoff",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        wait_seconds=round(wait, 2),
                        error_snippet=error_str[:50],
                    )
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(
            f"Gemini API transient error exceeded after {max_retries} retries."
        )

    async def upload_file(self, file_path: str | Path) -> str:
        """Upload local file to Gemini Files API and return file identifier."""
        uploaded = await asyncio.to_thread(
            self._call_with_backoff, self.client.files.upload, file=str(file_path)
        )
        return cast(str, uploaded.name)

    async def get_file_info(self, file_name: str) -> Any:
        """Retrieve File metadata from Gemini."""
        return await asyncio.to_thread(
            self._call_with_backoff, self.client.files.get, name=file_name
        )

    async def wait_for_file_active(self, file_name: str, timeout_seconds: int = 60) -> Any:
        """Poll Gemini file status until ACTIVE or raise."""
        elapsed = 0
        file_info = await self.get_file_info(file_name)
        while file_info.state.name == "PROCESSING":
            if elapsed >= timeout_seconds:
                raise TimeoutError(f"Gemini file processing timed out for {file_name}")
            await asyncio.sleep(2)
            elapsed += 2
            file_info = await self.get_file_info(file_name)

        if file_info.state.name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {file_name}")
        return file_info

    async def delete_file(self, file_name: str) -> None:
        """Safely delete uploaded file from Gemini storage."""
        try:
            await asyncio.to_thread(
                self._call_with_backoff, self.client.files.delete, name=file_name
            )
        except Exception as exc:
            logger.warning("gemini_file_cleanup_failed", file_name=file_name, error=str(exc))

    async def generate_content(
        self,
        contents: Any,
        config: genai_types.GenerateContentConfig | None = None,
        job_id: str | None = None,
        operation_type: str = "generate_content",
    ) -> Any:
        """Execute generate_content call and log token usage."""
        response = await asyncio.to_thread(
            self._call_with_backoff,
            self.client.models.generate_content,
            model=self.model_name,
            contents=contents,
            config=config,
        )
        usage = getattr(response.usage_metadata, "total_token_count", 0)
        if usage > 0 and job_id:
            await asyncio.to_thread(
                log_ai_usage, job_id, usage, self.model_name, operation_type
            )
        return response
