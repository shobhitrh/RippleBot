"""Google OAuth 2.0 helpers. Ported near-verbatim from infosec-tool's auth/service.py."""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

from backend.src import config

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def get_authorization_url(state: str) -> str:
    params = {
        "client_id": config.GOOGLE_CLIENT_ID or "",
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": config.GOOGLE_CLIENT_ID or "",
                "client_secret": config.GOOGLE_CLIENT_SECRET or "",
                "redirect_uri": config.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        r.raise_for_status()
        return r.json()


async def get_google_user_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()
