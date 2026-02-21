"""
app/core/auth.py

Admin authentication dependency for FastAPI.

Validates the Supabase JWT from the ``Authorization: Bearer <token>`` header,
then checks ``profiles.is_admin == true`` for the authenticated user.

Usage:
    from app.core.auth import require_admin, AdminUser

    @router.get("/admin/submissions")
    async def list_submissions(admin: AdminUser = Depends(require_admin)):
        # admin.user_id and admin.email are available
        ...

Security notes:
  - Uses Supabase's own ``auth.get_user(token)`` to validate the JWT.
    This performs a real API call to Supabase Auth, which verifies the token's
    signature, expiry, and revocation status.
  - The ``is_admin`` check is a second DB query against ``public.profiles``.
  - Never log the raw JWT token.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.logging import get_logger

logger = get_logger(__name__)

# HTTPBearer extracts the token from "Authorization: Bearer <token>"
_bearer_scheme = HTTPBearer(
    description="Supabase JWT — obtained by signing in via the frontend",
    auto_error=True,  # Returns 403 if header is missing
)


@dataclass(frozen=True)
class AdminUser:
    """Represents an authenticated admin user. Returned by ``require_admin``."""

    user_id: UUID
    email: str


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> AdminUser:
    """FastAPI dependency: validate JWT and verify the user is an admin.

    Raises:
        HTTPException 401: Invalid or expired JWT.
        HTTPException 403: Valid JWT but user is not an admin.

    Returns:
        AdminUser: The verified admin identity.
    """
    token = credentials.credentials
    supabase = request.app.state.supabase

    # ── Step 1: Verify the JWT with Supabase Auth ─────────────────────────────
    try:
        user_response = supabase.auth.get_user(token)
        user = user_response.user

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication token",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth_token_validation_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed — could not verify token",
        ) from exc

    user_id = user.id
    email = user.email or "unknown"

    # ── Step 2: Check admin role in profiles table ────────────────────────────
    try:
        result = (
            supabase.table("profiles")
            .select("is_admin")
            .eq("id", str(user_id))
            .single()
            .execute()
        )
        profile = result.data

        if not profile or not profile.get("is_admin"):
            logger.warning(
                "admin_access_denied",
                user_id=str(user_id),
                email=email,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrator access required",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "admin_check_failed",
            user_id=str(user_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify admin status",
        ) from exc

    logger.info("admin_authenticated", user_id=str(user_id), email=email)

    return AdminUser(user_id=UUID(user_id), email=email)
