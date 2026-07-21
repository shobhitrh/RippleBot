import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Send,
  Plus,
  Trash2,
  MessageSquare,
  Sparkles,
  Flag,
  Download,
  FileText,
  User,
  GripVertical,
  PanelLeftClose,
  PanelLeftOpen,
  ChevronDown,
  Pencil,
  Check,
  X,
  Mic,
  MicOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  appendMessage,
  createThread,
  deleteThread,
  updateMessage,
  updateThreadTitle,
  useThreads,
  threadsForCompany,
  type ChatMessage,
} from "@/lib/chat-store";
import { promptChips } from "@/lib/mock-data";
import { SourceDrawer } from "@/components/source-drawer";
import { apiUrl, companyHeaders } from "@/lib/api";
import { useCompany } from "@/lib/company";

export const Route = createFileRoute("/chat/$threadId")({
  component: ChatThreadView,
});

// ── Sidebar drag-to-resize ─────────────────────────────
const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 420;
const SIDEBAR_DEFAULT = 260;

function ChatThreadView() {
  const { threadId } = Route.useParams();
  const navigate = useNavigate();
  const allThreads = useThreads();
  const [selectedCompany] = useCompany();
  // Only show conversations belonging to the selected tenant.
  const threads = threadsForCompany(allThreads, selectedCompany);
  const thread = allThreads.find((t) => t.id === threadId);

  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [sourceOpen, setSourceOpen] = useState<{ file: string; snippet?: string } | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT);
  const [dragging, setDragging] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);

  const messages = thread?.messages ?? [];
  const lastLen = messages[messages.length - 1]?.content.length ?? 0;

  // When the tenant switches, leave a conversation that belongs to another one.
  useEffect(() => {
    if (thread && (thread.companyId ?? selectedCompany) !== selectedCompany) {
      navigate({ to: "/chat" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCompany]);

  // Keep the conversation pinned to the latest output. Fires on new messages,
  // the thinking indicator, and while tokens stream into the last message.
  useEffect(() => {
    const viewport = scrollRef.current?.querySelector<HTMLElement>(
      "[data-radix-scroll-area-viewport]"
    );
    const target = viewport ?? scrollRef.current;
    if (!target) return;
    // Instant follow during streaming reads as smooth; avoids stacking
    // smooth-scroll animations that never catch up to fast token updates.
    target.scrollTo({ top: target.scrollHeight, behavior: "auto" });
  }, [messages.length, lastLen, thinking]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [threadId]);

  // ── Sidebar drag logic ───────────────────────────────
  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: sidebarWidth };
    setDragging(true);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, [sidebarWidth]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      // rAF-throttle so width updates land at most once per frame — no jank
      // from firing setState on every high-frequency mousemove event.
      const delta = e.clientX - dragRef.current.startX;
      const next = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, dragRef.current.startW + delta));
      requestAnimationFrame(() => setSidebarWidth(next));
    };
    const onUp = () => {
      if (!dragRef.current) return;
      dragRef.current = null;
      setDragging(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  // ── Send / streaming ─────────────────────────────────
  const send = async (text: string) => {
    if (!text.trim() || !thread) return;
    const currentThreadId = thread.id;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text.trim(),
      createdAt: Date.now(),
    };
    appendMessage(currentThreadId, userMsg);
    setInput("");
    setThinking(true);

    const assistantMsgId = crypto.randomUUID();
    appendMessage(currentThreadId, {
      id: assistantMsgId,
      role: "assistant",
      content: "",
      citations: [],
      isGenerating: true,
      createdAt: Date.now(),
    });

    try {
      const response = await fetch(apiUrl("/api/chat/query"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...companyHeaders() },
        body: JSON.stringify({ query: text.trim(), filters: {} }),
      });

      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) throw new Error("Response body not readable");

      const decoder = new TextDecoder("utf-8");
      let partialData = "";
      let streamedText = "";
      let citations: any[] = [];

      setThinking(false);

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        partialData += decoder.decode(value, { stream: true });
        const lines = partialData.split("\n");
        partialData = lines.pop() || "";

        for (const line of lines) {
          const cleanLine = line.trim();
          if (!cleanLine.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(cleanLine.slice(6));
            if (data.type === "sources") {
              citations = data.sources.map((s: any) => ({
                file: s.filename,
                snippet: s.exact_snippet_text,
              }));
              updateMessage(currentThreadId, assistantMsgId, { citations });
            } else if (data.type === "token") {
              streamedText += data.text;
              updateMessage(currentThreadId, assistantMsgId, { content: streamedText });
            } else if (data.type === "done") {
              updateMessage(currentThreadId, assistantMsgId, { isGenerating: false });
              break;
            }
          } catch (e) {
            console.error("SSE parse error", e);
          }
        }
      }
    } catch (e) {
      console.error("Network error", e);
      updateMessage(currentThreadId, assistantMsgId, {
        content:
          "Connection to the RAG backend failed. Make sure the backend is running and reachable.",
      });
      toast.error("RAG API connection error");
    } finally {
      setThinking(false);
      updateMessage(currentThreadId, assistantMsgId, { isGenerating: false });
      inputRef.current?.focus();
    }
  };

  const newThread = () => {
    const t = createThread();
    navigate({ to: "/chat/$threadId", params: { threadId: t.id } });
  };

  const remove = (id: string) => {
    deleteThread(id);
    if (id === threadId) navigate({ to: "/chat" });
  };

  const exportMd = () => {
    if (!thread) return;
    const md = `# ${thread.title}\n\n${thread.messages
      .map(
        (m) =>
          `**${m.role === "user" ? "You" : "Assistant"}**\n\n${m.content}${
            m.citations?.length
              ? "\n\n_Sources: " + m.citations.map((c) => c.file).join(", ") + "_"
              : ""
          }`
      )
      .join("\n\n---\n\n")}`;
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${thread.title.replace(/\s+/g, "-")}.md`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("Conversation exported");
  };

  const flag = (msgId: string) => {
    if (!thread) return;
    updateMessage(thread.id, msgId, { flagged: true });
    toast.success("Flagged as missing info", {
      description: "Admins will be notified to upload updated docs.",
    });
  };

  // ── Speech-to-Text (Voice Input) ──
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);

  const toggleListening = () => {
    if (isListening) {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
      setIsListening(false);
      return;
    }

    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      toast.error("Speech recognition is not supported in your browser.", {
        description: "Please use Google Chrome, Microsoft Edge, or Apple Safari.",
      });
      return;
    }

    try {
      const recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = "en-US";

      recognition.onstart = () => {
        setIsListening(true);
        toast.info("Listening... Speak your question.");
      };

      recognition.onresult = (event: any) => {
        let currentTranscript = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          currentTranscript += event.results[i][0].transcript;
        }
        if (currentTranscript.trim()) {
          setInput(currentTranscript);
        }
      };

      recognition.onerror = (event: any) => {
        console.error("Speech recognition error:", event.error);
        if (event.error !== "no-speech") {
          toast.error(`Voice input error: ${event.error}`);
        }
        setIsListening(false);
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognitionRef.current = recognition;
      recognition.start();
    } catch (err) {
      console.error("Failed to start speech recognition:", err);
      toast.error("Failed to start microphone recording.");
      setIsListening(false);
    }
  };

  // ── Group threads by date ─────────────────────────────
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86400000).toDateString();
  const todayThreads = threads.filter(
    (t) => new Date(t.createdAt ?? 0).toDateString() === today
  );
  const yesterdayThreads = threads.filter(
    (t) => new Date(t.createdAt ?? 0).toDateString() === yesterday
  );
  const olderThreads = threads.filter((t) => {
    const d = new Date(t.createdAt ?? 0).toDateString();
    return d !== today && d !== yesterday;
  });

  const ThreadGroup = ({ label, items }: { label: string; items: typeof threads }) => {
    if (items.length === 0) return null;
    return (
      <div className="mb-2">
        <p className="px-3 py-1 text-[10px] font-semibold uppercase tracking-widest text-sidebar-foreground/40 select-none">
          {label}
        </p>
        {items.map((t) => (
          <ThreadItem key={t.id} t={t} active={t.id === threadId} onDelete={remove} />
        ))}
      </div>
    );
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] w-full overflow-hidden animate-in-fade">
      {/* ── Sidebar ── */}
      <aside
        className={`hidden md:flex flex-col flex-shrink-0 overflow-hidden ${
          dragging ? "" : "transition-[width] duration-200 ease-in-out"
        }`}
        style={{
          width: sidebarOpen ? sidebarWidth : 0,
          minWidth: sidebarOpen ? (dragging ? 0 : SIDEBAR_MIN) : 0,
          background: "var(--color-sidebar)",
        }}
      >
        {/* New conversation button */}
        <div className="flex items-center gap-2 p-3">
          <button
            onClick={newThread}
            className="flex flex-1 items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-all duration-150 group"
          >
            <Plus className="h-4 w-4 shrink-0 text-sidebar-primary" />
            <span className="truncate">New conversation</span>
          </button>
        </div>

        <div className="mx-3 border-b border-sidebar-border" />

        {/* Thread list */}
        <ScrollArea className="flex-1 py-2">
          {threads.length === 0 ? (
            <div className="px-4 py-8 text-center text-xs text-sidebar-foreground/40">
              No conversations yet.
              <br />
              Start a new one above.
            </div>
          ) : (
            <>
              <ThreadGroup label="Today" items={todayThreads} />
              <ThreadGroup label="Yesterday" items={yesterdayThreads} />
              <ThreadGroup label="Older" items={olderThreads} />
            </>
          )}
        </ScrollArea>
      </aside>

      {/* ── Drag handle ── */}
      {sidebarOpen && (
        <div
          onMouseDown={onDragStart}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize conversation sidebar"
          className="group relative hidden md:flex w-2 shrink-0 cursor-col-resize items-center justify-center"
        >
          {/* invisible by default so there's no seam between the dark sidebar and
              the light chat area; the accent line only appears on hover / drag */}
          <div
            className={`h-full w-px transition-colors duration-150 ${
              dragging
                ? "bg-accent/70"
                : "bg-transparent group-hover:bg-accent/50"
            }`}
          />
          <GripVertical
            className={`absolute h-4 w-4 opacity-0 transition-opacity duration-150 group-hover:opacity-100 ${
              dragging ? "opacity-100 text-accent/70" : "text-sidebar-foreground/40"
            }`}
          />
        </div>
      )}

      {/* ── Main chat area ── */}
      <main className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <div className="flex items-center justify-between gap-2 border-b bg-background/80 backdrop-blur-sm px-4 py-2.5 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            {/* Sidebar toggle */}
            <button
              onClick={() => setSidebarOpen((v) => !v)}
              className="hidden md:flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-all duration-150"
            >
              {sidebarOpen ? (
                <PanelLeftClose className="h-4 w-4" />
              ) : (
                <PanelLeftOpen className="h-4 w-4" />
              )}
            </button>
            <div className="flex items-center gap-2 min-w-0">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/15">
                <Sparkles className="h-3.5 w-3.5 text-accent" />
              </div>
              <span className="truncate text-sm font-semibold">
                {thread?.title ?? "RippleBot Assistant"}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={exportMd}
              className="h-8 gap-1.5 text-xs text-muted-foreground"
            >
              <Download className="h-3.5 w-3.5" />
              Export
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={newThread}
              className="h-8 gap-1.5 text-xs text-muted-foreground md:hidden"
            >
              <Plus className="h-3.5 w-3.5" />
              New
            </Button>
          </div>
        </div>

        {/* Messages */}
        <ScrollArea ref={scrollRef} className="flex-1 min-h-0">
          <div className="mx-auto max-w-3xl px-4 py-8">
            {messages.length === 0 && !thinking && (
              <EmptyState onPick={(p) => send(p)} />
            )}

            <div className="space-y-6">
              {messages.map((m, i) => (
                <MessageBubble
                  key={m.id}
                  m={m}
                  style={{ animationDelay: `${i * 30}ms` }}
                  onCite={(c) => setSourceOpen(c)}
                  onFlag={() => flag(m.id)}
                />
              ))}

            </div>
          </div>
        </ScrollArea>

        {/* Input bar */}
        <div className="border-t bg-background/90 backdrop-blur-sm px-4 py-3 shrink-0">
          <div className="mx-auto max-w-3xl">
            <div className="input-glow flex items-end gap-2 rounded-xl border bg-card px-3 py-2 shadow-sm transition-all duration-200">
              <Textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(input);
                  }
                }}
                placeholder={
                  isListening
                    ? "Listening... Speak your question..."
                    : "Ask anything about your knowledge base..."
                }
                className="min-h-[44px] max-h-36 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 text-sm p-1 placeholder:text-muted-foreground/60"
              />
              <Button
                size="icon"
                type="button"
                variant="ghost"
                onClick={toggleListening}
                className={`h-9 w-9 shrink-0 rounded-lg transition-all duration-200 ${
                  isListening
                    ? "bg-red-500 text-white hover:bg-red-600 animate-pulse shadow-md shadow-red-500/20"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                }`}
                title={isListening ? "Stop voice input" : "Start voice input (Speech-to-Text)"}
              >
                {isListening ? (
                  <MicOff className="h-4 w-4" />
                ) : (
                  <Mic className="h-4 w-4" />
                )}
              </Button>
              <Button
                size="icon"
                onClick={() => send(input)}
                disabled={!input.trim() || thinking}
                className="h-9 w-9 shrink-0 rounded-lg bg-accent hover:bg-accent/90 transition-all duration-150 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
            <p className="mt-1.5 text-center text-[11px] text-muted-foreground/60">
              Click <Mic className="inline h-3 w-3 text-accent" /> for voice input &middot; Enter to send &middot; Shift+Enter for newline
            </p>
          </div>
        </div>
      </main>

      <SourceDrawer
        open={!!sourceOpen}
        onOpenChange={(o) => !o && setSourceOpen(null)}
        file={sourceOpen?.file ?? null}
        snippet={sourceOpen?.snippet}
      />
    </div>
  );
}

// ── Thread item ───────────────────────────────────────────
function ThreadItem({
  t,
  active,
  onDelete,
}: {
  t: { id: string; title: string };
  active: boolean;
  onDelete: (id: string) => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(t.title);

  const handleSave = () => {
    if (editTitle.trim()) {
      updateThreadTitle(t.id, editTitle);
      toast.success("Conversation renamed");
    } else {
      setEditTitle(t.title);
    }
    setIsEditing(false);
  };

  return (
    <div
      className={`group relative flex items-center gap-1 mx-1.5 rounded-lg px-2 py-2 transition-all duration-150 ${
        active
          ? "bg-sidebar-accent text-sidebar-foreground"
          : "text-sidebar-foreground/60 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
      }`}
    >
      <Link
        to="/chat/$threadId"
        params={{ threadId: t.id }}
        className="flex min-w-0 flex-1 items-start gap-2 overflow-hidden"
      >
        <MessageSquare className="h-3.5 w-3.5 shrink-0 text-sidebar-primary/70 mt-0.5" />
        {isEditing ? (
          <input
            type="text"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave();
              if (e.key === "Escape") {
                setEditTitle(t.title);
                setIsEditing(false);
              }
            }}
            onClick={(e) => e.stopPropagation()}
            autoFocus
            className="w-full rounded bg-background border border-border px-1.5 py-0.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
        ) : (
          <span
            className="block text-sm leading-snug overflow-hidden text-ellipsis break-words min-w-0 flex-1"
            style={{
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {t.title}
          </span>
        )}
      </Link>
      {isEditing ? (
        <div className="flex items-center gap-0.5 shrink-0">
          <button
            onClick={handleSave}
            className="rounded p-1 text-emerald-500 hover:bg-sidebar-accent transition-colors"
            title="Save title"
          >
            <Check className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => {
              setEditTitle(t.title);
              setIsEditing(false);
            }}
            className="rounded p-1 text-sidebar-foreground/50 hover:bg-sidebar-accent transition-colors"
            title="Cancel"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsEditing(true);
            }}
            className="rounded-md p-0.5 text-sidebar-foreground/40 hover:text-sidebar-foreground transition-all duration-150"
            title="Rename conversation"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onDelete(t.id);
            }}
            className="rounded-md p-0.5 text-sidebar-foreground/40 hover:text-destructive transition-all duration-150"
            title="Delete conversation"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────
function EmptyState({ onPick }: { onPick: (p: string) => void }) {
  return (
    <div className="mx-auto max-w-2xl text-center animate-in-fade">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-accent/20 to-accent/5 ring-1 ring-accent/20">
        <Sparkles className="h-7 w-7 text-accent" />
      </div>
      <h2 className="mt-5 text-2xl font-semibold tracking-tight">Ask RippleBot</h2>
      <p className="mt-2 text-sm text-muted-foreground max-w-sm mx-auto">
        Get grounded answers from your organization's knowledge base, complete with source citations.
      </p>
      <div className="mt-7 grid gap-2.5 text-left sm:grid-cols-2">
        {promptChips.map((p) => (
          <button
            key={p}
            onClick={() => onPick(p)}
            className="group rounded-xl border bg-card p-3.5 text-sm text-left transition-all duration-200 hover:border-accent/50 hover:bg-accent/5 hover:shadow-sm active:scale-[0.98]"
          >
            <span className="text-foreground/80 group-hover:text-foreground transition-colors">
              {p}
            </span>
            <ChevronDown className="mt-1 h-3.5 w-3.5 rotate-[-90deg] text-muted-foreground/40 group-hover:text-accent/60 transition-all duration-200" />
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────
function MessageBubble({
  m,
  style,
  onCite,
  onFlag,
}: {
  m: ChatMessage;
  style?: React.CSSProperties;
  onCite: (c: { file: string; snippet: string }) => void;
  onFlag: () => void;
}) {
  const isUser = m.role === "user";

  return (
    <div
      className={`flex gap-3 msg-enter ${isUser ? "flex-row-reverse" : ""}`}
      style={style}
    >
      {/* Avatar */}
      <div
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
          isUser
            ? "bg-accent text-accent-foreground"
            : "bg-accent/15 text-accent"
        }`}
      >
        {isUser ? (
          <User className="h-4 w-4" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
      </div>

      {/* Content */}
      <div className={`min-w-0 max-w-[78%] flex flex-col ${isUser ? "items-end" : "items-start"}`}>
        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words ${
            isUser
              ? "rounded-tr-sm bg-accent text-accent-foreground"
              : "rounded-tl-sm bg-muted/70 text-foreground"
          }`}
        >
          {!isUser && !m.content && m.isGenerating ? (
            <div className="flex gap-1.5 py-1 items-center">
              <span className="typing-dot h-2 w-2 rounded-full bg-accent/70 inline-block" />
              <span className="typing-dot h-2 w-2 rounded-full bg-accent/70 inline-block" />
              <span className="typing-dot h-2 w-2 rounded-full bg-accent/70 inline-block" />
            </div>
          ) : m.content ? (
            m.content.replace(/\s*\[Source:.*?\]\s*/g, "").replace(/\s*\[Source\s+\d+:.*?\]\s*/g, "").trim()
          ) : (
            "\u00A0"
          )}
        </div>

        {/* Citations */}
        {!isUser && !m.isGenerating && m.citations && m.citations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 animate-fade-in">
            {(() => {
              const seen = new Set();
              const unique = m.citations.filter((c) => {
                if (seen.has(c.file)) return false;
                seen.add(c.file);
                return true;
              });
              return unique.map((c, i) => (
                <button
                  key={i}
                  onClick={() => onCite(c)}
                  className="inline-flex items-center gap-1 rounded-lg border border-accent/25 bg-accent/8 px-2.5 py-1 text-xs text-accent hover:bg-accent/15 transition-all duration-150"
                >
                  <FileText className="h-3 w-3" />
                  {c.file}
                </button>
              ));
            })()}
          </div>
        )}

        {/* Flag */}
        {!isUser && m.content && (
          <button
            onClick={onFlag}
            className={`mt-1 inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] transition-all duration-150 ${
              m.flagged
                ? "text-amber-500"
                : "text-muted-foreground/50 hover:text-muted-foreground"
            }`}
          >
            <Flag className="h-3 w-3" />
            {m.flagged ? "Flagged" : "Flag missing info"}
          </button>
        )}
      </div>
    </div>
  );
}
