"""
app/workers/__init__.py

Workers package — background task processing (Phase 3+).

This package will contain:
  - grading_worker.py: Consumes from the Redis grading queue, fetches the
    submission, compiles the prompt with the rubric, calls Langchain, and
    writes llm_score + llm_feedback back to Supabase.

Phase 2: This directory exists as a placeholder. The queue is written to by
the /api/v1/grade endpoint; consumption starts in Phase 3.
"""
