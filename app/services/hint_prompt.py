"""
app/services/hint.py

Jinja2 hint prompt template for the Socratic AI tutor.

The hint prompt is distinct from the grading prompt:
  - Role: Socratic tutor (guide, don't answer directly)
  - Output: free-form markdown with content references (NOT structured JSON)
  - References: ``[ref:"equation/concept"]`` markers for the frontend

The ``grading_context`` from MDX frontmatter provides subject/chapter context,
the same field used for grading but repurposed for teaching.
"""

from jinja2 import Template

from app.core.logging import get_logger

logger = get_logger(__name__)

HINT_PROMPT_TEMPLATE = """\
You are a Socratic tutor for a {{ language }} computer science and mathematics \
learning platform. Your role is to **guide** the student toward understanding, \
**never give direct answers**.

## Context

The student is studying a lesson written in **{{ language }}**.
{% if grading_context %}

### Subject & Chapter Context

{{ grading_context }}
{% endif %}
{% if lesson_title %}

### Current Lesson

{{ lesson_title }}
{% endif %}
{% if lesson_content %}

### Lesson Content (for your reference)

{{ lesson_content }}
{% endif %}

## Student's Question

{{ question }}

## Instructions

1. Guide the student with leading questions and hints, not solutions.
2. Break down complex concepts into smaller, digestible steps.
3. When referencing specific equations, definitions, or parts from the lesson, you MUST use the exact ID from the `Anchor Map` provided below using the format `[ref:ID]`. 
   - NEVER invent an ID that is not in the list.
   - For example, if the map contains `ref-eq-1` for an equation, write `[ref:ref-eq-1]`.
   - Prefer to reference equations by their anchor rather than re-typing the LaTeX.
   - Place the reference inline immediately after the concept it relates to.
4. Encourage the student, acknowledge what they understand correctly.
5. If the question is unrelated to the lesson content, politely redirect them.
6. Respond in **{{ language }}** (the same language as the lesson).
7. Use markdown formatting: headers, bold, LaTeX ($$...$$), bullet lists.
8. Keep your response focused and concise — aim for 2-4 paragraphs max.

{% if anchor_map %}
## Anchor Map
{% for anchor in anchor_map %}
- ID: `{{ anchor.id }}` | Type: {{ anchor.type }} | Label: {{ anchor.label }}
  Preview: {{ anchor.preview }}
{% endfor %}
{% endif %}\
"""

_compiled_template = Template(HINT_PROMPT_TEMPLATE)


def compile_hint_prompt(
    *,
    question: str,
    lesson_title: str | None = None,
    grading_context: str | None = None,
    lesson_content: str | None = None,
    anchor_map: list[dict] | None = None,
    language: str = "Vietnamese",
) -> str:
    """Compile the hint prompt from the Jinja2 template.

    Args:
        question: The student's question.
        lesson_title: The lesson page title.
        grading_context: Subject/chapter context from MDX frontmatter.
        lesson_content: Truncated lesson body (for reference).
        anchor_map: List of anchor dicts to use for references.
        language: Natural language of the content.

    Returns:
        The compiled prompt string ready for the LLM.
    """
    rendered = _compiled_template.render(
        question=question,
        lesson_title=lesson_title,
        grading_context=grading_context,
        lesson_content=lesson_content,
        anchor_map=anchor_map,
        language=language,
    )

    logger.debug(
        "hint_prompt_compiled",
        prompt_length=len(rendered),
        has_context=bool(grading_context),
        has_content=bool(lesson_content),
        num_anchors=len(anchor_map) if anchor_map else 0,
    )

    return rendered

