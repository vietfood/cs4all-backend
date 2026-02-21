-- Migration: Create public.exercise_submissions table
-- Phase 3 prep — tracks submissions, LLM scores, and human review
-- Source of truth: docs/DATABASE.md
-- Backend writes: llm_score, llm_feedback, reviewer_score, status (after 'submitted')
-- Frontend writes: user_id, lesson_id, content, status='submitted'

create table if not exists public.exercise_submissions (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users on delete cascade not null,
  lesson_id text not null,        -- e.g. '1-exercise'

  -- The raw payload from the user (could be LaTeX, Markdown, or plain text)
  content text not null,

  -- Phase 3.5: AI Review — BACKEND ONLY writes these columns
  llm_score integer check (llm_score >= 0 and llm_score <= 100),
  llm_feedback jsonb,             -- Detailed per-criterion feedback (see docs/AGENTS.md Section 4.3)

  -- Phase 3: Human Review — BACKEND ONLY writes these columns
  reviewer_score integer check (reviewer_score >= 0 and reviewer_score <= 100),

  -- Computed: final_score = reviewer_score if set, otherwise llm_score
  final_score integer generated always as (
    coalesce(reviewer_score, llm_score)
  ) stored,

  -- Status flow: 'submitted' -> 'ai_graded' -> 'human_reviewed'
  -- See docs/AGENTS.md Section 4.5 for the authoritative state machine.
  status text check (status in ('submitted', 'ai_graded', 'human_reviewed')) default 'submitted',

  submitted_at timestamp with time zone default timezone('utc'::text, now()) not null,
  reviewed_at timestamp with time zone
);

-- RLS: Enable Row Level Security
alter table public.exercise_submissions enable row level security;

-- Policy: Users can insert their own submissions (status must be 'submitted')
create policy "Users can submit exercises"
  on public.exercise_submissions for insert
  with check (
    auth.uid() = user_id
    and status = 'submitted'
  );

-- Policy: Users can read their own submissions (to see scores and feedback)
create policy "Users can view their own submissions"
  on public.exercise_submissions for select
  using (auth.uid() = user_id);

-- NOTE: UPDATE is intentionally NOT granted to users via RLS.
-- Only the backend (using the Service Role Key, which bypasses RLS) can:
--   - Write llm_score, llm_feedback, reviewer_score
--   - Change status from 'submitted' -> 'ai_graded' -> 'human_reviewed'
-- This is a critical security boundary. See docs/AGENTS.md Section 2.4.

-- Indexes for common query patterns
create index if not exists idx_submissions_user_id on public.exercise_submissions (user_id);
create index if not exists idx_submissions_status on public.exercise_submissions (status);
create index if not exists idx_submissions_lesson on public.exercise_submissions (lesson_id);
