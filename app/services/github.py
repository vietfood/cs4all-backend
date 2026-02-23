"""
app/services/github.py

Fetch exercise content (question, solution, rubric) from the vietfood/cs4all-content
GitHub repository via the GitHub Contents API.

The worker calls this to get the reference material for LLM grading.

Rules (from AGENTS.md Section 3.7):
    - Never read/write cs4all-content files directly — fetch via GitHub API.
    - Uses GITHUB_TOKEN from settings for authenticated access (higher rate limits).
"""

import base64
import json
import re
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"
CONTENT_REPO = "vietfood/cs4all-content"

# In-memory cache: {file_path: raw_content}
_content_cache: dict[str, str] = {}


@dataclass
class ExerciseContent:
    """Parsed exercise content from the MDX file."""

    exercise_id: str
    question: str
    solution: str | None = None
    rubric_criteria: list[dict] | None = field(default_factory=list)
    grading_context: str | None = None


class ExerciseFetchError(Exception):
    """Raised when exercise content cannot be fetched or parsed."""

    pass


def _parse_lesson_id(lesson_id: str) -> tuple[str, str | None]:
    """Parse a lesson_id into (file_path, exercise_id).

    Format: "prml/1-exercise#1-1"
        → file_path: "note/prml/1-exercise/index.mdx"
        → exercise_id: "1-1"

    If no '#' is present, exercise_id is None (entire page).

    Args:
        lesson_id: The lesson/exercise ID from the submission.

    Returns:
        Tuple of (relative file path in content repo, exercise ID or None).
    """
    if "#" in lesson_id:
        page_id, exercise_id = lesson_id.rsplit("#", 1)
    else:
        page_id = lesson_id
        exercise_id = None

    # Content repo structure: note/{subject}/{chapter-slug}/index.mdx
    file_path = f"note/{page_id}/index.mdx"
    return file_path, exercise_id


async def _fetch_file_from_github(file_path: str) -> str:
    """Fetch a file from the content repo via GitHub Contents API.

    Uses in-memory caching to avoid repeated API calls for the same file.

    Args:
        file_path: Relative path within the content repo.

    Returns:
        The raw file content as a string.

    Raises:
        ExerciseFetchError: If the file cannot be fetched.
    """
    if file_path in _content_cache:
        logger.debug("github_cache_hit", file_path=file_path)
        return _content_cache[file_path]

    settings = get_settings()
    url = f"{GITHUB_API_BASE}/repos/{CONTENT_REPO}/contents/{file_path}"

    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "github_fetch_failed",
            file_path=file_path,
            status_code=exc.response.status_code,
            error=str(exc),
        )
        raise ExerciseFetchError(
            f"Failed to fetch {file_path} from GitHub: {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        logger.error(
            "github_request_error",
            file_path=file_path,
            error=str(exc),
        )
        raise ExerciseFetchError(
            f"GitHub API request failed for {file_path}: {exc}"
        ) from exc

    data = response.json()

    # GitHub returns base64-encoded content
    raw_content = base64.b64decode(data["content"]).decode("utf-8")

    # Cache for the worker's lifetime
    _content_cache[file_path] = raw_content
    logger.info("github_file_fetched", file_path=file_path, size=len(raw_content))

    return raw_content

def _extract_frontmatter_field(mdx_content: str, field_name: str) -> str | None:
    """Extract a field value from MDX YAML frontmatter.

    Handles both single-line values and multi-line block scalars (using |).
    Uses simple string parsing to avoid adding a PyYAML dependency.

    Args:
        mdx_content: The full raw MDX file content.
        field_name: The frontmatter field name to extract.

    Returns:
        The field value as a string, or None if not found.
    """
    # Frontmatter is between the first two '---' lines
    parts = mdx_content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = parts[1]

    # Try multi-line block scalar first: "field: |" followed by indented lines
    block_pattern = re.compile(
        rf'^{re.escape(field_name)}:\s*\|\s*\n((?:\s+.+\n?)+)',
        re.MULTILINE,
    )
    block_match = block_pattern.search(frontmatter)
    if block_match:
        # Dedent the block content
        lines = block_match.group(1).split("\n")
        dedented = "\n".join(line.strip() for line in lines if line.strip())
        return dedented

    # Try single-line: "field: value" or 'field: "value"'
    line_pattern = re.compile(
        rf'^{re.escape(field_name)}:\s*["\']?(.+?)["\']?\s*$',
        re.MULTILINE,
    )
    line_match = line_pattern.search(frontmatter)
    if line_match:
        return line_match.group(1).strip()

    return None


