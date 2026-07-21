import { useEffect, useState } from "react";
import { Search, Circle } from "lucide-react";
import { apiFetch, useBackoffPoll } from "@/lib/api";
import { CompanySelector } from "@/components/company-selector";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useNavigate } from "@tanstack/react-router";

export function AppHeader() {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<any[]>([]);
  const [backendStatus, setBackendStatus] = useState<"active" | "error" | "indexing">("indexing");
  const [vectorStatus, setVectorStatus] = useState<"active" | "error" | "indexing">("indexing");
  const navigate = useNavigate();

  const ping = async () => {
    try {
      const r = await apiFetch("/api/health", { timeoutMs: 8000 });
      if (r.ok) {
        const d = await r.json();
        setBackendStatus("active");
        setVectorStatus(d?.vector_db?.status === "connected" ? "active" : "error");
        return true;
      }
    } catch {
      /* fall through to offline state */
    }
    setBackendStatus("error");
    setVectorStatus("error");
    return false;
  };

  useBackoffPoll(ping, { baseMs: 10000, maxMs: 60000 });

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => {
    if (!open) return;
    const fetchSearchData = async () => {
      try {
        const res = await apiFetch("/api/documents");
        if (res.ok) {
          const data = await res.json();
          setFiles(data);
        }
      } catch (e) {
        console.error("Failed to load search data:", e);
      }
    };
    fetchSearchData();
  }, [open]);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-1.5 border-b bg-background/80 px-2.5 backdrop-blur-sm sm:gap-3 sm:px-4">
      <SidebarTrigger className="shrink-0" />
      <Separator orientation="vertical" className="hidden h-6 sm:block" />
      <button
        onClick={() => setOpen(true)}
        className="flex h-9 min-w-0 flex-1 max-w-md items-center gap-1.5 rounded-lg border bg-muted/40 px-2.5 text-xs sm:text-sm text-muted-foreground transition-all duration-150 hover:bg-muted hover:border-border/60"
      >
        <Search className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0" />
        <span className="flex-1 truncate text-left">
          <span className="sm:hidden">Search…</span>
          <span className="hidden sm:inline">Search files, meetings, prompts...</span>
        </span>
        <kbd className="rounded border bg-background px-1.5 py-0.5 text-[10px] font-mono hidden sm:block">
          Ctrl+K
        </kbd>
      </button>

      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        <CompanySelector />
        <StatusPill label="API" status={backendStatus} text={backendStatus === "active" ? "Online" : backendStatus === "error" ? "Offline" : "..."} />
        <StatusPill label="Vector DB" status={vectorStatus} text={vectorStatus === "active" ? "Active" : vectorStatus === "error" ? "Error" : "..."} />
        <Avatar className="h-7 w-7 sm:h-8 sm:w-8 shrink-0">
          <AvatarFallback className="bg-accent text-accent-foreground text-[10px] sm:text-xs font-semibold">
            AC
          </AvatarFallback>
        </Avatar>
      </div>

      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput placeholder="Search files, meetings, and actions..." />
        <CommandList>
          <CommandEmpty>No results.</CommandEmpty>
          <CommandGroup heading="Navigate">
            <CommandItem onSelect={() => { setOpen(false); navigate({ to: "/chat" }); }}>
              Open Chatbot Assistant
            </CommandItem>
            <CommandItem onSelect={() => { setOpen(false); navigate({ to: "/knowledge" }); }}>
              Knowledge Base
            </CommandItem>
            <CommandItem onSelect={() => { setOpen(false); navigate({ to: "/meetings" }); }}>
              Meeting Logs
            </CommandItem>
          </CommandGroup>
          <CommandGroup heading="Files">
            {files.filter((f) => !f.filename.startsWith("FF_")).slice(0, 5).map((f) => (
              <CommandItem key={f.filename} onSelect={() => { setOpen(false); navigate({ to: "/knowledge" }); }}>
                {f.filename}
              </CommandItem>
            ))}
            {files.filter((f) => !f.filename.startsWith("FF_")).length === 0 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">No files uploaded yet.</div>
            )}
          </CommandGroup>
          <CommandGroup heading="Meetings">
            {files.filter((f) => f.filename.startsWith("FF_")).slice(0, 5).map((f) => {
              const cleanName = f.filename.replace(/^FF_/, "").replace(/\.md$/, "").replace(/_/g, " ");
              return (
                <CommandItem key={f.filename} onSelect={() => { setOpen(false); navigate({ to: "/meetings" }); }}>
                  {cleanName}
                </CommandItem>
              );
            })}
            {files.filter((f) => f.filename.startsWith("FF_")).length === 0 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">No meetings synced yet.</div>
            )}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </header>
  );
}

function StatusPill({
  label,
  status,
  text,
}: {
  label: string;
  status: "active" | "indexing" | "error";
  text: string;
}) {
  const dotColor =
    status === "active"
      ? "text-emerald-500"
      : status === "indexing"
      ? "text-amber-500"
      : "text-red-500";

  const pulse = status === "active";

  return (
    <div className="hidden md:flex items-center gap-1.5 rounded-lg border bg-card px-2.5 py-1 text-xs transition-all duration-300">
      <span className={`relative flex h-2 w-2 ${pulse ? "animate-pulse" : ""}`}>
        <Circle className={`h-2 w-2 fill-current ${dotColor}`} />
      </span>
      <span className="text-muted-foreground">{label}:</span>
      <span className="font-medium">{text}</span>
    </div>
  );
}
