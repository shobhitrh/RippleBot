import { useEffect, useState, useSyncExternalStore } from "react";
import { getSelectedCompany } from "@/lib/company";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: { file: string; snippet: string }[];
  flagged?: boolean;
  isGenerating?: boolean;
  createdAt: number;
};

export type ChatThread = {
  id: string;
  title: string;
  companyId: string; // tenant this conversation belongs to
  messages: ChatMessage[];
  updatedAt: number;
  createdAt: number;
};

const KEY = "ksai:threads:v1";

function read(): ChatThread[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as ChatThread[]) : [];
  } catch {
    return [];
  }
}

const listeners = new Set<() => void>();

// ── Stable snapshot cache ──────────────────────────────────────────────────
// useSyncExternalStore requires getSnapshot to return the SAME reference when
// nothing has changed. Without this cache, read() parses JSON on every call
// and always returns a new array, causing an infinite "state changed" loop.
let _cache: ChatThread[] | null = null;

function getSnapshot(): ChatThread[] {
  if (_cache === null) {
    _cache = read();
  }
  return _cache;
}

function emit() {
  _cache = null; // invalidate cache so next getSnapshot re-reads
  listeners.forEach((l) => l());
}

function write(threads: ChatThread[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY, JSON.stringify(threads));
  emit();
}

export function useThreads() {
  const subscribe = (cb: () => void) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  };
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const threads = useSyncExternalStore(
    subscribe,
    getSnapshot,          // stable reference — same array until emit() is called
    () => [] as ChatThread[],
  );
  return hydrated ? threads : [];
}

export function createThread(title = "New conversation"): ChatThread {
  const now = Date.now();
  const t: ChatThread = {
    id: crypto.randomUUID(),
    title,
    companyId: getSelectedCompany(),
    messages: [],
    updatedAt: now,
    createdAt: now,
  };
  write([t, ...read()]);
  return t;
}

/** Threads belonging to a given company (legacy threads with no companyId
 *  are shown under the currently-selected company). */
export function threadsForCompany(threads: ChatThread[], companyId: string): ChatThread[] {
  return threads.filter((t) => (t.companyId ?? companyId) === companyId);
}

export function deleteThread(id: string) {
  write(read().filter((t) => t.id !== id));
}

export function getThread(id: string): ChatThread | undefined {
  return read().find((t) => t.id === id);
}

export function appendMessage(threadId: string, msg: ChatMessage) {
  const threads = read();
  const idx = threads.findIndex((t) => t.id === threadId);
  if (idx === -1) return;
  threads[idx].messages.push(msg);
  threads[idx].updatedAt = Date.now();
  if (threads[idx].title === "New conversation" && msg.role === "user") {
    threads[idx].title = msg.content.slice(0, 48);
  }
  const [t] = threads.splice(idx, 1);
  write([t, ...threads]);
}

export function updateThreadTitle(id: string, newTitle: string) {
  const threads = read();
  const t = threads.find((x) => x.id === id);
  if (!t) return;
  t.title = newTitle.trim() || "Untitled conversation";
  t.updatedAt = Date.now();
  write(threads);
}

export function updateMessage(threadId: string, msgId: string, patch: Partial<ChatMessage>) {
  const threads = read();
  const t = threads.find((x) => x.id === threadId);
  if (!t) return;
  const m = t.messages.find((x) => x.id === msgId);
  if (!m) return;
  Object.assign(m, patch);
  write(threads);
}

