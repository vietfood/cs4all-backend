"""
app/api/v1/admin.py

Admin review endpoints for Phase 3 — Human Review Workflow.

All endpoints require admin authentication via the ``require_admin`` dependency,
which validates the Supabase JWT and checks ``profiles.is_admin == true``.

Endpoints:
  GET  /api/v1/admin/submissions          — List submissions (filterable by status)
  GET  /api/v1/admin/submissions/{id}     — View a single submission
  POST /api/v1/admin/submissions/{id}/review — Assign reviewer_score, mark as reviewed

Security rules (from AGENTS.md Section 3.4):
  - Never return raw prompt internals (compiled LLM prompt) to the frontend.
  - All endpoints require admin role verification.

Status flow (Phase 3):
  'submitted'  →  'human_reviewed'
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.auth import AdminUser, require_admin
from app.core.logging import get_logger
from app.schemas.admin import (
    ReviewRequest,
    ReviewResponse,
    SubmissionDetail,
    SubmissionListResponse,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "/admin/submissions",
    response_model=SubmissionListResponse,
    summary="List exercise submissions (admin)",
    description=(
        "Returns a paginated list of exercise submissions. "
        "Optionally filter by status (submitted, ai_graded, human_reviewed)."
    ),
)
async def list_submissions(
    request: Request,
    admin: AdminUser = Depends(require_admin),
    submission_status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> SubmissionListResponse:
    """List submissions for admin review.

    Args:
        submission_status: Filter by status (e.g. 'submitted' for pending review).
        page: Page number (1-indexed).
        page_size: Number of items per page (max 100).
    """
    supabase = request.app.state.supabase
    page_size = min(page_size, 100)  # Cap to prevent abuse
    offset = (max(page, 1) - 1) * page_size

    try:
        # Build query — select submissions ordered by newest first
        query = supabase.table("exercise_submissions").select(
            "*", count="exact"
        )

        if submission_status:
            query = query.eq("status", submission_status)

        result = (
            query.order("submitted_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        submissions = [SubmissionDetail(**row) for row in (result.data or [])]
        total = result.count if result.count is not None else len(submissions)

    except Exception as exc:
        logger.error("admin_list_submissions_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch submissions",
        ) from exc

    logger.info(
        "admin_list_submissions",
        admin_id=str(admin.user_id),
        status_filter=submission_status,
        page=page,
        count=len(submissions),
        total=total,
    )

    return SubmissionListResponse(
        submissions=submissions,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/admin/submissions/{submission_id}",
    response_model=SubmissionDetail,
    summary="View a single submission (admin)",
)
async def get_submission(
    submission_id: UUID,
    request: Request,
    admin: AdminUser = Depends(require_admin),
) -> SubmissionDetail:
    """Fetch a single submission by ID for admin review."""
    supabase = request.app.state.supabase

    try:
        result = (
            supabase.table("exercise_submissions")
            .select("*")
            .eq("id", str(submission_id))
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(
            "admin_get_submission_failed",
            submission_id=str(submission_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission {submission_id} not found",
        ) from exc

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission {submission_id} not found",
        )

    logger.info(
        "admin_view_submission",
        admin_id=str(admin.user_id),
        submission_id=str(submission_id),
    )

    return SubmissionDetail(**result.data)


@router.post(
    "/admin/submissions/{submission_id}/review",
    response_model=ReviewResponse,
    summary="Review a submission (admin)",
    description=(
        "Assigns a reviewer_score to the submission and transitions its status "
        "to 'human_reviewed'. This is the terminal state for Phase 3."
    ),
)
async def review_submission(
    submission_id: UUID,
    body: ReviewRequest,
    request: Request,
    admin: AdminUser = Depends(require_admin),
) -> ReviewResponse:
    """Assign a human review score and finalize the submission.

    Status transition: 'submitted' (or 'ai_graded') → 'human_reviewed'
    """
    supabase = request.app.state.supabase

    # ── Verify the submission exists and is in a reviewable state ──────────────
    try:
        fetch_result = (
            supabase.table("exercise_submissions")
            .select("id, status")
            .eq("id", str(submission_id))
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(
            "review_fetch_failed",
            submission_id=str(submission_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission {submission_id} not found",
        ) from exc

    if not fetch_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission {submission_id} not found",
        )

    current_status = fetch_result.data.get("status")

    # Status guard: only 'submitted' or 'ai_graded' can transition to 'human_reviewed'
    # See docs/AGENTS.md Section 4.5 — no agent may skip or reverse transitions.
    if current_status == "human_reviewed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Submission has already been reviewed",
        )

    if current_status not in ("submitted", "ai_graded"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot review submission with status '{current_status}'",
        )

    # ── Write the review ──────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()

    try:
        update_result = (
            supabase.table("exercise_submissions")
            .update(
                {
                    "reviewer_score": body.reviewer_score,
                    "status": "human_reviewed",
                    "reviewed_at": now,
                }
            )
            .eq("id", str(submission_id))
            .execute()
        )
    except Exception as exc:
        logger.error(
            "review_update_failed",
            submission_id=str(submission_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save review",
        ) from exc

    updated = update_result.data[0] if update_result.data else {}

    logger.info(
        "submission_reviewed",
        admin_id=str(admin.user_id),
        admin_email=admin.email,
        submission_id=str(submission_id),
        reviewer_score=body.reviewer_score,
        previous_status=current_status,
    )

    return ReviewResponse(
        status="reviewed",
        submission_id=submission_id,
        reviewer_score=body.reviewer_score,
        final_score=updated.get("final_score"),
    )
