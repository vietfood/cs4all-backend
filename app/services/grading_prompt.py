"""
app/services/prompt.py

Modular Jinja2 grading prompt for the LLM grader.

Prompt structure:
    CONTEXT  — base role + language + optional frontmatter grading_context
    QUESTION — the exercise question from MDX
    RUBRIC   — grading criteria from <Rubric hidden> component
    SUBMISSION — the student's submitted solution
    REFERENCE  — the reference solution from <Solution> (for context only)
    INSTRUCTIONS — grading rules and output format

The grading_context field is optional and comes from the MDX page's
frontmatter. Content authors use it to provide subject/chapter-specific
instructions to the LLM grader (e.g., notation conventions, accepted
proof techniques, common pitfalls).

Rules:
    - The prompt is always in English; the `language` field tells the LLM
      what language the exercise content is in.
    - Keep the prompt tightly constrained to prevent hallucination.
    - The template is user-editable — modify GRADING_PROMPT_TEMPLATE below.
"""

from jinja2 import Template

from app.core.logging import get_logger

logger = get_logger(__name__)

GRADING_PROMPT_TEMPLATE = """\
You are a rigorous but fair academic grader for a computer science and mathematics \
learning platform. Your task is to grade a student's exercise submission.

## Context

This exercise is written in **{{ language }}**. The student's submission is also \
in {{ language }}. Grade based on mathematical/logical correctness, not language quality.
{% if grading_context %}

### Subject & Chapter Context

{{ grading_context }}
{% endif %}

## Exercise Question

{{ question_text }}

{% if rubric_criteria %}
## Grading Rubric

Grade the submission against EACH of the following criteria. A student may use \
any valid mathematical approach (e.g., matrix notation vs summation notation, \
different proof strategies) — award full points if the result is mathematically \
equivalent and correct, regardless of the specific method used.

{% for criterion in rubric_criteria %}
- **{{ criterion.points }} point(s)**: {{ criterion.description }}
{% endfor %}
{% endif %}

## Student's Submission

{{ user_submission }}

{% if reference_solution %}
## Reference Solution (for your context only — do NOT penalize alternative valid approaches)

{{ reference_solution }}
{% endif %}

## Instructions

1. Evaluate the student's work against each rubric criterion independently.
2. For each criterion, determine how many points to award (0 to max).
3. A student's approach may differ from the reference solution — this is acceptable \
   if it is mathematically valid and arrives at a correct result.
4. Be specific in your comments: cite exactly where the student's reasoning is \
   correct or where it breaks down.
5. Compute an overall score as a percentage (0–100) based on total points awarded \
   vs total points possible.

Return your evaluation as structured JSON.\
"""

_compiled_template = Template(GRADING_PROMPT_TEMPLATE)


def compile_grading_prompt(
    *,
    question_text: str,
    rubric_criteria: list[dict] | None = None,
    reference_solution: str | None = None,
    user_submission: str,
    language: str = "Vietnamese",
    grading_context: str | None = None,
) -> str:
    """Compile the grading prompt from the Jinja2 template.

    Args:
        question_text: The exercise question (raw text/LaTeX).
        rubric_criteria: List of {"points": int, "description": str} dicts.
        reference_solution: The reference solution (raw text/LaTeX). Optional.
        user_submission: The user's submitted solution.
        language: The natural language of the exercise content.
        grading_context: Optional subject/chapter-specific context from
            the MDX page's frontmatter ``grading_context`` field.

    Returns:
        The compiled prompt string ready for the LLM.
    """
    rendered = _compiled_template.render(
        language=language,
        grading_context=grading_context,
        question_text=question_text,
        rubric_criteria=rubric_criteria or [],
        reference_solution=reference_solution,
        user_submission=user_submission,
    )

    logger.debug(
        "prompt_compiled",
        prompt_length=len(rendered),
        has_rubric=bool(rubric_criteria),
        has_reference=bool(reference_solution),
        has_context=bool(grading_context),
    )

    return rendered
