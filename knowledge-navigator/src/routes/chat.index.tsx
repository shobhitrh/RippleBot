import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { createThread, useThreads } from "@/lib/chat-store";

export const Route = createFileRoute("/chat/")({
  component: ChatIndex,
});

function ChatIndex() {
  const navigate = useNavigate();
  const threads = useThreads();
  const navigated = useRef(false);

  useEffect(() => {
    // Only navigate once — threads array stabilizes after hydration
    if (navigated.current) return;
    if (threads.length === 0) return; // still hydrating — wait for real data

    navigated.current = true;
    navigate({ to: "/chat/$threadId", params: { threadId: threads[0].id }, replace: true });
  }, [threads.length, navigate]);

  // If no threads exist yet, create one and navigate
  useEffect(() => {
    if (navigated.current) return;
    if (threads.length > 0) return; // handled above

    // Wait one tick so hydration can settle first
    const id = setTimeout(() => {
      if (navigated.current) return;
      if (useThreadsSnapshot().length > 0) return;
      navigated.current = true;
      const t = createThread();
      navigate({ to: "/chat/$threadId", params: { threadId: t.id }, replace: true });
    }, 100);

    return () => clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
