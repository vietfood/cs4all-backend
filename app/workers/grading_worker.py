"""
app/workers/grading_worker.py

Standalone async worker process that consumes from the Redis grading queue.

Phase 3 behaviour:
  - BRPOP from ``cs4all:grading_queue`` (blocks until an item arrives)
  - Fetch the submission from ``exercise_submissions`` via Service Role Key
  - Validate the row exists and status == 'submitted'
  - Log the submission for the human review queue (no LLM grading)

Phase 3.5 behaviour (future):
  - After fetching, compile prompt with rubric + user content
  - Call Langchain → validate GradingResponse → write llm_score, llm_feedback
  - Update status to 'ai_graded'

Run as:
    uv run python -m app.workers.grading_worker

See cs4all-backend/AGENTS.md Section 3.2 for the full responsibility chain.
"""

import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.services.redis_client import close_redis, init_redis
from app.services.supabase import init_supabase

logger = get_logger(__name__)

# Must match the key used by POST /api/v1/grade (app/api/v1/grade.py)
GRADING_QUEUE_KEY = "cs4all:grading_queue"

# How long BRPOP blocks before checking for shutdown signals (seconds)
BRPOP_TIMEOUT = 5


async def process_submission(supabase, submission_id: str) -> None:
    """Fetch and process a single submission from the database.

    Phase 3: validates the submission exists and logs it.
    Phase 3.5: this function will be extended with Langchain grading.

    Args:
        supabase: The initialized Supabase client (Service Role Key).
        submission_id: UUID string of the exercise submission.
    """
    logger.info("processing_submission", submission_id=submission_id)

    # ── Fetch the submission row ──────────────────────────────────────────────
    try:
        result = (
            supabase.table("exercise_submissions")
            .select("*")
            .eq("id", submission_id)
            .single()
            .execute()
        )
        submission = result.data
    except Exception as exc:
        logger.error(
            "submission_fetch_failed",
            submission_id=submission_id,
            error=str(exc),
        )
        return

    if not submission:
        logger.warning("submission_not_found", submission_id=submission_id)
        return

    # ── Validate status ───────────────────────────────────────────────────────
    current_status = submission.get("status")
    if current_status != "submitted":
        logger.warning(
            "submission_skipped_wrong_status",
            submission_id=submission_id,
            status=current_status,
            reason="Expected 'submitted', skipping to avoid reprocessing",
        )
        return

    # ── Phase 3: Log for human review queue ───────────────────────────────────
    # In Phase 3, submissions go straight to the human review queue.
    # The admin endpoints (GET /api/v1/admin/submissions) serve as the queue.
    # No status change here — it stays as 'submitted' until an admin reviews it.
    logger.info(
        "submission_ready_for_review",
        submission_id=submission_id,
        user_id=submission.get("user_id"),
        lesson_id=submission.get("lesson_id"),
        content_length=len(submission.get("content", "")),
    )

    # TODO (Phase 3.5): Replace the above with:
    #   1. Fetch rubric for lesson_id from GitHub API
    #   2. Compile structured prompt
    #   3. Call Langchain with GradingResponse schema enforcement
    #   4. Validate response with Pydantic
    #   5. UPDATE exercise_submissions SET llm_score, llm_feedback, status='ai_graded'


async def run_worker() -> None:
    """Main worker loop — runs indefinitely, consuming from the grading queue.

    Initializes its own Supabase and Redis clients (separate from the FastAPI app).
    Blocks on BRPOP and processes one submission at a time.
    """
    settings = get_settings()
    setup_logging(environment=settings.environment)

    logger.info(
        "worker_starting",
        queue=GRADING_QUEUE_KEY,
        environment=settings.environment,
    )

    # Initialize clients (worker has its own lifecycle, independent of FastAPI)
    supabase = await init_supabase()
    redis = await init_redis()

    logger.info("worker_ready", message="Listening for submissions...")

    try:
        while True:
            # BRPOP blocks until an item is available or timeout elapses.
            # Returns (key, value) or None on timeout.
            result = await redis.brpop(GRADING_QUEUE_KEY, timeout=BRPOP_TIMEOUT)

            if result is None:
                # Timeout — no items in queue. Loop back and block again.
                continue

            _key, submission_id = result
            submission_id = str(submission_id)

            try:
                await process_submission(supabase, submission_id)
            except Exception as exc:
                # Catch-all: never let a single bad submission crash the worker.
                logger.error(
                    "submission_processing_error",
                    submission_id=submission_id,
                    error=str(exc),
                    exc_info=True,
                )

    except KeyboardInterrupt:
        logger.info("worker_interrupted", message="Received SIGINT, shutting down...")
    except Exception as exc:
        logger.error("worker_fatal_error", error=str(exc), exc_info=True)
        sys.exit(1)
    finally:
        await close_redis(redis)
        logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
