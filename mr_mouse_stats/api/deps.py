"""Shared plumbing: database connections and admin authentication."""

from __future__ import annotations

import logging
import os
from typing import Iterator

import psycopg
from fastapi import HTTPException, Request

from .. import config, db

logger = logging.getLogger(__name__)

# Set to "1" to accept unauthenticated admin requests. Local development
# only — in AWS the Cognito authorizer rejects before the Lambda is invoked,
# so this flag is never set there.
ENV_DEV_AUTH = "MR_MOUSE_STATS_ADMIN_DEV_AUTH"

_connection: db.Connection | None = None


def get_conn() -> Iterator[db.Connection]:
    """One connection per execution environment, reused across invocations.

    Kept at module scope deliberately: opening a connection per request would
    add a round trip to Neon on every call. Reconnects if the cached handle
    has been closed or broken by an idle timeout.
    """
    global _connection
    if _connection is None or _connection.closed:
        _connection = db.connect(config.db())
    try:
        yield _connection
    except psycopg.OperationalError:
        # A dead connection must not be reused by the next invocation.
        try:
            _connection.close()
        finally:
            _connection = None
        raise


def reset_connection() -> None:
    """Drop the cached connection. Used by tests."""
    global _connection
    if _connection is not None and not _connection.closed:
        _connection.close()
    _connection = None


def _claims_from_gateway(request: Request) -> dict | None:
    """Claims that API Gateway's JWT authorizer attached to the request.

    We do not verify the token here on purpose. API Gateway rejects
    unauthorized requests *before* invoking the Lambda, so anything that
    reaches this code has already been validated against the Cognito JWKS.
    Re-implementing verification would add a second, weaker code path.
    """
    event = request.scope.get("aws.event")
    if not isinstance(event, dict):
        return None
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("jwt", {}).get("claims")
    return claims if isinstance(claims, dict) else None


def require_admin(request: Request) -> dict:
    """Identify the caller, or refuse. Returns the caller's JWT claims."""
    claims = _claims_from_gateway(request)
    if claims is not None:
        return claims
    if os.environ.get(ENV_DEV_AUTH) == "1":
        logger.warning(
            "admin request accepted without authentication",
            extra={"fields": {"reason": f"{ENV_DEV_AUTH}=1", "path": request.url.path}},
        )
        return {"sub": "dev", "email": "dev@localhost"}
    raise HTTPException(
        status_code=401,
        detail="admin API requires a Cognito access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def caller_label(claims: dict) -> str:
    """Short identifier for logging who performed a mutation."""
    return claims.get("email") or claims.get("username") or claims.get("sub") or "unknown"
