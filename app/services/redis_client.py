"""
app/services/redis_client.py

Redis connection pool — lifecycle managed by FastAPI's lifespan.

The client is stored on ``app.state.redis`` after startup and torn down
cleanly at shutdown. Endpoints that need Redis should use the ``get_redis``
dependency rather than importing the global (which doesn't exist anymore).

Usage:
    from fastapi import Depends, Request
    from app.services.redis_client import get_redis
    import redis.asyncio as aioredis

    @router.post("/example")
    async def example(redis: aioredis.Redis = Depends(get_redis)):
        await redis.lpush("some_queue", "value")
"""

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def init_redis() -> aioredis.Redis:
    """Create an async Redis connection pool and verify connectivity.

    Called once during application startup (lifespan). Performs a PING
    to fail fast if Redis is unreachable.

    Returns:
        An initialized, ping-verified ``redis.asyncio.Redis`` instance.

    Raises:
        RuntimeError: If Redis cannot be reached at startup.
    """
    settings = get_settings()
    redis_url = settings.redis_url

    logger.info("redis_init_start", url=redis_url)

    client: aioredis.Redis = aioredis.from_url(
        redis_url,
        decode_responses=True,
        # Health-check interval keeps the pool from going stale on long idle periods.
        health_check_interval=30,
    )

    try:
        pong = await client.ping()
        if not pong:
            raise ConnectionError("PING returned falsy response")
        logger.info("redis_connected", url=redis_url)
    except Exception as exc:
        logger.error("redis_connection_failed", url=redis_url, error=str(exc))
        raise RuntimeError(f"Failed to connect to Redis at {redis_url!r}: {exc}") from exc

    return client


async def close_redis(client: aioredis.Redis) -> None:
    """Gracefully close the Redis connection pool at shutdown."""
    await client.aclose()
    logger.info("redis_closed")


def get_redis(request) -> aioredis.Redis:  # type: ignore[type-arg]
    """FastAPI dependency that retrieves the Redis client from app state.

    Usage:
        from fastapi import Depends, Request
        from app.services.redis_client import get_redis

        @router.post("/example")
        async def example(redis: aioredis.Redis = Depends(get_redis)):
            await redis.lpush("grading_queue", submission_id)
    """
    return request.app.state.redis
