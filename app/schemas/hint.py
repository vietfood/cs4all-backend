"""
app/schemas/hint.py

Pydantic models for the LLM hint API endpoint.
"""

from pydantic import BaseModel, Field


class HintRequest(BaseModel):
    """Request body for POST /api/v1/hint."""

    lesson_id: str = Field(
        ...,
        description="Lesson page ID, e.g. 'prml/1-exercise'",
        min_length=1,
        max_length=200,
    )
    question: str = Field(
        ...,
        description="Student's question about the lesson content",
        min_length=1,
        max_length=2000,
    )
    anchor_map: list[dict] = Field(
        default_factory=list,
        description="List of anchor elements from the lesson containing id, label, type, and preview"
    )
