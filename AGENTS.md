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

**Currently in: Phase 3 — Exercise Submission & Human Review Workflow**

**Phase 2 tasks for this repo (Backend MVP Setup): ✅ COMPLETE**
- [x] Initialize FastAPI project structure with `uv` + `pyproject.toml`.
- [x] Set up fail-fast env var validation via Pydantic Settings v2 (`app/core/config.py`).
- [x] Set up structured logging via structlog (`app/core/logging.py`).
- [x] Validate Supabase connectivity with Service Role Key in lifespan startup.
- [x] Validate Redis connectivity with async PING in lifespan startup.
- [x] Implement `GET /api/v1/health` with live probes (returns 503 on failure).
- [x] Implement `POST /api/v1/grade` with HMAC signature guard and Redis LPUSH.
- [x] Define full Pydantic schemas for webhook payload and grading response.
- [x] Create `app/workers/` stub for Phase 3 grading worker.

**Phase 3 implementation tasks: ✅ COMPLETE**
- [x] Implement `grading_worker.py`: BRPOP → fetch submission → validate status → log for review queue.
- [x] Implement `app/core/auth.py`: JWT validation + `profiles.is_admin` check.
- [x] Implement admin review endpoints: list, view, review with status guards.
- [x] Migration: `is_admin` column on `profiles`.

**Phase 3.5 implementation tasks (LLM Integration):**
- [ ] Langchain integration — orchestrate commercial LLM APIs with structured output parsers.
- [ ] Background rubric-fetching from `vietfood/cs4all-content` via GitHub API (`GITHUB_TOKEN`).
- [ ] Write `llm_score`, `llm_feedback`, update `status = 'ai_graded'` via Service Role Key.

---

## 2. Current State of the Codebase

**Framework**: FastAPI (Python ≥ 3.13)
**Package manager**: `uv` — always use `uv add`, `uv run`, `uv sync`. Never `pip`.
**Database Access**: `supabase-py` with Service Role Key (bypasses RLS — handle with care).
**LLM Engine**: Langchain (Phase 3.5 — not yet wired up).
**Task Queue**: `redis` (async) with a simple LPUSH/BRPOP queue. Celery/RQ decision deferred to Phase 3.
**Logging**: `structlog` — JSON in prod, coloured console in dev. Never use `print()`.

### Actual Directory Structure

```text
cs4all-backend/
├── AGENTS.md                      # This file
├── .env                           # Secret keys — never commit
├── .env.example                   # Safe template — copy to .env
├── pyproject.toml                 # uv project config + dependencies
├── uv.lock                        # Lockfile — commit this
│
├── app/
│   ├── main.py                    # create_app() factory + lifespan context manager
│   ├── __init__.py
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Pydantic Settings v2 — get_settings() singleton
│   │   └── logging.py             # structlog setup — get_logger(), setup_logging()
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── health.py          # GET /api/v1/health — live Supabase + Redis probes
│   │       └── grade.py           # POST /api/v1/grade — webhook receiver + Redis enqueue
│   │
│   ├── workers/
│   │   ├── __init__.py
│   │   └── grading_worker.py      # Phase 3 stub — BRPOP → fetch → grade → write back
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── supabase.py            # init_supabase(), get_supabase() — lifespan-managed
│   │   └── redis_client.py        # init_redis(), get_redis(), close_redis() — async
│   │
│   └── schemas/
│       ├── __init__.py
│       └── grading.py             # SupabaseWebhookPayload, GradingResponse, HealthResponse
│
├── supabase/
│   └── migrations/                # SQL migration files — run manually, never by app code
│       ├── 20260221000000_create_profiles.sql
│       ├── 20260221000001_create_user_progress.sql
│       └── 20260221000002_create_exercise_submissions.sql
│
└── docs/                          # Cross-repo docs (read-only for agents)
    ├── AGENTS.md, BACKEND.md, DATABASE.md, DEVELOPMENT.md, ARCHITECTURE.md
```

---

## 3. Backend-Specific Technical Guidelines

