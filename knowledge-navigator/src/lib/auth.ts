/**
 * Employee-login (Google OAuth) client helpers.
 *
 * Flow: user clicks "Sign in with Google" → /api/auth/login → Google → the backend
 * redirects to `<frontend>/?token=<jwt>`. captureTokenFromUrl() stores that JWT and
 * strips it from the URL; every API call then carries `Authorization: Bearer <jwt>`
 * (via api.ts authHeader()). Auth is enforced only when the backend has AUTH_ENABLED
 * on — otherwise the app is open (current behaviour), so this is fully non-breaking.
 */
import { apiFetch, apiUrl } from "@/lib/api";

const TOKEN_KEY = "ripplebot:token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(t: string) {
  if (typeof window !== "undefined") window.localStorage.setItem(TOKEN_KEY, t);
}

export function clearToken() {
  if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY);
}

/** Read `?token=<jwt>` left by the OAuth redirect, store it, and clean the URL. */
export function captureTokenFromUrl() {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const t = url.searchParams.get("token");
  if (t) {
    setToken(t);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  }
}

export type AuthStatus = { enabled: boolean; configured: boolean; allowed_domain: string | null };

export async function getAuthStatus(): Promise<AuthStatus | null> {
  try {
    const r = await apiFetch("/api/auth/status", { timeoutMs: 8000 });
    return r.ok ? ((await r.json()) as AuthStatus) : null;
  } catch {
    return null;
  }
}

export type Me = { authenticated: boolean; email?: string; role?: string; name?: string };

export async function getMe(): Promise<Me> {
  try {
    const r = await apiFetch("/api/auth/me", { timeoutMs: 8000 });
    return r.ok ? ((await r.json()) as Me) : { authenticated: false };
  } catch {
    return { authenticated: false };
  }
}

export function loginUrl(): string {
  return apiUrl("/api/auth/login");
}

export function logout() {
  clearToken();
  if (typeof window !== "undefined") window.location.href = "/";
}
