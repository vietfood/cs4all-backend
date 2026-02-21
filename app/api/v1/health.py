"""
app/api/v1/health.py

GET /api/v1/health — Backend and dependency connectivity check.

Returns HTTP 200 if all critical services are reachable.
Returns HTTP 503 if any critical service (Supabase, Redis) is down, so
monitoring tools and load balancers correctly detect failures.

Contract defined in docs/AGENTS.md Section 4.6.
"""

import redis.asyncio as aioredis
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from supabase import Client

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.grading import HealthResponse, ServiceStatus

logger = get_logger(__name__)
router = APIRouter()


async def _probe_supabase(client: Client) -> ServiceStatus:
    """Attempt a real SELECT against Supabase to verify live connectivity.

    Returns ServiceStatus with status="ok" on success, "error" on failure.
    We do NOT rely on whether the client object exists — we verify it can
    actually execute a query.
    """
    try:
        # Minimal-cost query: single row from user_progress with no RLS impact
        # (Service Role Key bypasses RLS). Limit 0 would return no rows.
        client.table("user_progress").select("id").limit(1).execute()
        return ServiceStatus(status="ok")
    except Exception as exc:
        logger.warning("health_supabase_probe_failed", error=str(exc))
        return ServiceStatus(status="error", detail="Supabase query failed")


async def _probe_redis(redis: aioredis.Redis) -> ServiceStatus:
    """Issue an async PING to verify Redis is reachable."""
    try:
        pong = await redis.ping()
        if pong:
            return ServiceStatus(status="ok")
        return ServiceStatus(status="error", detail="PING returned falsy response")
    except Exception as exc:
        logger.warning("health_redis_probe_failed", error=str(exc))
        return ServiceStatus(status="error", detail=str(exc))


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Backend health check",
    description=(
        "Verifies that the FastAPI application, Supabase, and Redis are all reachable. "
        "Returns HTTP 503 if any critical service is unavailable."
    ),
)
async def health_check(request: Request) -> JSONResponse:
    """Run live connectivity probes against all backend dependencies."""
    settings = get_settings()

    supabase_client: Client = request.app.state.supabase
    redis_client: aioredis.Redis = request.app.state.redis

    supabase_status = await _probe_supabase(supabase_client)
    redis_status = await _probe_redis(redis_client)

    all_ok = supabase_status.status == "ok" and redis_status.status == "ok"
    overall = "ok" if all_ok else "degraded"

    response = HealthResponse(
        status=overall,
        version=settings.app_version,
        environment=settings.environment,
        supabase=supabase_status,
        redis=redis_status,
    )

    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE

    logger.info(
        "health_check",
        overall=overall,
        supabase=supabase_status.status,
        redis=redis_status.status,
    )

    return JSONResponse(
        content=response.model_dump(),
        status_code=http_status,
    )
