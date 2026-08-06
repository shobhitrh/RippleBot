/**
 * Agentic engine client (PIA unification — PRD §18).
 *
 * This is the frontend "plug-in point": the backend agentic layer ships flag-gated
 * and stubbed, so this client works today (reporting not-configured state) and lights
 * up the moment the keys are set in the backend .env. No further frontend wiring is
 * needed to go live — flip AGENTIC_MODE=on and add ANTHROPIC_API_KEY on the server.
 *
 * The /api/agentic/query stream uses the SAME SSE contract as /api/chat/query
 * (sources → token → done), so the existing chat renderer consumes it unchanged.
 */
import { apiFetch, apiUrl, companyHeaders, authHeader } from "@/lib/api";

export type ToolStatus = {
  owner: "ripplebot" | "pia";
  ready: boolean;
  missing?: string[];
};

export type AgenticStatus = {
  agentic_mode: boolean;
  orchestrator: { configured: boolean; model: string; classifier_model: string; missing: string[] };
  tools: Record<string, ToolStatus>;
  auth: { supabase_configured: boolean; note: string };
};

/** Fetch readiness of the agentic engine (which keys are still missing). Safe anytime. */
export async function getAgenticStatus(): Promise<AgenticStatus | null> {
  try {
    const res = await apiFetch("/api/agentic/status", { timeoutMs: 8000 });
    if (!res.ok) return null;
    return (await res.json()) as AgenticStatus;
  } catch {
    return null;
  }
}

export type SseHandlers = {
  onSources?: (sources: { file: string; snippet: string }[]) => void;
  onToken?: (text: string) => void;
  onDone?: () => void;
  onError?: (err: unknown) => void;
};

/**
 * Stream an answer from the agentic engine. Mirrors the existing chat SSE parsing
 * so it can be dropped into the chat route behind a feature check.
 */
export async function streamAgenticQuery(
  query: string,
  handlers: SseHandlers,
  opts: {
    history?: { role: string; content: string }[];
    signal?: AbortSignal;
    companyId?: string; // override tenant scope (the Assistant tab is cross-tenant)
  } = {}
): Promise<void> {
  try {
    // The Assistant is cross-tenant: help center is a shared store and the live-DB
    // tool fans out across all schemas, so a fixed scope avoids company-selector bias.
    const headers: Record<string, string> = opts.companyId
      ? { "Content-Type": "application/json", "X-Company-Id": opts.companyId, ...authHeader() }
      : { "Content-Type": "application/json", ...companyHeaders(), ...authHeader() };
    const res = await fetch(apiUrl("/api/agentic/query"), {
      method: "POST",
      headers,
      body: JSON.stringify({ query, history: opts.history ?? null }),
      signal: opts.signal,
    });
    if (!res.body) {
      handlers.onError?.(new Error("No response stream"));
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const clean = line.trim();
        if (!clean.startsWith("data: ")) continue;
        try {
          const data = JSON.parse(clean.slice(6));
          if (data.type === "sources") handlers.onSources?.(data.sources ?? []);
          else if (data.type === "token") handlers.onToken?.(data.text ?? "");
          else if (data.type === "done") handlers.onDone?.();
        } catch {
          /* ignore malformed keep-alive lines */
        }
      }
    }
  } catch (err) {
    handlers.onError?.(err);
  }
}
