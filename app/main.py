"""
app/main.py

FastAPI application entrypoint.

Startup sequence (via lifespan):
  1. Logging is configured (JSON in prod, coloured console in dev).
  2. Supabase client is created and connectivity-probed (fails fast on bad creds).
  3. Redis connection pool is created and PINGed (fails fast if unreachable).
  4. Both clients are stored on ``app.state`` for injection into endpoints.

Shutdown sequence (via lifespan):
  1. Redis connection pool is gracefully closed.

Environment variables are loaded by Pydantic Settings from ``.env`` — there
is no ``load_dotenv()`` call here. Do not add one.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.services.redis_client import close_redis, init_redis
from app.services.supabase import init_supabase


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown of all external service connections.

    FastAPI guarantees this runs before the first request and after the last.
    Placing all I/O here (rather than at import time) means:
      - Test isolation: each test can spin up a fresh app with its own state.
      - Fail fast: a bad credential or unreachable service aborts startup cleanly.
      - No singletons polluting module globals.
    """
    settings = get_settings()

    # ── Startup ───────────────────────────────────────────────────────────────
    setup_logging(environment=settings.environment)
    logger = get_logger(__name__)

    logger.info(
        "app_startup",
        version=settings.app_version,
        environment=settings.environment,
    )

    app.state.supabase = await init_supabase()
    app.state.redis = await init_redis()

    logger.info("app_ready", message="All services connected. Accepting requests.")

    yield  # ← application serves requests here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("app_shutdown", message="Shutting down gracefully...")
    await close_redis(app.state.redis)
    logger.info("app_stopped")


def create_app() -> FastAPI:
    """Application factory.

    Returns a configured FastAPI instance. Separating creation from the module
    global makes the app importable without side effects (useful for testing).
    """
    settings = get_settings()

    app = FastAPI(
        title="cs4all Backend",
        description=(
            "AI grading and progress-tracking backend for the cs4all Vietnamese CS learning platform. "
            "Receives exercise submissions via Supabase webhook, enqueues them for async LLM grading, "
            "and exposes admin review endpoints."
        ),
        version=settings.app_version,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Restrict to the frontend origin in production.
    # In development, allow all origins so the Astro dev server at any port works.
    origins = (
        ["*"]
        if not settings.is_production
        else [
            # TODO (Phase 3): Replace with the actual production Astro URL.
            "https://cs4all.vn",
        ]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    from app.api.v1 import admin, grade, health  # noqa: PLC0415  (deferred import avoids circular deps at configure time)

    app.include_router(health.router, prefix="/api/v1", tags=["Health"])
    app.include_router(grade.router, prefix="/api/v1", tags=["Grading"])
    app.include_router(admin.router, prefix="/api/v1", tags=["Admin"])

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": "cs4all-backend",
            "version": settings.app_version,
            "docs": "/docs",
        }

    return app


# Module-level app instance — used by uvicorn: ``uvicorn app.main:app``
app = create_app()
