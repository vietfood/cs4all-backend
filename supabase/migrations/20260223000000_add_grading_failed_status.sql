-- Migration: Add 'grading_failed' to exercise_submissions status check constraint
-- Phase 3.5: Allows the grading worker to mark permanently failed LLM grading attempts.

-- Drop the old CHECK constraint and recreate with the new value
ALTER TABLE public.exercise_submissions
  DROP CONSTRAINT IF EXISTS exercise_submissions_status_check;

ALTER TABLE public.exercise_submissions
  ADD CONSTRAINT exercise_submissions_status_check
  CHECK (status IN ('submitted', 'ai_graded', 'human_reviewed', 'grading_failed'));
