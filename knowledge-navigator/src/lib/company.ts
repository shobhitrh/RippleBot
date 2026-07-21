import { useEffect, useSyncExternalStore } from "react";

/**
 * Tenant (company) selection, backed by the backend registry (GET/POST
 * /api/companies). The selected id is sent as X-Company-Id on every request and
 * scopes chat history locally. Companies + their email domains live in the
 * backend so Fireflies auto-routing and the selector share one source of truth.
 */
export type Company = { id: string; name: string; domains?: string[] };

const SELECTED_KEY = "selected_company";
const API_BASE = (
  (import.meta as any).env?.VITE_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

// Fallback used only until the backend list loads (or if it's unreachable).
const FALLBACK: Company[] = [{ id: "pinelabs", name: "Pine Labs" }];

let _companies: Company[] = FALLBACK;
let _fetched = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function getCompanies(): Company[] {
  return _companies;
}

export function getSelectedCompany(): string {
  if (typeof window === "undefined") return _companies[0]?.id ?? "pinelabs";
  return window.localStorage.getItem(SELECTED_KEY) || _companies[0]?.id || "pinelabs";
}

export function setSelectedCompany(id: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(SELECTED_KEY, id);
  emit();
}

export async function refreshCompanies(): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/api/companies`);
    if (res.ok) {
      const data = (await res.json()) as Company[];
      if (Array.isArray(data) && data.length) {
        _companies = data;
        // If the stored selection no longer exists (e.g. removed demo tenant),
        // fall back to the first real company.
        const sel = typeof window !== "undefined" && window.localStorage.getItem(SELECTED_KEY);
        if (sel && !data.some((c) => c.id === sel)) {
          window.localStorage.setItem(SELECTED_KEY, data[0].id);
        }
        emit();
      }
    }
  } catch {
    /* keep fallback/last-known list if backend is unreachable */
  } finally {
    _fetched = true;
  }
}

export async function addCompany(name: string, domain?: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/companies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, domains: domain ? [domain] : [] }),
    });
  } catch {
    /* ignore; refresh will reflect server state */
  }
  await refreshCompanies();
  // select the newly-created company
  const id = name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^[_-]+|[_-]+$/g, "");
  if (id) setSelectedCompany(id);
}

// ── React binding ────────────────────────────────────────────────────────────
function subscribe(cb: () => void) {
  listeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === SELECTED_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

/** Returns [selectedId, companies]; re-renders on change. Fetches the list once. */
export function useCompany(): [string, Company[]] {
  useEffect(() => {
    if (!_fetched) refreshCompanies();
  }, []);
  const selected = useSyncExternalStore(subscribe, getSelectedCompany, () => "pinelabs");
  const companies = useSyncExternalStore(subscribe, getCompanies, () => FALLBACK);
  return [selected, companies];
}
