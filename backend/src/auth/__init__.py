"""
Employee authentication (Google OAuth 2.0 + domain gate).

Ported from trampolinetech/infosec-tool — only @ALLOWED_DOMAIN Google accounts can
sign in. ADDITIVE and OFF BY DEFAULT (config.AUTH_ENABLED): with it unset the app
enforces nothing and behaves exactly as before. Stateless — the signed JWT is the
session; no users table required.

Flow: /api/auth/login → Google consent → /api/auth/callback (domain check → JWT →
redirect to the frontend with ?token=) → frontend stores it → sends
`Authorization: Bearer <jwt>` on every request → require_user validates it.
"""
