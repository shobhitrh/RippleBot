import { useSyncExternalStore } from "react";

/**
 * Tenant (company) selection. The selected company id is sent as the
 * "X-Company-Id" header on every backend request (see api.ts) and used to scope
 * chat history locally. New tenants can be added on the fly — the backend
 * provisions storage for any company id it sees.
 */
export type Company = { id: string; name: string };

const COMPANIES_KEY = "ripplebot:companies:v1";
const SELECTED_KEY = "selected_company";

const DEFAULT_COMPANIES: Company[] = [
  { id: "pinelabs", name: "Pine Labs" },
  { id: "techcorp", name: "TechCorp" },
];

export function normalizeCompanyId(raw: string): string {
  return (raw || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^[_-]+|[_-]+$/g, "");
}

// Cached so useSyncExternalStore gets a stable reference until it changes.
let _companiesCache: Company[] | null = null;

function readCompanies(): Company[] {
  if (_companiesCache) return _companiesCache;
  if (typeof window === "undefined") return DEFAULT_COMPANIES;
  try {
    const raw = window.localStorage.getItem(COMPANIES_KEY);
    const parsed = raw ? (JSON.parse(raw) as Company[]) : null;
    _companiesCache = parsed && parsed.length ? parsed : DEFAULT_COMPANIES;
  } catch {
    _companiesCache = DEFAULT_COMPANIES;
  }
  return _companiesCache;
}

export function getCompanies(): Company[] {
  return readCompanies();
}

export function getSelectedCompany(): string {
  if (typeof window === "undefined") return DEFAULT_COMPANIES[0].id;
  return window.localStorage.getItem(SELECTED_KEY) || readCompanies()[0].id;
}

const listeners = new Set<() => void>();
function emit() {
  _companiesCache = null; // invalidate so next read re-parses
  listeners.forEach((l) => l());
}

export function setSelectedCompany(id: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(SELECTED_KEY, id);
  emit();
}

export function addCompany(name: string): Company {
  const id = normalizeCompanyId(name);
  const companies = readCompanies();
  const existing = companies.find((c) => c.id === id);
  if (!existing) {
    companies.push({ id, name: name.trim() || id });
    window.localStorage.setItem(COMPANIES_KEY, JSON.stringify(companies));
  }
  setSelectedCompany(id); // also emits
  return { id, name };
}

// ── React binding ──────────────────────────────────────────────────────────
function subscribe(cb: () => void) {
  listeners.add(cb);
  // Sync across tabs.
  const onStorage = (e: StorageEvent) => {
    if (e.key === SELECTED_KEY || e.key === COMPANIES_KEY) {
      _companiesCache = null;
      cb();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

/** Returns [selectedId, companies]; re-renders when either changes. */
export function useCompany(): [string, Company[]] {
  const selected = useSyncExternalStore(
    subscribe,
    getSelectedCompany,
    () => DEFAULT_COMPANIES[0].id,
  );
  const companies = useSyncExternalStore(
    subscribe,
    getCompanies,
    () => DEFAULT_COMPANIES,
  );
  return [selected, companies];
}
