"""
app/workers/grading_worker.py

Standalone async worker process that consumes from the Redis grading queue.

Phase 3.5 behaviour:
  - BRPOP from ``cs4all:grading_queue`` (blocks until an item arrives)
  - Fetch the submission from ``exercise_submissions`` via Service Role Key
  - Validate the row exists and status == 'submitted'
  - Fetch exercise content (question, rubric, reference solution) from GitHub
  - Call LLM via Langchain with structured output (GradingResponse)
  - On success: UPDATE llm_score, llm_feedback, status='ai_graded'
  - On failure: log error, set status='grading_failed' if permanent

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

    Phase 3.5 pipeline:
      1. Fetch submission row from DB
      2. Validate status == 'submitted'
      3. Fetch exercise content (question, rubric, solution) from GitHub
      4. Call LLM grader with structured output
      5. Write llm_score, llm_feedback, status='ai_graded' to DB

    On permanent failure (e.g., malformed LLM output after retries):
      - Set status='grading_failed' and log the error.

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

    # ── Phase 3.5: LLM-Assisted Grading ───────────────────────────────────────
    lesson_id = submission.get("lesson_id", "")
    user_content = submission.get("content", "")

    try:
        # 1. Fetch exercise content from GitHub (question, rubric, solution)
        from app.services.github import ExerciseFetchError, fetch_exercise_content

        exercise = await fetch_exercise_content(lesson_id)

        logger.info(
            "exercise_content_fetched",
            submission_id=submission_id,
            exercise_id=exercise.exercise_id,
            has_rubric=bool(exercise.rubric_criteria),
            has_solution=bool(exercise.solution),
        )

        # 2. Call LLM grader
        from app.services.llm import GradingError, grade_submission

        grading_result = await grade_submission(
            question_text=exercise.question,
            rubric_criteria=exercise.rubric_criteria,
            reference_solution=exercise.solution,
            user_content=user_content,
            grading_context=exercise.grading_context,
        )

        # 3. Write results to DB
        supabase.table("exercise_submissions").update(
            {
                "llm_score": grading_result.overall_score,
                "llm_feedback": [item.model_dump() for item in grading_result.feedback],
                "status": "ai_graded",
            }
        ).eq("id", submission_id).execute()

        logger.info(
            "submission_graded",
            submission_id=submission_id,
            llm_score=grading_result.overall_score,
            feedback_count=len(grading_result.feedback),
            status="ai_graded",
        )

    except (ExerciseFetchError, GradingError) as exc:
        # Permanent failure — mark as grading_failed
        logger.error(
            "grading_permanent_failure",
            submission_id=submission_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        try:
            supabase.table("exercise_submissions").update(
                {"status": "grading_failed"}
            ).eq("id", submission_id).execute()
            logger.info(
                "submission_marked_failed",
                submission_id=submission_id,
            )
        except Exception as db_exc:
            logger.error(
                "failed_to_mark_grading_failed",
                submission_id=submission_id,
                error=str(db_exc),
            )

    except Exception as exc:
        # Unexpected error — log but leave as 'submitted' for retry
        logger.error(
            "grading_unexpected_error",
            submission_id=submission_id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )


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
    # init_supabase() is async for consistency with the lifespan pattern,
    # even though supabase-py's create_client() is synchronous.
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
