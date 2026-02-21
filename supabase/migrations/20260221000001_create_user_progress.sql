-- Migration: Create public.user_progress table
-- Phase 2 — tracks lesson completion per user
-- Source of truth: docs/DATABASE.md

create table if not exists public.user_progress (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users on delete cascade not null,
  lesson_id text not null,        -- e.g. '1-2/gaussian'
  status text check (status in ('in_progress', 'completed')) not null default 'completed',
  completed_at timestamp with time zone default timezone('utc'::text, now()) not null,

  -- A user can only have one progress entry per lesson
  unique(user_id, lesson_id)
);

-- RLS: Enable Row Level Security
alter table public.user_progress enable row level security;

-- Policy: Users can read their own progress
create policy "Users can view their own progress"
  on public.user_progress for select
  using (auth.uid() = user_id);

-- Policy: Users can insert their own progress
create policy "Users can insert their own progress"
  on public.user_progress for insert
  with check (auth.uid() = user_id);

-- Policy: Users can update their own progress (e.g. in_progress -> completed)
create policy "Users can update their own progress"
  on public.user_progress for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Index for fast per-user lookups (the frontend queries progress by user)
create index if not exists idx_user_progress_user_id on public.user_progress (user_id);
