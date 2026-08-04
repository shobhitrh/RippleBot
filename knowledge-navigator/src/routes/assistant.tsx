import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Sparkles, Send, User, Database, BookOpen, Mic, MicOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { streamAgenticQuery, getAgenticStatus, type AgenticStatus } from "@/lib/agentic";
import { toast } from "sonner";

export const Route = createFileRoute("/assistant")({
  component: AssistantView,
});

type Msg = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: { file: string; snippet: string }[];
  generating?: boolean;
};

// The Assistant (PIA) is CROSS-TENANT and internal: help center (shared) + live data
// (fans out across all tenant schemas). It is intentionally NOT scoped to the company
// selector, that avoids the "asking about Axis while Pine Labs is selected" confusion.
const CROSS_TENANT_SCOPE = "default";

function renderInline(text: string) {
  const parts = text.split(/(\*\*.*?\*\*)/g);
  return parts.map((part, pIdx) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
      return (
        <strong key={pIdx} className="font-semibold text-foreground">
          {part.slice(2, -2)}
        </strong>
      );
    }
    const starParts = part.split(/(★)/g);
    return starParts.map((sp, sIdx) => {
      if (sp === "★") {
        return (
          <span key={`${pIdx}-${sIdx}`} className="star-rating text-sm">★</span>
        );
      }
      return sp;
    });
  });
}

function isTableSeparator(line: string) {
  return /^\|[\s\-:|]+\|$/.test(line);
}

function parseTableRow(line: string) {
  return line
    .split("|")
    .slice(1, -1)
    .map((cell) => cell.trim());
}