### 3.1 — Supabase Client
- Always initialize the Supabase client with the **Service Role Key**, never the anon key.
- The Service Role Key bypasses all RLS. Every database write must be intentional and minimal — only write the columns your current task requires.
- Never log the Service Role Key. Never include it in error messages or stack traces.
- The client is stored on `app.state.supabase` (set in lifespan). Access it via `request.app.state.supabase` or the `get_supabase()` dependency.

### 3.2 — Grading Worker Responsibilities (Phase 3)
The grading worker is the heart of this service. Its exact responsibilities in order:

1. BRPOP a submission `id` from the Redis queue (`cs4all:grading_queue`).
2. Fetch the full submission row from `public.exercise_submissions` using the Service Role Key.
3. Fetch the rubric for `lesson_id` from `vietfood/cs4all-content` via GitHub API, or from a synced Supabase table if background sync is implemented.
4. Compile the structured prompt: exercise instructions + rubric + user's `content`.
5. Call the Commercial LLM API via Langchain. Enforce the JSON output schema defined in `../docs/AGENTS.md` Section 4.3. Validate the response with `GradingResponse` Pydantic model before writing anything to the DB.
6. On success: UPDATE `exercise_submissions` SET `llm_score`, `llm_feedback`, `status = 'ai_graded'`.
7. On failure: log the raw LLM output, do NOT write partial data to the DB, mark for retry or set `status = 'grading_failed'` (coordinate with frontend and update `../docs/AGENTS.md` Section 4.5 first).

### 3.3 — Langchain Integration
- Langchain must be configured to enforce the JSON output schema from `../docs/AGENTS.md` Section 4.3 via structured output parsers. Never write free-form LLM output directly to the database.
- Always validate the LLM's response against `GradingResponse` in `app/schemas/grading.py` before any DB write.
- If the LLM returns a malformed response, log the raw output, do NOT write partial data to the DB, and mark the task for retry or manual review.
- The LLM API Key comes from `GEMINI_API_KEY` (or `OPENAI_API_KEY`) in the environment. Never hardcode it.

### 3.4 — Admin Review Endpoints (Phase 3)
- Admin endpoints must verify that the requesting user has an admin role before returning any data.
- These endpoints must never return raw prompt internals (the compiled prompt sent to the LLM) to the frontend. They return only the user's submission, the reference solution, and the LLM score + feedback.

### 3.5 — Error Handling Philosophy
- Every external call (Supabase, Langchain/LLM API, GitHub API, Redis) must be wrapped in try/except.
- A failed grading task must never silently leave a submission stuck in `'submitted'` status indefinitely. Implement retry logic or a dead-letter queue.
- Use `structlog` for all logging. JSON format is parseable by Railway/Render dashboards.

### 3.6 — Lifecycle Rules
- **Never create services at module import time.** Use lifespan. This prevents import-time side effects that break test isolation and make debugging impossible.
- **Never use `print()`.** Always use `get_logger(__name__)` from `app.core.logging`.
- **Never call `load_dotenv()`.** Pydantic Settings handles this via `SettingsConfigDict(env_file=".env")`.

### 3.7 — What This Repo Must Never Do
- Never read or write `cs4all-content` files directly — fetch from GitHub API or use the synced Supabase table.
- Never expose the Service Role Key in any API response, log line, or error payload.
- Never update `public.user_progress` directly as a side effect of grading. If this changes, update `../docs/AGENTS.md` Section 4 first.
- Never run git commands — see `../docs/AGENTS.md` Section 2.1.

### 3.8 — Database Interaction Rules

The backend interacts with Supabase via the `supabase-py` client (Service Role Key, bypasses RLS). See `docs/DATABASE.md` Section 5 for full code examples.

**What is allowed:**
- `SELECT` — read submissions, progress, profiles as needed for grading/admin tasks
- `INSERT` — only when the backend itself needs to create records (rare)
- `UPDATE` — write `llm_score`, `llm_feedback`, `reviewer_score`, `status`, `reviewed_at` on `exercise_submissions`

