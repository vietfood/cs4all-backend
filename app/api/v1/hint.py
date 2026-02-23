"""
app/api/v1/hint.py

POST /api/v1/hint — SSE streaming endpoint for LLM-assisted hints.

Flow:
  1. Validate JWT (Supabase auth — user must be logged in)
  2. Rate-limit check (Redis counter: 20 asks/day per user)
  3. Fetch lesson context from GitHub API
  4. Compile Socratic hint prompt (Jinja2)
  5. Stream LLM response as SSE (text/event-stream)

The response includes ``[ref:"..."]`` markers that the frontend parses
into styled citation blocks referencing lesson equations/paragraphs.
"""

from datetime import date

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger
from app.schemas.hint import HintRequest
from app.services.github import ExerciseFetchError, fetch_lesson_context
from app.services.hint_prompt import compile_hint_prompt
from app.services.llm import GradingError, stream_hint

logger = get_logger(__name__)

router = APIRouter()

# Daily rate limit per user
DAILY_HINT_LIMIT = 20
HINT_COUNTER_PREFIX = "cs4all:hints"


async def _get_user_id_from_request(request: Request) -> str:
    """Extract and validate user ID from the Supabase JWT.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The authenticated user's UUID string.

    Raises:
        HTTPException: 401 if no valid token / user.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in to ask questions.",
        )

    token = auth_header.removeprefix("Bearer ").strip()
    supabase = request.app.state.supabase

    try:
        user_response = supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
            )
        return str(user_response.user.id)
    except Exception as exc:
        logger.warning("hint_auth_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed.",
        ) from exc


async def _check_rate_limit(request: Request, user_id: str) -> None:
    """Check and increment the daily hint rate limit.

    Uses a Redis counter with key ``cs4all:hints:{user_id}:{YYYY-MM-DD}``
    that expires at end of day.

    Args:
        request: The FastAPI request (for Redis access).
        user_id: The authenticated user's ID.

    Raises:
        HTTPException: 429 if rate limit exceeded.
    """
    redis = request.app.state.redis
    today = date.today().isoformat()
    key = f"{HINT_COUNTER_PREFIX}:{user_id}:{today}"

    count = await redis.incr(key)

    # Set expiry on first use (24h from midnight)
    if count == 1:
        await redis.expire(key, 86400)  # 24 hours

    if count > DAILY_HINT_LIMIT:
        logger.warning(
            "hint_rate_limited",
            user_id=user_id,
            count=count,
            limit=DAILY_HINT_LIMIT,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily limit of {DAILY_HINT_LIMIT} questions reached. Try again tomorrow.",
        )

    logger.debug("hint_rate_check", user_id=user_id, count=count, limit=DAILY_HINT_LIMIT)


@router.post("/hint", status_code=status.HTTP_200_OK)
async def ask_hint(body: HintRequest, request: Request):
    """Stream a Socratic hint for the given lesson and question.

    Returns an SSE stream (``text/event-stream``) with token-by-token
    LLM output. The stream ends with ``data: [DONE]``.
    """
    # 1. Authenticate
    user_id = await _get_user_id_from_request(request)

    # 2. Rate-limit
    await _check_rate_limit(request, user_id)

    logger.info(
        "hint_request",
        user_id=user_id,
        lesson_id=body.lesson_id,
        question_length=len(body.question),
    )

    # 3. Fetch lesson context
    try:
        context = await fetch_lesson_context(body.lesson_id)
    except ExerciseFetchError as exc:
        logger.error(
            "hint_context_fetch_failed",
            lesson_id=body.lesson_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Could not load lesson content for '{body.lesson_id}'.",
        ) from exc

    # 4. Compile prompt
    prompt = compile_hint_prompt(
        question=body.question,
        lesson_title=context.title,
        grading_context=context.grading_context,
        lesson_content=context.content_body,
        anchor_map=body.anchor_map,
    )

    # 5. Stream response
    async def sse_generator():
        """Yield SSE-formatted chunks from the LLM stream."""
        try:
            async for token in stream_hint(prompt, anchor_map=body.anchor_map):
                # SSE format: data: <content>\n\n
                yield f"data: {token}\n\n"
            yield "data: [DONE]\n\n"
        except GradingError as exc:
            logger.error("hint_stream_error", error=str(exc))
            yield f"data: [ERROR] {exc}\n\n"
        except Exception as exc:
            logger.error(
                "hint_stream_unexpected_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            yield "data: [ERROR] An unexpected error occurred.\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
