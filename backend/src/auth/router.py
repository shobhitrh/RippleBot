"""
Auth endpoints (mounted at /api/auth). Google OAuth login + domain gate.

Stateless: on callback we verify the email domain and issue a JWT — no user table.
All @ALLOWED_DOMAIN users get the "internal" role (employee-only launch). Customer/
tenant roles come in a later phase.
"""
from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import RedirectResponse

from backend.src import config
from backend.src.auth import service
from backend.src.auth.jwt_utils import create_access_token, decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
async def auth_status():
    """Whether auth is enabled/configured (no secrets)."""
    return {
        "enabled": config.AUTH_ENABLED,
        "configured": config.auth_configured(),
        "allowed_domain": config.ALLOWED_DOMAIN or None,
        "missing": [] if config.auth_configured() else ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
    }


@router.get("/login")
async def login():
    """Redirect to Google's consent screen."""
    if not config.auth_configured():
        raise HTTPException(status_code=503, detail="Auth not configured (GOOGLE_CLIENT_ID/SECRET missing).")
    state = secrets.token_urlsafe(16)
    return RedirectResponse(url=service.get_authorization_url(state))


@router.get("/callback")
async def callback(code: str, state: str | None = None):
    """Exchange the code, enforce the domain gate, issue a JWT, redirect to the frontend."""
    if not config.auth_configured():
        raise HTTPException(status_code=503, detail="Auth not configured.")
    try:
        token_data = await service.exchange_code_for_token(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to exchange code: {e}")

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token received")

    try:
        user_info = await service.get_google_user_info(access_token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get user info: {e}")

    email = (user_info.get("email") or "").lower()
    # The domain gate — only @ALLOWED_DOMAIN employees may sign in.
    if config.ALLOWED_DOMAIN and not email.endswith(f"@{config.ALLOWED_DOMAIN}"):
        raise HTTPException(status_code=403, detail=f"Access restricted to @{config.ALLOWED_DOMAIN} accounts")

    jwt_token = create_access_token(email=email, role="internal", name=user_info.get("name", ""))
    frontend = config.FRONTEND_URL.rstrip("/")
    return RedirectResponse(url=f"{frontend}/?{urlencode({'token': jwt_token})}")


@router.get("/me")
async def me(authorization: str | None = Header(default=None)):
    """Return the current principal from the Bearer JWT (or anonymous)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return {"authenticated": False}
    payload = decode_access_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        return {"authenticated": False}
    return {"authenticated": True, "email": payload.get("email"), "role": payload.get("role"), "name": payload.get("name")}


@router.post("/logout")
async def logout():
    # Stateless JWT — logout is client-side (drop the token). Endpoint for symmetry.
    return {"message": "Logged out"}