**What is NOT allowed:**
- **Never run DDL** (`CREATE TABLE`, `ALTER TABLE`, `DROP`) in application code. Schema changes live in `supabase/migrations/` as SQL files.
- **Never write `user_progress`** as a grading side effect
- **Never write frontend-owned columns** (`content`, `submitted_at`) after initial insert
- **Always validate data** with Pydantic models before writing to the DB

**Migration workflow:**
1. Write a new `.sql` file in `supabase/migrations/` with a timestamp prefix
2. Update `docs/DATABASE.md` with the new schema
3. Human runs migrations via `supabase db push`, psql, or Supabase Dashboard
4. Agent never runs migrations — this is a human-only operation

### 3.9 — Cross-Repo Documentation Maintenance

After any API change (new endpoint, modified schema, renamed path, changed auth), you MUST update:

1. **`cs4all-frontend/docs/BACKEND_USAGE.md`** — the frontend developer guide with endpoint reference, code examples, and TypeScript types.
2. **Root `AGENTS.md` Section 4.6** — the shared endpoint contract table.
3. **This file, Section 5** — the backend endpoint table.
4. **`docs/BACKEND.md`** — the backend architecture doc.

---

## 4. Environment Variables

All secrets live in `.env` (never committed). Use `.env.example` as the canonical template.

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=    # NEVER share. Bypasses RLS.

REDIS_URL=redis://localhost:6379/0

WEBHOOK_SECRET=               # HMAC secret shared with Supabase (Phase 3+, required in prod)
GITHUB_TOKEN=                 # Read-only PAT for vietfood/cs4all-content (Phase 3.5+)

GEMINI_API_KEY=               # (or OPENAI_API_KEY, ANTHROPIC_API_KEY) LLM key (Phase 3.5+)

ENVIRONMENT=development       # "development" | "production"
```

---

## 5. API Endpoints

These must stay in sync with `../docs/AGENTS.md` Section 4.6. Do not add, rename, or remove
endpoints without updating the master contracts file first.

| Method | Path                                    | Caller               | Purpose |
|--------|-----------------------------------------|----------------------|---------|
| GET    | `/api/v1/health`                        | Monitoring / Frontend | Live probes: Supabase query + Redis PING. Returns 503 on failure. |
| POST   | `/api/v1/grade`                         | Supabase Webhook     | Validate payload, optional HMAC check, LPUSH to `cs4all:grading_queue`. |
| GET    | `/api/v1/admin/submissions`             | Admin Frontend       | List submissions (filterable by status, paginated). Requires admin JWT. |
| GET    | `/api/v1/admin/submissions/{id}`        | Admin Frontend       | View single submission detail. Requires admin JWT. |
| POST   | `/api/v1/admin/submissions/{id}/review` | Admin Frontend       | Assign reviewer_score, set status='human_reviewed'. Requires admin JWT. |

---

## 6. How to Run

```bash
cd cs4all-backend

# Install / sync dependencies
uv sync

# Start dev server (reloads on file changes)
uv run uvicorn app.main:app --reload --port 8000

# Hit health endpoint
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

---

## 7. Phased Plan

### Phase 1 — Separate Content from Frontend (COMPLETED)
Not applicable to this repo.

### Phase 2 — Auth, Progress Tracking & Backend MVP Setup ✅ COMPLETE
- FastAPI project with full lifespan-managed service connections.
- Pydantic Settings v2 for fail-fast env validation.
- structlog JSON logging.
- Real connectivity probes in health check.
- Webhook receiver with HMAC guard and Redis queue.
- Full Pydantic schemas for all API contracts.

### Phase 3 — Exercise Submission & Human Review Workflow
**Goal:** Consume the grading queue, store submissions, provide admin review panel (no LLM).
- Implement `grading_worker.py`: consume from `cs4all:grading_queue`.
- Implement admin review endpoints with role verification.

### Phase 3.5 — LLM-Assisted Grading Integration
**Goal:** Introduce Langchain automatic grading before human review.
- Integrate Langchain with structured output parsers and `GradingResponse` validation.
- Optionally sync rubrics from `cs4all-content` to Supabase.

