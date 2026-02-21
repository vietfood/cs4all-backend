# AGENTS.md — cs4all-backend
# Location: `cs4all-backend/AGENTS.md`
#
# MANDATORY: Read `docs/AGENTS.md` (one level up, at the monorepo root) FIRST
# before reading this file. This file only covers backend-specific conventions.
# Cross-repo contracts, shared prohibitions, and global state live in `docs/AGENTS.md`.

---

## 0. Mandatory Reading Order

1. `../docs/AGENTS.md` — global prohibitions, shared contracts, current phase
2. `../docs/DATABASE.md` — full schema; the backend owns writes to `llm_score`, `llm_feedback`, `reviewer_score`, `status`
3. `../docs/BACKEND.md` — tech stack decisions, Langchain integration, deployment strategy
4. `../docs/ARCHITECTURE.md` — system interaction flow (read before sweeping changes)
5. This file — backend-specific structure and conventions
6. Use DeepWiki MCP for documentation on FastAPI and Langchain if you are unsure about your code.

---

## 1. Current Phase

**Currently in: Phase 2 — Authentication & Progress Tracking**

The backend repository is scaffolded but has no active implementation work in Phase 2.
Phase 2 work is entirely frontend-side. The backend becomes the primary concern in Phase 3.

**Backend is active starting Phase 3. Pre-Phase 3 tasks for this repo:**
- [ ] Initialize FastAPI project structure (see Section 2).
- [ ] Set up environment variable handling (`python-dotenv` or similar).
- [ ] Confirm Supabase connectivity with Service Role Key.
- [ ] Confirm Redis connectivity for task queue.
- [ ] Implement `GET /api/v1/health` endpoint as a smoke test.

**Phase 3 implementation tasks:**
- [ ] `POST /api/v1/grade` — webhook receiver, enqueues submission ID to Redis.
- [ ] Celery/RQ worker — fetches submission, fetches rubric, compiles prompt.
- [ ] Langchain integration — orchestrate commercial LLM APIs with structured output parsers.
- [ ] Write `llm_score`, `llm_feedback`, update `status = 'ai_graded'` via Service Role Key.
- [ ] Admin review endpoints — safe side-by-side diff without leaking prompt internals.

---

## 2. Current State of the Codebase

The backend repo currently contains only the project scaffold (`.gitignore`, `LICENSE`, `README.md`).
No application code exists yet. This section will be updated as the implementation grows.

- **Framework**: FastAPI (Python)
- **Database Access**: `supabase-py` with Service Role Key (bypasses RLS — handle with care)
- **LLM Engine**: Langchain (orchestrating commercial LLM APIs; see `../docs/BACKEND.md` Section 2)
- **Task Queue**: Celery or RQ + Redis (decision to be finalized in Phase 3)
- **Package Manager**: Always use `uv` and new Python version (`>=3.11`). Document whichever is chosen here for future agents.

**Planned Directory Structure (to be built in Phase 3)**:
```text
cs4all-backend/
├── AGENTS.md                  # This file (backend-local)
├── app/
│   ├── main.py                # FastAPI app entrypoint
│   ├── api/
│   │   └── v1/
│   │       ├── grade.py       # POST /api/v1/grade — webhook receiver
│   │       └── health.py      # GET /api/v1/health
│   ├── workers/
│   │   └── grading_worker.py  # Celery/RQ task: fetch, prompt, call LLM via Langchain, write results
│   ├── services/
│   │   ├── supabase.py        # Supabase client initialization (Service Role Key)
│   │   ├── langchain_service.py # Langchain orchestrator and structured prompt logic
│   │   └── rubric.py          # Rubric fetching from GitHub or Supabase
│   └── schemas/
│       └── grading.py         # Pydantic models for webhook payload and Langchain response
├── .env                       # Secret keys — never commit. See Section 4.
├── .env.example               # Safe template — no real values.
├── requirements.txt           # Or pyproject.toml
├── README.md
└── .gitignore

../docs/                       # Cross-repo architecture documentation (read-only for agents)
├── AGENTS.md                  # Master orchestration file — READ FIRST
├── ARCHITECTURE.md
├── BACKEND.md
├── DATABASE.md
├── DEVELOPMENT.md
└── FRONTEND_PLAN.md
```

---

## 3. Backend-Specific Technical Guidelines

### 3.1 — Supabase Client
- Always initialize the Supabase client with the **Service Role Key**, never the anon key.
- The Service Role Key bypasses all RLS. Every database write must be intentional and minimal — only write the columns your current task requires.
- Never log the Service Role Key. Never include it in error messages or stack traces.
- The client should be a singleton initialized once at startup (`app/services/supabase.py`).

### 3.2 — Grading Worker Responsibilities (Phase 3)
The grading worker is the heart of this service. Its exact responsibilities in order:

