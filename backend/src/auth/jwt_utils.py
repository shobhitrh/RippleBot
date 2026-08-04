"""JWT create/verify (HS256). Uses PyJWT. Ported from infosec-tool's auth/jwt.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.src import config


def create_access_token(email: str, role: str = "internal", name: str = "") -> str:
    import jwt  # PyJWT — lazy import so the module loads even if the dep is absent

    now = datetime.now(timezone.utc)
    payload = {
        "email": email,
        "role": role,
        "name": name,
        "iat": now,
        "exp": now + timedelta(hours=config.JWT_EXPIRATION_HOURS),
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        import jwt
        return jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
    except Exception:
        return None
