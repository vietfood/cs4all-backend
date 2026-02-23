"""
app/services/llm.py

Langchain LLM integration for:
  - Automated exercise grading (structured output → GradingResponse)
  - Socratic hints (streaming free-form markdown)

Initializes a chat model based on available API keys (Gemini → OpenAI).

Rules (from AGENTS.md Section 3.3):
    - Always validate LLM response against GradingResponse before DB write.
    - Never write free-form LLM output to the database.
    - If malformed response, log raw output and do NOT write partial data.
    - LLM API key comes from environment — never hardcode.
"""

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.grading import GradingResponse
from app.services.grading_prompt import compile_grading_prompt
from app.services.hint_process import HintPostProcessor

logger = get_logger(__name__)


class GradingError(Exception):
    """Raised when LLM grading fails permanently (after retries)."""

    pass


def _create_llm():
    """Create a Langchain chat model based on available API keys.

    Priority: Gemini → OpenAI.
    Returns a chat model instance configured with the appropriate API key.

    Raises:
        GradingError: If no API key is configured.
    """
    settings = get_settings()

    if settings.gemini_api_key:
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_name = settings.llm_model or "gemini-2.5-flash"
        logger.info("llm_init", provider="google", model=model_name)
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.gemini_api_key,
            temperature=0.1,  # Low temperature for consistent grading
        )

    if settings.openai_api_key:
        from langchain_openai import ChatOpenAI

        model_name = settings.llm_model or "gpt-4o-mini"
        logger.info("llm_init", provider="openai", model=model_name)
        return ChatOpenAI(
            model=model_name,
            api_key=settings.openai_api_key,
            temperature=0.1,
        )

    raise GradingError(
        "No LLM API key configured. Set GEMINI_API_KEY or OPENAI_API_KEY in .env"
    )


async def grade_submission(
    *,
    question_text: str,
    rubric_criteria: list[dict] | None = None,
    reference_solution: str | None = None,
    user_content: str,
    language: str = "Vietnamese",
    grading_context: str | None = None,
    max_retries: int = 1,
) -> GradingResponse:
    """Grade a student's exercise submission using LLM.

    Compiles the grading prompt via Jinja2 template, calls the LLM with
    structured output enforcement, validates the response, and returns
    a GradingResponse.

    Args:
        question_text: The exercise question text.
        rubric_criteria: List of {"points": int, "description": str} dicts.
        reference_solution: The reference solution text (optional).
        user_content: The student's submitted solution.
        language: The natural language of the exercise (default: Vietnamese).
        max_retries: Number of retries on transient failures.

    Returns:
        A validated GradingResponse instance.

    Raises:
        GradingError: If grading fails permanently after retries.
    """
    # Compile the prompt
    prompt = compile_grading_prompt(
        question_text=question_text,
        rubric_criteria=rubric_criteria,
        reference_solution=reference_solution,
        user_submission=user_content,
        language=language,
        grading_context=grading_context,
    )

    # Create LLM with structured output
    llm = _create_llm()
    structured_llm = llm.with_structured_output(GradingResponse)

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            logger.info(
                "llm_grading_start",
                attempt=attempt + 1,
                prompt_length=len(prompt),
            )

            result = await structured_llm.ainvoke(prompt)

            if not isinstance(result, GradingResponse):
                raise GradingError(
                    f"LLM returned unexpected type: {type(result).__name__}"
                )

            logger.info(
                "llm_grading_success",
                overall_score=result.overall_score,
                feedback_count=len(result.feedback),
            )

            return result

        except GradingError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "llm_grading_attempt_failed",
                attempt=attempt + 1,
                max_retries=max_retries,
                error=str(exc),
                error_type=type(exc).__name__,
            )

            if attempt < max_retries:
                logger.info("llm_grading_retrying", next_attempt=attempt + 2)
                continue

    # All retries exhausted
    logger.error(
        "llm_grading_failed_permanently",
        error=str(last_error),
        error_type=type(last_error).__name__ if last_error else "unknown",
    )
    raise GradingError(
        f"LLM grading failed after {max_retries + 1} attempts: {last_error}"
    ) from last_error


async def stream_hint(prompt: str, anchor_map: list[dict] | None = None):
    """Stream a free-form hint response from the LLM with post-processing

    Used by the hint API endpoint for token-by-token SSE streaming.
    Unlike grade_submission(), this does NOT enforce structured output —
    the response is free-form markdown with optional [ref:"..."] markers.
    """
    llm = _create_llm()
    logger.info("hint_stream_start", prompt_length=len(prompt))

    valid_ids = [a["id"] for a in anchor_map] if anchor_map else []
    processor = HintPostProcessor(valid_ids=valid_ids)

    async for chunk in llm.astream(prompt):
        if hasattr(chunk, "content"):
            if isinstance(chunk.content, list):
                token = "".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in chunk.content)
            else:
                token = str(chunk.content)
        else:
            token = str(chunk)
            
        if token:
            output = processor.feed(token)
            if output:
                yield output

    # Always flush — catches any ref tag sitting in the buffer at stream end
    remainder = processor.flush()
    if remainder:
        yield remainder

    logger.info("hint_stream_complete")