1. Receive submission `id` from the Redis queue (enqueued by `POST /api/v1/grade`).
2. Fetch the full submission row from `public.exercise_submissions` using the Service Role Key.
3. Fetch the rubric for `lesson_id` from `vietfood/cs4all-content` via GitHub API, or from a synced Supabase table if background sync is implemented.
4. Compile the structured prompt: exercise instructions + rubric + user's `content`.
5. Call the Commercial LLM API via Langchain. Enforce the JSON output schema defined in `../docs/AGENTS.md` Section 4.3. Validate the response with Pydantic before writing anything to the DB.
6. On success: UPDATE `exercise_submissions` SET `llm_score`, `llm_feedback`, `status = 'ai_graded'`.
7. On failure: UPDATE `status` back to `'submitted'` (or a new `'grading_failed'` status if added — coordinate with frontend and update `../docs/AGENTS.md` Section 4.5 first).

### 3.3 — Langchain Integration
- Langchain must be configured to enforce the JSON output schema from `../docs/AGENTS.md` Section 4.3 via structured output parsers. Never write free-form LLM output directly to the database.
- Always validate the LLM's response against the Pydantic model in `app/schemas/grading.py` before any DB write.
- If the LLM returns a malformed response, log the raw output, do NOT write partial data to the DB, and mark the task for retry or manual review.
- The LLM API Key comes from `OPENAI_API_KEY` (or similar) in the environment. Never hardcode it.

### 3.4 — Admin Review Endpoints
- Admin endpoints must verify that the requesting user has an admin role before returning any data.
- These endpoints must never return raw prompt internals (the compiled prompt sent to the LLM) to the frontend. They return only the user's submission, the reference solution, and the LLM score + feedback. The frontend will decide how to render this.

### 3.5 — Error Handling Philosophy
- Every external call (Supabase, Langchain/LLM API, GitHub API, Redis) must be wrapped in try/except.
- A failed grading task must never silently leave a submission stuck in `'submitted'` status indefinitely. Implement retry logic or a dead-letter queue.
- Structured logging (JSON format preferred) so logs are parseable in Railway/Render dashboards.

### 3.6 — What This Repo Must Never Do
- Never read or write `cs4all-content` files directly — fetch from GitHub API or use the synced Supabase table.
- Never expose the Service Role Key in any API response, log line, or error payload.
- Never update `public.user_progress` directly as a side effect of grading. Progress updates are external to this worker. If this changes, update `../docs/AGENTS.md` Section 4 first.
- Never run git commands — see `../docs/AGENTS.md` Section 2.1.

---

## 4. Environment Variables

All secrets live in `.env` (never committed). Use `.env.example` as the canonical template.

```bash
# .env.example — copy to .env and fill in real values

SUPABASE_URL=                  # Same URL as frontend — safe to share between repos
SUPABASE_SERVICE_ROLE_KEY=     # NEVER share with frontend. Bypasses all RLS.

OPENAI_API_KEY=                # (Or ANTHROPIC_API_KEY) Secret key for commercial LLM API

REDIS_URL=                     # e.g. redis://localhost:6379/0

# Optional — if fetching rubrics directly from GitHub API
GITHUB_TOKEN=                  # Read-only PAT scoped to vietfood/cs4all-content
```

---

## 5. API Endpoints

These must stay in sync with `../docs/AGENTS.md` Section 4.6. Do not add, rename, or remove
endpoints without updating the master contracts file first.

| Method | Path             | Caller               | Purpose |
|--------|------------------|----------------------|---------|
| POST   | `/api/v1/grade`  | Supabase Webhook     | Validate webhook payload, enqueue `submission.id` to Redis |
| GET    | `/api/v1/health` | Monitoring / Frontend | Return status of FastAPI, LLM API, and Redis connectivity |

---

## 6. Phased Plan

### Phase 1 — Separate Content from Frontend (COMPLETED)
Not applicable to this repo.

### Phase 2 — Authentication & Progress Tracking (ACTIVE — frontend only)
Backend is in standby. Pre-Phase 3 scaffolding may be done now (see Section 1 checklist).

### Phase 3 — Exercise Submission & LLM-Assisted Grading (BACKEND PRIMARY PHASE)
**Goal:** Receive submissions via webhook, grade them via Langchain, write results back to Supabase.
- Implement `POST /api/v1/grade` and the full async grading worker pipeline.
- Implement Langchain constrained inference with Pydantic validation.
- Implement admin review endpoints.
- Optionally implement background rubric sync from `cs4all-content` to Supabase.

### Phase 4 — Leaderboard & Community Features
**Goal:** Support leaderboard queries and LLM-assisted hints.
- Expose endpoint for LLM-assisted hints (rubric + stuck point → hint, no answer leak).
- Support any backend queries the leaderboard feature requires.

---

## 7. Agent Mistake Log

*(No entries yet. Append below as mistakes occur.)*

---

## 8. Session Log

**2026-02-21**
- **What was worked on**: Rewrote the architectural plan to replace SGLang and local LLM inference with Langchain orchestrating commercial LLM APIs. Updated all documentation references including `DEVELOPMENT.md`, `BACKEND.md`, `ARCHITECTURE.md`, and the `AGENTS.md` orchestration files.
- **Decisions made**: Transitioned from a locally-hosted SGLang model to commercial LLM APIs via Langchain for structured output scoring, reducing infrastructure overhead while maintaining deterministic JSON grading.