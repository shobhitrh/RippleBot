"""
FastAPI auth dependencies.

require_user  — enforces a valid employee JWT *only when* config.AUTH_ENABLED.
               With auth off, it returns a permissive anonymous principal so the
               existing app keeps working unchanged (non-breaking).
require_internal — additionally requires the "internal" role; use to gate the
               live-DB tools so customers (a later, non-internal role) can't reach them.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from backend.src import config
from backend.src.auth.jwt_utils import decode_access_token

ANON = {"email": None, "role": "anonymous", "name": "", "authenticated": False}


def _principal_from_header(authorization: Optional[str]) -> Optional[dict]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    if not payload:
        return None
    return {
        "email": payload.get("email"),
        "role": payload.get("role", "internal"),
        "name": payload.get("name", ""),
        "authenticated": True,
    }


async def require_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """Valid JWT required when AUTH_ENABLED; otherwise anonymous is allowed through."""
    principal = _principal_from_header(authorization)
    if not config.AUTH_ENABLED:
        return principal or ANON
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


async def require_internal(authorization: Optional[str] = Header(default=None)) -> dict:
    """Employee-only. When AUTH_ENABLED, requires an authenticated 'internal' role."""
    principal = await require_user(authorization)
    if config.AUTH_ENABLED and principal.get("role") != "internal":
        raise HTTPException(status_code=403, detail="Internal access only")
    return principal
