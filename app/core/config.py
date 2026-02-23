"""
app/core/config.py

Application settings loaded from environment variables / .env file.
Uses Pydantic Settings v2 for type-safe config with fail-fast validation:
if a required variable is missing, the app will refuse to start with a
clear error message rather than silently propagating None values.

Usage:
    from app.core.config import get_settings
    settings = get_settings()
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Extra fields in .env are silently ignored — tolerates future additions.
        extra="ignore",
    )

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str = Field(..., description="Supabase project URL (same as frontend)")
    supabase_service_role_key: str = Field(
        ..., description="Service Role Key — bypasses RLS. NEVER share with frontend."
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for the task queue",
    )

    # ── Webhook security ──────────────────────────────────────────────────────
    # Optional in Phase 2; will be required in Phase 3 when Supabase webhook is live.
    webhook_secret: str | None = Field(
        default=None,
        description="HMAC secret shared with Supabase webhook config (Phase 3+)",
    )

    # ── LLM APIs ──────────────────────────────────────────────────────────────
    # Optional now; required in Phase 3.5 when Langchain grading is active.
    gemini_api_key: str | None = Field(default=None, description="Google Gemini API key (Phase 3.5+)")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key (alternative, Phase 3.5+)")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key (alternative, Phase 3.5+)")

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: str | None = Field(
        default=None,
        description="Read-only PAT scoped to vietfood/cs4all-content (Phase 3.5+)",
    )

    # ── LLM Model Override ───────────────────────────────────────────────────
    llm_model: str | None = Field(
        default=None,
        description="Override default LLM model name (e.g. 'gemini-2.0-flash', 'gpt-4o-mini')",
    )

    # ── Application ───────────────────────────────────────────────────────────
    environment: Literal["development", "production"] = Field(
        default="development",
        description="Runtime environment — controls log format and debug features",
    )
    app_version: str = Field(default="0.1.0")

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("supabase_url")
    @classmethod
    def supabase_url_must_be_valid(cls, v: str) -> str:
        # Allow both https:// (production) and http://127... (local Supabase CLI).
        # Plain strings without a scheme are always wrong.
        if not v.startswith(("https://", "http://127.", "http://localhost")):
            raise ValueError(
                "SUPABASE_URL must start with https:// (production) or "
                "http://127.x.x.x / http://localhost (local Supabase CLI only)"
            )
        return v.rstrip("/")

    @field_validator("supabase_service_role_key")
    @classmethod
    def service_role_key_must_not_be_empty(cls, v: str) -> str:
        if not v or v.strip() == "":
            raise ValueError(
                "SUPABASE_SERVICE_ROLE_KEY is not set. "
                "Copy .env.example to .env and fill in the value."
            )
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return (and cache) the application settings singleton.

    The cache means settings are validated once at first call.
    Use `get_settings.cache_clear()` in tests to reload from a fresh environment.
    """
    return Settings()
