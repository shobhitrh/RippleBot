import { useEffect, useRef } from "react";
import { getSelectedCompany } from "@/lib/company";

/** Header that scopes every request to the currently selected tenant. */
export function companyHeaders(): Record<string, string> {
  return { "X-Company-Id": getSelectedCompany() };
}

/**
 * Central backend access. The base URL is configurable via VITE_API_URL so the
 * frontend isn't hard-wired to localhost:8000 (set it in a .env file for other
 * environments). Everything here is defensive: timeouts prevent hung requests,
 * and the polling hook backs off when the backend is unreachable instead of
 * hammering it every few seconds.
 */
export const API_BASE = (
  (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * fetch() against the backend with an abort-based timeout. Use for short
 * request/response JSON calls — NOT for the chat SSE stream (which is
 * long-lived; call fetch(apiUrl(...)) directly there).
 */
export async function apiFetch(
  path: string,
  opts: RequestInit & { timeoutMs?: number } = {}
): Promise<Response> {
  const { timeoutMs = 12000, signal, headers, ...rest } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Respect a caller-supplied signal too.
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  try {
    // Always attach the tenant header (caller headers win on conflict).
    return await fetch(apiUrl(path), {
      ...rest,
      headers: { ...companyHeaders(), ...(headers as Record<string, string> | undefined) },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Poll `task` on an interval that backs off exponentially on failure and resets
 * on success. Pauses while the browser tab is hidden and resumes on focus, so a
 * backgrounded tab (or a down backend) never floods the server with requests.
 *
 * `task` should return `true`/resolve on success and `false`/throw on failure.
 */
export function useBackoffPoll(
  task: () => Promise<boolean | void>,
  { baseMs, maxMs = 30000 }: { baseMs: number; maxMs?: number }
) {
  // Keep the latest task without re-arming the loop on every render.
  const taskRef = useRef(task);
  taskRef.current = task;

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = baseMs; // failure backoff, grows x2 on failure up to maxMs

    // Slow cadence while the tab is backgrounded — but we never stop fetching
    // entirely, so data still loads in embedded/hidden contexts.
    const BACKGROUND_MS = Math.min(maxMs, 30000);

    const schedule = (ms: number) => {
      if (stopped) return;
      timer = setTimeout(run, ms);
    };

    async function run() {
      if (stopped) return;
      let ok = true;
      try {
        const result = await taskRef.current();
        ok = result !== false;
      } catch {
        ok = false;
      }
      delay = ok ? baseMs : Math.min(maxMs, delay * 2);
      const hidden = typeof document !== "undefined" && document.hidden;
      schedule(hidden ? Math.max(delay, BACKGROUND_MS) : delay);
    }

    run(); // always do an immediate first load, regardless of visibility

    const onVisible = () => {
      if (!stopped && !document.hidden) {
        clearTimeout(timer);
        delay = baseMs;
        run(); // refresh promptly when the tab returns to the foreground
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseMs, maxMs]);
}
