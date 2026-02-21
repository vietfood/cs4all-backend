"""
app/api/v1/grade.py

POST /api/v1/grade — Supabase webhook receiver for new exercise submissions.

Flow (Phase 2):
  1. Validate payload shape via SupabaseWebhookPayload (Pydantic).
  2. Optionally verify HMAC signature if WEBHOOK_SECRET is configured.
  3. Enqueue the submission ID to Redis "grading_queue" for Phase 3 worker.
  4. Return HTTP 202 Accepted immediately — grading is async.

Flow (Phase 3+):
  - The worker process (app/workers/grading_worker.py) picks up the ID
    from the queue, fetches the full submission, compiles the prompt,
    calls Langchain, and writes back llm_score + llm_feedback.

Security note:
  - WEBHOOK_SECRET validation is implemented but SKIPPED if the env var
    is not set (graceful degradation for local dev). It will be enforced
    in production by configuring Supabase to send the secret.

Contract defined in docs/AGENTS.md Section 4.2.
"""

import hashlib
import hmac
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.grading import SupabaseWebhookPayload

logger = get_logger(__name__)
router = APIRouter()

# Redis key for the grading task queue (FIFO via LPUSH / BRPOP).
GRADING_QUEUE_KEY = "cs4all:grading_queue"


def _verify_webhook_signature(
    raw_body: bytes,
    x_webhook_secret: str | None,
    expected_secret: str,
) -> None:
    """Verify the webhook's HMAC-SHA256 signature.

    Supabase custom webhooks can send a shared secret in a header.
    We use constant-time comparison to prevent timing attacks.

    Args:
        raw_body: The raw request body bytes.
        x_webhook_secret: The value of the ``X-Webhook-Secret`` header.
        expected_secret: The secret from ``settings.webhook_secret``.

    Raises:
        HTTPException 401: If the signature is missing or invalid.
    """
    if not x_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Webhook-Secret header",
        )

    # Constant-time comparison to prevent timing attacks.
    is_valid = hmac.compare_digest(
        x_webhook_secret.encode(),
        expected_secret.encode(),
    )
    if not is_valid:
        logger.warning("webhook_signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )


@router.post(
    "/grade",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Supabase webhook: new exercise submission",
    description=(
        "Receives INSERT webhooks from Supabase when a user submits an exercise. "
        "Validates the payload and enqueues the submission ID for async grading. "
        "Always returns 202 — grading happens in the background."
    ),
)
async def receive_grading_webhook(
    request: Request,
    payload: SupabaseWebhookPayload,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    """Validate webhook payload and enqueue submission for grading.

    The endpoint intentionally does NOT perform grading inline — it returns
    immediately to avoid Supabase webhook timeouts (30 s limit).
    """
    settings = get_settings()

    # ── 1. Webhook signature check (if secret is configured) ─────────────────
    if settings.webhook_secret:
        _verify_webhook_signature(
            raw_body=await request.body(),
            x_webhook_secret=x_webhook_secret,
            expected_secret=settings.webhook_secret,
        )
    else:
        logger.debug(
            "webhook_signature_check_skipped",
            reason="WEBHOOK_SECRET not configured — set it before going to production",
        )

    submission_id: UUID = payload.record.id
    lesson_id: str = payload.record.lesson_id
    user_id: UUID = payload.record.user_id

    logger.info(
        "webhook_received",
        submission_id=str(submission_id),
        lesson_id=lesson_id,
        user_id=str(user_id),
    )

    # ── 2. Enqueue submission ID for the grading worker ───────────────────────
    redis: aioredis.Redis = request.app.state.redis
    try:
        # LPUSH + BRPOP makes a reliable FIFO queue.
        # The worker (Phase 3) will BRPOP from the right end.
        await redis.lpush(GRADING_QUEUE_KEY, str(submission_id))
        logger.info(
            "submission_enqueued",
            submission_id=str(submission_id),
            queue=GRADING_QUEUE_KEY,
        )
    except Exception as exc:
        logger.error(
            "enqueue_failed",
            submission_id=str(submission_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue submission for grading. Try again.",
        ) from exc

    return {
        "status": "queued",
        "submission_id": str(submission_id),
    }