### Phase 4 — Leaderboard & Community Features
**Goal:** Support leaderboard queries and LLM-assisted hints.
- Expose hint endpoint (rubric + stuck point → hint, no answer leak).
---

## 8. Agent Mistake Log

*(No entries yet. Append below as mistakes occur.)*

---

## 9. Session Log

**2026-02-21 — Session 1**
- **What was worked on**: Phase 2 initial scaffolding (FastAPI main, health, grade stub, supabase/redis services).
- **Files created**: `app/main.py`, `app/api/v1/health.py`, `app/api/v1/grade.py`, `app/services/supabase.py`, `app/services/redis_client.py`, `app/schemas/grading.py`.
- **Decisions made**: Redis setup pulled into Phase 2. Webhook payload uses `SupabaseWebhookPayload` Pydantic schema.

**2026-02-21 — Session 2**
- **What was worked on**: Phase 2 backend rewrite — replaced naive scaffold with production-quality code.
- **Files created/modified**:
  - NEW: `app/core/__init__.py`, `app/core/config.py`, `app/core/logging.py`
  - NEW: `app/workers/__init__.py`, `app/workers/grading_worker.py` (Phase 3 stub)
  - REWRITTEN: `app/main.py` (lifespan, app factory, CORS), `app/services/supabase.py`, `app/services/redis_client.py` (now async), `app/api/v1/health.py` (live probes, 503), `app/api/v1/grade.py` (HMAC, Redis LPUSH), `app/schemas/grading.py` (full contract schemas)
  - UPDATED: `pyproject.toml` (pydantic-settings, structlog, redis[hiredis], httpx, dev deps), `.env.example`
- **Decisions made**:
  - Switched to `redis.asyncio` for full async compatibility with FastAPI.
  - `supabase.Client` is sync (supabase-py v2 does not yet have a stable async client); wrapped in lifespan normally.
  - `WEBHOOK_SECRET` is optional in Phase 2 (dev), enforced in Phase 3 production.
  - `/docs` and `/redoc` are disabled in `ENVIRONMENT=production`.
- **Blockers**: None. Phase 3 work can begin on the grading worker.

**2026-02-21 — Session 3**
- **What was worked on**: Database migrations and documentation.
- **Files created**:
  - `supabase/migrations/20260221000000_create_profiles.sql` (with auto-create trigger on signup)
  - `supabase/migrations/20260221000001_create_user_progress.sql` (with RLS + index)
  - `supabase/migrations/20260221000002_create_exercise_submissions.sql` (with computed final_score, strict RLS, indexes)
- **Files updated**: `docs/DATABASE.md` (added migration instructions and backend DB interaction code examples), `cs4all-backend/AGENTS.md` (added Section 3.8 — Database Interaction Rules, updated directory structure)
- **Decisions made**: Migration SQL files live in `cs4all-backend/supabase/migrations/`. Agents never run migrations — human-only operation via CLI/Dashboard/psql.
- **Blockers**: None.

**2026-02-21 — Session 4**
- **What was worked on**: Phase 3 — Exercise Submission & Human Review Workflow.
- **Files created**:
  - `app/core/auth.py` (admin JWT verification: `supabase.auth.get_user()` + `profiles.is_admin` check)
  - `app/schemas/admin.py` (ReviewRequest, SubmissionDetail, SubmissionListResponse, ReviewResponse)
  - `app/api/v1/admin.py` (GET list, GET detail, POST review with status guards)
  - `supabase/migrations/20260221000003_add_is_admin_to_profiles.sql`
- **Files modified**:
  - `app/workers/grading_worker.py` (full BRPOP consumer: fetch → validate → log)
  - `app/main.py` (registered admin router)
- **Decisions made**:
  - Admin auth uses `supabase.auth.get_user(token)` for JWT validation (server-side, handles revocation).
  - `is_admin` boolean column on `profiles` — set manually via Dashboard/SQL.
  - Status guard on review: rejects already-reviewed submissions (409 Conflict).
  - Worker in Phase 3 logs for review queue only; Langchain grading deferred to Phase 3.5.
- **Blockers**: None. Phase 3.5 (LLM grading via Langchain) can begin.