"""
Application configuration via Pydantic Settings.

Loads and validates all environment variables at startup.
Fails fast with clear errors if required variables are missing.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FIELD_DOCS_DIR = Path("data/field_docs")
FIELD_DOCS_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Validated application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # --- Webhook Security ---
    webhook_secret: str = Field(
        ...,
        description="Shared secret for x-api-key header validation on inbound webhooks.",
    )
    jwt_secret: str = Field(
        default="super-secret-jwt-key-for-local-dev-only",
        description="Secret key for signing JWTs.",
    )
    admin_pin: str = Field(
        default="8471",
        description="Access PIN for the Admin persona.",
    )
    accounting_pin: str = Field(
        default="5392",
        description="Access PIN for the Accounting persona.",
    )
    operations_pin: str = Field(
        default="2648",
        description="Access PIN for the Operations persona.",
    )
    field_pin: str = Field(
        default="",
        description="Access PIN for Field Salesmen. Retired Phase 9. Use field_reps table via Admin UI instead.",
    )

    # --- Redis ---
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL (Render internal KV store).",
    )

    # --- Google Gemini AI ---
    gemini_api_key: str = Field(
        ..., description="API key for the Google Gemini generative AI service."
    )
    
    # --- Image Processing Constraints ---
    ai_image_max_width: int = Field(
        default=1600,
        description="Maximum pixel width for AI photo uploads.",
    )
    pdf_image_max_width: int = Field(
        default=800,
        description="Maximum pixel width for embedded PDF photos.",
    )

    # --- Application ---
    app_env: str = Field(
        default="dev",
        description="Runtime environment: dev or prod.",
    )
    log_level: str = Field(
        default="DEBUG",
        description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
    )
    quarantine_status: str = Field(
        default="API TEST LAB",
        description="CRM status name used to filter test jobs. Only webhooks with this status are processed.",
    )

    # --- Storm Ingestion & Alerting ---
    storm_office_lat: float = Field(
        default=30.8766,
        description="Latitude of the office center for storm monitoring."
    )
    storm_office_lon: float = Field(
        default=-84.1994,
        description="Longitude of the office center for storm monitoring."
    )
    storm_ingest_radius_miles: float = Field(
        default=50.0,
        description="Radius in miles around the office to ingest storm data."
    )
    storm_alert_radius_miles: float = Field(
        default=30.0,
        description="Radius in miles around the office to trigger active alerts."
    )
    storm_alert_min_hail_inches: float = Field(
        default=1.0,
        description="Minimum hail size in inches to trigger a storm alert."
    )
    storm_alert_min_wind_mph: float = Field(
        default=50.0,
        description="Minimum wind speed in mph to trigger a storm alert."
    )
    storm_ingest_interval_minutes: int = Field(
        default=15,
        description="Interval in minutes at which to run the storm ingestion task."
    )

    @property
    def get_db_path(self) -> str:
        """
        Returns the path to the active SQLite database based on the environment.
        - data/wickham.db: Primary Production CRM ledger and job database.
        - data/wickham_dev.db: Local development/testing environment DB.
        Note: data/cache.db is also actively used (managed by app/core/cache.py) 
        for caching AI analysis results.
        """
        if self.app_env.lower() == "prod":
            return "data/wickham.db"
        return "data/wickham_dev.db"

    BACKUP_RETENTION_LIMIT: int = Field(
        default=10,
        description="Number of hot SQLite WAL backups to retain before pruning."
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            msg = f"Invalid log_level '{v}'. Must be one of: {allowed}"
            raise ValueError(msg)
        return upper


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    Raises pydantic's ValidationError at startup if required
    environment variables are missing or malformed.
    """
    return Settings() # type: ignore


