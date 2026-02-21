"""
app/schemas/grading.py

Pydantic v2 models for:
  1. The Supabase Webhook payload (POST /api/v1/grade receives this).
  2. The Langchain/LLM grading response (Phase 3.5 validation before DB write).

Contracts defined in docs/AGENTS.md Sections 4.2 and 4.3.

Rules:
  - All models use ``extra="ignore"`` to tolerate extra Supabase fields without
    crashing. Supabase envelopes can include additional metadata not in the contract.
  - Never write an LLM response to the DB without validating against GradingResponse.
  - RubricCriterion must match the frontmatter schema in cs4all-frontend/content.config.ts.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Supabase Webhook Payload ───────────────────────────────────────────────────


class WebhookRecord(BaseModel):
    """The ``record`` field from a Supabase table INSERT webhook.

    Matches the contract defined in docs/AGENTS.md Section 4.2.
    Only the fields the backend actually needs are declared; extras are ignored.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    lesson_id: str
    content: str
    status: Literal["submitted"] = "submitted"
    submitted_at: datetime

    # These are always null at INSERT time — declared here for completeness.
    # The backend must never assume they are set.
    llm_score: int | None = None
    llm_feedback: dict | None = None
    reviewer_score: int | None = None


class SupabaseWebhookPayload(BaseModel):
    """Full Supabase Webhook POST body for a table INSERT event.

    Supabase sends additional top-level keys (``schema``, ``old_record``, etc.)
    which we don't need and safely ignore via ``extra="ignore"``.
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["INSERT"]
    table: Literal["exercise_submissions"]
    record: WebhookRecord
    schema_: str | None = Field(default=None, alias="schema")


# ── Rubric (shared frontmatter schema — source of truth: content.config.ts) ───


class RubricCriterion(BaseModel):
    """One criterion entry from an MDX file's frontmatter rubric array.

    Must stay in sync with the Zod schema in cs4all-frontend/content.config.ts.
    If the frontend schema changes, update this model and docs/AGENTS.md Section 4.4.
    """

    model_config = ConfigDict(extra="ignore")

    criterion: str
    points: int
    description: str


# ── LLM Grading Response (Langchain structured output — Phase 3.5) ────────────


class FeedbackItem(BaseModel):
    """Per-criterion breakdown in the LLM's grading response.

    The ``criterion`` value must exactly match a ``RubricCriterion.criterion``
    string from the lesson's frontmatter. Validated by the grading worker
    before writing to the DB.
    """

    model_config = ConfigDict(extra="forbid")  # strict: LLM must not hallucinate fields

    criterion: str = Field(..., description="Must match rubric criterion name exactly")
    points_awarded: int = Field(..., ge=0)
    points_possible: int = Field(..., ge=0)
    comment: str


class GradingResponse(BaseModel):
    """Full structured output from the LLM grader.

    This is the exact JSON shape stored verbatim in ``exercise_submissions.llm_feedback``.
    Defined in docs/AGENTS.md Section 4.3.

    Never write a partial or unvalidated LLM response to the database.
    If Langchain returns a malformed response, log the raw output and do NOT
    write anything — see AGENTS.md Section 3.3.
    """

    model_config = ConfigDict(extra="forbid")

    overall_score: int = Field(..., ge=0, le=100)
    feedback: list[FeedbackItem] = Field(..., min_length=1)


# ── Health check response (used by health endpoint) ───────────────────────────


class ServiceStatus(BaseModel):
    status: Literal["ok", "degraded", "error"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    supabase: ServiceStatus
    redis: ServiceStatus
