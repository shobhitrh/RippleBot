import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { createThread, useThreads, threadsForCompany } from "@/lib/chat-store";
import { useCompany } from "@/lib/company";

export const Route = createFileRoute("/chat/")({
  component: ChatIndex,
});

function ChatIndex() {
  const navigate = useNavigate();
  const allThreads = useThreads();
  const [selectedCompany] = useCompany();
  const threads = threadsForCompany(allThreads, selectedCompany);
  const navigated = useRef(false);

  useEffect(() => {
    // Only navigate once — threads array stabilizes after hydration
    if (navigated.current) return;
    if (threads.length === 0) return; // still hydrating — wait for real data

    navigated.current = true;
    navigate({ to: "/chat/$threadId", params: { threadId: threads[0].id }, replace: true });
  }, [threads.length, navigate]);

  // If this company has no threads yet, create one and navigate.
  useEffect(() => {
    if (navigated.current) return;
    if (threads.length > 0) return; // handled above

    // Wait one tick so hydration can settle first
    const id = setTimeout(() => {
      if (navigated.current) return;
      const mine = threadsForCompany(useThreadsSnapshot(), selectedCompany);
      if (mine.length > 0) {
        navigated.current = true;
        navigate({ to: "/chat/$threadId", params: { threadId: mine[0].id }, replace: true });
        return;
      }
      navigated.current = true;
      const t = createThread(); // stamped with the selected company
      navigate({ to: "/chat/$threadId", params: { threadId: t.id }, replace: true });
    }, 100);

    return () => clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCompany]);

  return (
    <div className="flex h-[calc(100vh-3.5rem)] items-center justify-center text-sm text-muted-foreground">
      Loading conversation…
    </div>
  );
}

// Read threads directly (no hook) to avoid stale closure issues
function useThreadsSnapshot() {
  try {
    const raw = window.localStorage.getItem("ksai:threads:v1");
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}
