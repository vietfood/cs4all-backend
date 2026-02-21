-- Migration: Add is_admin column to public.profiles
-- Phase 3 — enables admin role verification for review endpoints
-- Source of truth: docs/DATABASE.md

-- Add admin flag to profiles (defaults to false for all existing and new users)
alter table public.profiles
  add column if not exists is_admin boolean default false;

-- NOTE: To grant yourself admin access, run manually in Supabase SQL Editor:
--   UPDATE public.profiles SET is_admin = true WHERE id = 'your-user-uuid';
-- Never set is_admin via application code or frontend.

-- Prevent non-admin users from reading the is_admin column of other users
-- (optional security hardening — the RLS policies already restrict row access)
comment on column public.profiles.is_admin is
  'Admin flag. Set manually via SQL. Backend reads for admin endpoint auth.';