def _extract_exercise_block(mdx_content: str, exercise_id: str) -> dict[str, str]:
    """Extract question, solution, and rubric from an ExerciseBlock in MDX.

    Parses the raw MDX to find the <ExerciseBlock id="X"> and extracts
    the content between <Question>...</Question>, <Solution>...</Solution>,
    and <Rubric hidden>...</Rubric> tags.

    Args:
        mdx_content: The full raw MDX file content.
        exercise_id: The exercise ID to extract (e.g., "1-1").

    Returns:
        Dict with keys: "question", "solution", "rubric_raw".

    Raises:
        ExerciseFetchError: If the exercise block cannot be found.
    """
    # Find the ExerciseBlock with matching id
    # Pattern: <ExerciseBlock id="1-1" ...> ... </ExerciseBlock>
    block_pattern = re.compile(
        rf'<ExerciseBlock\s[^>]*id="{re.escape(exercise_id)}"[^>]*>'
        r'(.*?)'
        r'</ExerciseBlock>',
        re.DOTALL,
    )
    match = block_pattern.search(mdx_content)
    if not match:
        raise ExerciseFetchError(
            f"ExerciseBlock with id='{exercise_id}' not found in MDX"
        )

    block_content = match.group(1)

    # Extract <Question>...</Question>
    question_match = re.search(
        r'<Question>(.*?)</Question>', block_content, re.DOTALL
    )
    question = question_match.group(1).strip() if question_match else ""

    # Extract <Solution>...</Solution>
    solution_match = re.search(
        r'<Solution>(.*?)</Solution>', block_content, re.DOTALL
    )
    solution = solution_match.group(1).strip() if solution_match else ""

    # Extract <Rubric hidden>...</Rubric>
    rubric_match = re.search(
        r'<Rubric[^>]*>(.*?)</Rubric>', block_content, re.DOTALL
    )
    rubric_raw = rubric_match.group(1).strip() if rubric_match else ""

    # Extract grading_context from page frontmatter (between --- delimiters)
    grading_context = _extract_frontmatter_field(mdx_content, "grading_context")

    return {
        "question": question,
        "solution": solution,
        "rubric_raw": rubric_raw,
        "grading_context": grading_context,
    }


def _parse_rubric_json(rubric_raw: str) -> list[dict] | None:
    """Parse the rubric JSON from the raw <Rubric> content.

    Expected format:
        {"criteria": [{"points": 2, "description": "..."}, ...]}
    Handles both raw JSON and JSON enclosed inside markdown code blocks (e.g. ```json ... ```)

    Args:
        rubric_raw: Raw string from inside <Rubric> tags.

    Returns:
        List of criterion dicts, or None if parsing fails.
    """
    if not rubric_raw:
        return None

    # Strip out any potential markdown code block syntax
    clean_json_str = re.sub(r"^```(?:json)?\s*\n|\n```\s*$", "", rubric_raw.strip(), flags=re.MULTILINE)

    try:
        data = json.loads(clean_json_str)
        return data.get("criteria", [])
    except json.JSONDecodeError as exc:
        logger.warning(
            "rubric_parse_failed",
            error=str(exc),
            raw_preview=clean_json_str[:200],
        )
        return None


async def fetch_exercise_content(lesson_id: str) -> ExerciseContent:
    """Fetch and parse exercise content from the GitHub content repo.

    This is the main entrypoint called by the grading worker.

    Args:
        lesson_id: The exercise lesson ID (e.g., "prml/1-exercise#1-1").

    Returns:
        ExerciseContent with question, solution, and rubric criteria.

    Raises:
        ExerciseFetchError: If content cannot be fetched or parsed.
    """
    file_path, exercise_id = _parse_lesson_id(lesson_id)

    if not exercise_id:
        raise ExerciseFetchError(
            f"lesson_id '{lesson_id}' has no exercise ID (expected format: page#id)"
        )

    logger.info(
        "fetching_exercise",
        lesson_id=lesson_id,
        file_path=file_path,
        exercise_id=exercise_id,
    )

    raw_mdx = await _fetch_file_from_github(file_path)
    parts = _extract_exercise_block(raw_mdx, exercise_id)
    rubric_criteria = _parse_rubric_json(parts["rubric_raw"])

    exercise = ExerciseContent(
        exercise_id=exercise_id,
        question=parts["question"],
        solution=parts["solution"] or None,
        rubric_criteria=rubric_criteria,
        grading_context=parts.get("grading_context"),
    )

    logger.info(
        "exercise_parsed",
        exercise_id=exercise_id,
        question_length=len(exercise.question),
        has_solution=bool(exercise.solution),
        rubric_count=len(rubric_criteria) if rubric_criteria else 0,
    )

    return exercise


@dataclass
class LessonContext:
    """Page-level context for the hint system (no per-exercise parsing)."""

    title: str | None = None
    grading_context: str | None = None
    content_body: str | None = None


async def fetch_lesson_context(lesson_id: str) -> LessonContext:
    """Fetch page-level lesson context for the hint system.

    Unlike fetch_exercise_content(), this extracts the full page context
    (title, grading_context, truncated body) without parsing individual
    exercise blocks. Used for the /ask hint endpoint.

    Args:
        lesson_id: The lesson page ID (e.g., "prml/1-exercise").
            If it contains '#', the exercise part is stripped.

    Returns:
        LessonContext with page-level metadata.

    Raises:
        ExerciseFetchError: If the content cannot be fetched.
    """
    # Strip exercise ID if present (hints are page-level)
    page_id = lesson_id.split("#")[0]
    file_path = f"note/{page_id}/index.mdx"

    logger.info("fetching_lesson_context", lesson_id=lesson_id, file_path=file_path)

    raw_mdx = await _fetch_file_from_github(file_path)

    title = _extract_frontmatter_field(raw_mdx, "title")
    grading_context = _extract_frontmatter_field(raw_mdx, "grading_context")

    # Extract content body (after frontmatter and imports, truncated)
    parts = raw_mdx.split("---", 2)
    content_body = None
    if len(parts) >= 3:
        body = parts[2].strip()
        # Skip import lines
        lines = body.split("\n")
        content_lines = [
            line for line in lines
            if not line.strip().startswith("import ")
        ]
        body = "\n".join(content_lines).strip()
        # Truncate to ~4000 chars to fit within LLM context
        if len(body) > 4000:
            body = body[:4000] + "\n\n[... lesson content truncated ...]"
        content_body = body

    return LessonContext(
        title=title,
        grading_context=grading_context,
        content_body=content_body,
    )

