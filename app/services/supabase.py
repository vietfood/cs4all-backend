"""
app/services/supabase.py

Supabase client — singleton lifecycle managed by FastAPI's lifespan.

Rules (from cs4all-backend/AGENTS.md Section 3.1):
  - Always use the Service Role Key. Never the anon key.
  - The client is a singleton: created once at startup, torn down at shutdown.
  - Never log the Service Role Key. Never include it in error messages.
  - Every DB write must be intentional and minimal.

Usage:
    # Inside an endpoint or dependency:
    from fastapi import Request
    def my_endpoint(request: Request):
        client = request.app.state.supabase

    # Or inject via the FastAPI dependency:
    from app.services.supabase import get_supabase
"""

from supabase import Client, create_client

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def init_supabase() -> Client:
    """Create and validate the Supabase client.

    Called once during application startup (lifespan). Performs a lightweight
    connectivity probe to fail fast if the credentials are wrong or the project
    is unreachable.

    Returns:
        An initialized, connectivity-verified ``supabase.Client``.

    Raises:
        RuntimeError: If the client cannot connect to the Supabase project.
    """
    settings = get_settings()

    logger.info("supabase_init_start", url=settings.supabase_url)

    client: Client = create_client(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_service_role_key,
    )

    # Connectivity probe: attempt a minimal SELECT against a system view.
    # This will raise if the URL is wrong or the key is invalid.
    try:
        # Supabase exposes pg_catalog.pg_tables via the REST API.
        # A zero-row limit means virtually no data is transferred.
        probe = client.table("user_progress").select("id").limit(1).execute()
        _ = probe  # result not used; we only care that it didn't raise.
        logger.info("supabase_connected")
    except Exception as exc:
        # Log without the key. The URL is safe to log; the key is not.
        logger.error(
            "supabase_connection_failed",
            url=settings.supabase_url,
            error=str(exc),
        )
        raise RuntimeError(
            f"Failed to connect to Supabase at {settings.supabase_url!r}: {exc}"
        ) from exc

    return client


def get_supabase(request) -> Client:  # type: ignore[type-arg]
    """FastAPI dependency that retrieves the Supabase client from app state.

    Usage:
        from fastapi import Depends, Request
        from app.services.supabase import get_supabase

        @router.get("/example")
        async def example(supabase: Client = Depends(get_supabase)):
            ...
    """
    return request.app.state.supabase