function FormattedContent({ text }: { text: string }) {
  const lines = text.split("\n");
  const elements: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Empty line
    if (!trimmed) {
      elements.push(<div key={i} className="h-1.5" />);
      i++;
      continue;
    }

    // Headers: ## or ###
    const headerMatch = trimmed.match(/^#{2,3}\s+(.*)/);
    if (headerMatch) {
      elements.push(
        <div key={i} className="mt-3 mb-1 text-sm font-semibold text-foreground">
          {renderInline(headerMatch[1])}
        </div>
      );
      i++;
      continue;
    }

    // Horizontal rule
    if (/^---+$/.test(trimmed)) {
      elements.push(
        <div key={i} className="my-2 border-t border-border" />
      );
      i++;
      continue;
    }

    // Table detection
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|") && lines[i].trim().endsWith("|")) {
        tableLines.push(lines[i].trim());
        i++;
      }

      if (tableLines.length >= 2) {
        const headerCells = parseTableRow(tableLines[0]);
        const hasSeparator = tableLines.length >= 2 && isTableSeparator(tableLines[1]);
        const bodyStart = hasSeparator ? 2 : 1;
        const bodyRows = tableLines.slice(bodyStart);

        elements.push(
          <div key={`table-${i}`} className="my-2 overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  {headerCells.map((cell, ci) => (
                    <th key={ci} className="px-3 py-2 text-left font-medium text-foreground">
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bodyRows.map((row, ri) => {
                  const cells = parseTableRow(row);
                  return (
                    <tr key={ri} className="border-b border-border last:border-0">
                      {cells.map((cell, ci) => (
                        <td key={ci} className="px-3 py-2 text-muted-foreground">
                          {renderInline(cell)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
        continue;
      }
    }

    // Bullet point
    const isBullet = trimmed.startsWith("* ") || trimmed.startsWith("- ") || trimmed.startsWith("• ");
    let content = trimmed;
    if (isBullet) {
      content = trimmed.replace(/^[\*\-\•]\s*/, "");
    }

    if (isBullet) {
      elements.push(
        <div key={i} className="flex items-start gap-2 pl-0.5">
          <span className="text-accent font-bold mt-0.5 select-none text-xs">•</span>
          <div className="flex-1 min-w-0">{renderInline(content)}</div>
        </div>
      );
    } else {
      elements.push(
        <div key={i} className="leading-relaxed">{renderInline(trimmed)}</div>
      );
    }

    i++;
  }

  return <div className="space-y-1">{elements}</div>;
}

function AssistantView() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<AgenticStatus | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Speech-to-Text
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);

  const stopListening = () => {
    try {
      recognitionRef.current?.stop();
    } catch {
      /* already stopped */
    }
    setIsListening(false);
  };

  const toggleListening = () => {
    if (isListening) {
      stopListening();
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

  useEffect(() => {
    getAgenticStatus().then(setStatus);
  }, []);

  useEffect(() => {
    const v = scrollRef.current?.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]");
    (v ?? scrollRef.current)?.scrollTo({ top: 9e9, behavior: "auto" });
  }, [messages]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    setInput("");

    const userMsg: Msg = { id: crypto.randomUUID(), role: "user", content: q };
    const aId = crypto.randomUUID();
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((cur) => [...cur, userMsg, { id: aId, role: "assistant", content: "", generating: true }]);

    let acc = "";
    await streamAgenticQuery(
      q,
      {
        onSources: (sources) =>
          setMessages((cur) => cur.map((m) => (m.id === aId ? { ...m, sources } : m))),
        onToken: (t) => {
          acc += t;
          setMessages((cur) => cur.map((m) => (m.id === aId ? { ...m, content: acc } : m)));
        },
        onDone: () =>
          setMessages((cur) => cur.map((m) => (m.id === aId ? { ...m, generating: false } : m))),
        onError: () =>
          setMessages((cur) =>
            cur.map((m) =>
              m.id === aId
                ? { ...m, generating: false, content: acc || "Connection to the assistant failed." }
                : m
            )
          ),
      },
      { history, companyId: CROSS_TENANT_SCOPE }
    );
    setBusy(false);
  };

  const unavailable = status && !(status.agentic_mode && status.orchestrator.configured);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-accent" />
          <h1 className="text-lg font-semibold">Assistant · Product Intelligence</h1>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Internal, cross-tenant. Answers <b>how-to</b> from the help center and <b>live data</b>{" "}
          across every tenant, not scoped to the selected company.
        </p>
      </div>

      {unavailable && (
        <div className="mx-4 mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs">
          The agentic engine isn't active. Set <code>AGENTIC_MODE=on</code> and{" "}
          <code>ANTHROPIC_API_KEY</code> on the backend.
        </div>
      )}

      {/* Messages */}
      <ScrollArea ref={scrollRef} className="flex-1 px-4">
        <div className="mx-auto max-w-3xl space-y-4 py-4">
          {messages.length === 0 && (
            <div className="mt-10 text-center">
              <Sparkles className="mx-auto h-8 w-8 text-accent" />
              <h2 className="mt-3 text-xl font-semibold">Ask the Product Intelligence assistant</h2>
              <div className="mx-auto mt-4 grid max-w-lg gap-2 text-left text-sm">
                <button
                  onClick={() => send("How do I set up referral tracking?")}
                  className="flex items-center gap-2 rounded-lg border px-3 py-2 hover:bg-muted"
                >
                  <BookOpen className="h-4 w-4 text-accent" /> How do I set up referral tracking?
                </button>
                <button
                  onClick={() => send("What is Axis's referral bonus config?")}
                  className="flex items-center gap-2 rounded-lg border px-3 py-2 hover:bg-muted"
                >
                  <Database className="h-4 w-4 text-accent" /> What is Axis's referral bonus config?
                </button>
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="flex items-start gap-2">
                  <div className="rounded-2xl bg-accent px-4 py-2 text-accent-foreground">{m.content}</div>
                  <User className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" />
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex items-start gap-2">
                <Sparkles className="mt-1 h-5 w-5 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="rounded-2xl bg-muted px-4 py-2 text-sm">
                    {m.content ? (
                      <FormattedContent text={m.content} />
                    ) : m.generating ? (
                      <div className="flex gap-1.5 py-1 items-center">
                        <span className="typing-dot h-2 w-2 rounded-full bg-accent/70 inline-block" />
                        <span className="typing-dot h-2 w-2 rounded-full bg-accent/70 inline-block" />
                        <span className="typing-dot h-2 w-2 rounded-full bg-accent/70 inline-block" />
                      </div>
                    ) : null}
                  </div>
                  {m.sources && m.sources.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {m.sources.map((s, i) => (
                        <span
                          key={i}
                          title={s.snippet}
                          className="rounded-md border bg-card px-2 py-0.5 text-[11px] text-muted-foreground"
                        >
                          {s.file}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          )}
        </div>
      </ScrollArea>

      {/* Composer */}
      <div className="border-t px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-xl border bg-card px-3 py-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder="Ask about how-to guides or live tenant data…"
            className="min-h-[40px] max-h-36 resize-none border-0 bg-transparent p-1 text-sm shadow-none focus-visible:ring-0"
          />
          <Button
            type="button"
            variant="ghost"
            onClick={toggleListening}
            className={`h-8 w-8 shrink-0 rounded-lg transition-all duration-200 ${
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
          <Button size="icon" onClick={() => send(input)} disabled={!input.trim() || busy} className="shrink-0">
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <p className="mt-1 text-center text-[11px] text-muted-foreground/60">
          Click <Mic className="inline h-3 w-3 text-accent" /> for voice input · Enter to send
        </p>
      </div>
    </div>
  );
}
