"""
app/schemas/admin.py

Pydantic v2 models for the admin review API endpoints.

All models use ``extra="ignore"`` to tolerate Supabase's extra database columns
without crashing during serialization.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Request models ─────────────────────────────────────────────────────────────


class ReviewRequest(BaseModel):
    """Body of POST /api/v1/admin/submissions/{id}/review."""

    reviewer_score: int = Field(..., ge=0, le=100, description="Human reviewer's score (0-100)")
    reviewer_comment: str | None = Field(
        default=None,
        max_length=5000,
        description="Optional reviewer comment or notes (not stored in llm_feedback)",
    )


# ── Response models ────────────────────────────────────────────────────────────


class SubmissionDetail(BaseModel):
    """A single exercise submission row — used for both list and detail views.

    Maps directly to ``public.exercise_submissions`` columns.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    lesson_id: str
    content: str

    # Grading columns — may be null if not yet graded/reviewed
    llm_score: int | None = None
    llm_feedback: dict | list | None = None
    reviewer_score: int | None = None
    final_score: int | None = None

    status: str
    submitted_at: datetime
    reviewed_at: datetime | None = None


class SubmissionListResponse(BaseModel):
    """Response for GET /api/v1/admin/submissions — paginated list."""

    submissions: list[SubmissionDetail]
    total: int
    page: int
    page_size: int


class ReviewResponse(BaseModel):
    """Response for POST /api/v1/admin/submissions/{id}/review."""

    status: Literal["reviewed"]
    submission_id: UUID
    reviewer_score: int
    final_score: int | None = None
