import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { apiFetch, useBackoffPoll } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  FileText,
  MessageSquare,
  Radio,
  Sparkles,
  Activity,
  ArrowRight,
  Database,
  Eye,
  Wifi,
  WifiOff,
} from "lucide-react";

export const Route = createFileRoute("/")({
  component: Overview,
});

type HealthData = {
  api: { status: string; latency_ms: number };
  vector_db: { status: string; doc_count?: number; chunk_count?: number; latency_ms?: number; error?: string };
  watcher: { status: string };
  knowledge_base_dir: { status: string; file_count?: number; error?: string };
};

function Overview() {
  const [files, setFiles] = useState<any[]>([]);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  const loadAll = async () => {
    let online = false;
    // Documents list
    try {
      const r = await apiFetch("/api/documents");
      if (r.ok) {
        setFiles(await r.json());
        online = true;
      }
    } catch {
      /* offline */
    }
    setBackendOnline(online);

    // Live health
    try {
      const r = await apiFetch("/api/health");
      setHealth(r.ok ? ((await r.json()) as HealthData) : null);
    } catch {
      setHealth(null);
    }

    setLoading(false);
    return online;
  };

  useBackoffPoll(loadAll, { baseMs: 6000, maxMs: 45000 });

  const totalFiles = files.length;
  const indexed = files.filter((f) => f.index_status === "indexed").length;
  const totalChunks =
    health?.vector_db?.chunk_count ??
    files.reduce((s, f) => s + (f.vector_count || 0), 0);
  const meetingsCount = files.filter((f) => f.filename.startsWith("FF_")).length;

  const stats = [
    {
      label: "Documents indexed",
      value: indexed,
      sub: `${totalFiles} total files`,
      icon: FileText,
      color: "from-violet-500/20 to-violet-500/5",
      iconColor: "text-violet-500",
    },
    {
      label: "Vector chunks",
      value: loading ? null : totalChunks.toLocaleString(),
      sub: "voyage-4-large embeddings",
      icon: Sparkles,
      color: "from-indigo-500/20 to-indigo-500/5",
      iconColor: "text-indigo-500",
    },
    {
      label: "Meeting transcripts",
      value: meetingsCount,
      sub: "Auto-synced via Fireflies",
      icon: Radio,
      color: "from-sky-500/20 to-sky-500/5",
      iconColor: "text-sky-500",
    },
    {
      label: "Backend status",
      value: backendOnline === null ? "..." : backendOnline ? "Online" : "Offline",
      sub:
        backendOnline && health?.api.latency_ms != null
          ? `${health.api.latency_ms}ms latency`
          : backendOnline === false
          ? "Cannot reach backend"
          : "Connecting...",
      icon: backendOnline === false ? WifiOff : Wifi,
      color:
        backendOnline === false
          ? "from-red-500/20 to-red-500/5"
          : "from-emerald-500/20 to-emerald-500/5",
      iconColor:
        backendOnline === false ? "text-red-500" : "text-emerald-500",
    },
  ];

  return (
    <div className="mx-auto max-w-7xl space-y-8 p-6 animate-in-fade">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5 mb-1">
            <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
            {backendOnline === true && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-600 ring-1 ring-emerald-500/20">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                Live
              </span>
            )}
            {backendOnline === false && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/10 px-2.5 py-0.5 text-xs font-medium text-red-600 ring-1 ring-red-500/20">
                <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
                Backend offline
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            Your organization's collective knowledge, indexed and queryable.
          </p>
        </div>
        <Button asChild className="gap-2">
          <Link to="/chat">
            <MessageSquare className="h-4 w-4" />
            Open Assistant
          </Link>
        </Button>
      </div>

      {/* Stat cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((s) => (
          <Card
            key={s.label}
            className="card-lift relative overflow-hidden border bg-card"
          >
            <div
              className={`absolute inset-0 bg-gradient-to-br ${s.color} pointer-events-none`}
            />
            <CardHeader className="relative pb-2 pt-4 px-4">
              <div className="flex items-center justify-between">
                <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {s.label}
                </CardTitle>
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-card/80 ring-1 ring-border">
                  <s.icon className={`h-4 w-4 ${s.iconColor}`} />
                </div>
              </div>
            </CardHeader>
            <CardContent className="relative px-4 pb-4">
              <div className="text-3xl font-bold tracking-tight">
                {loading && s.value === null ? (
                  <span className="text-muted-foreground/50 text-xl">---</span>
                ) : (
                  s.value
                )}
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">{s.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Lower grid */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Recent ingestions */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between pb-3">
            <div>
              <CardTitle className="text-base">Recent ingestions</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                Latest files pushed to /backend/knowledge_base/
              </p>
            </div>
            <Button variant="ghost" size="sm" asChild className="text-xs">
              <Link to="/knowledge">
                View all <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {files.slice(0, 5).map((f) => {
              const isFireflies = f.filename.startsWith("FF_");
              const sizeStr =
                f.size > 1024 * 1024
                  ? `${(f.size / 1024 / 1024).toFixed(1)} MB`
                  : `${Math.max(1, Math.round(f.size / 1024))} KB`;
              const dateAdded = f.modified ? f.modified.slice(0, 10) : "N/A";
              return (
                <div
                  key={f.filename}
                  className="flex items-center justify-between rounded-xl border bg-muted/20 px-3 py-2.5 hover:bg-muted/40 transition-colors duration-150"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-card ring-1 ring-border">
                      <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                    </div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{f.filename}</div>
                      <div className="text-xs text-muted-foreground">
                        {isFireflies ? "Fireflies auto-sync" : "Manual upload"} &middot;{" "}
                        {dateAdded} &middot; {sizeStr}
                      </div>
                    </div>
                  </div>
                  <Badge
                    variant={f.index_status === "indexed" ? "default" : "secondary"}
                    className="ml-2 capitalize shrink-0 text-xs"
                  >
                    {f.index_status}
                  </Badge>
                </div>
              );
            })}
            {!loading && files.length === 0 && (
              <div className="py-10 text-center text-sm text-muted-foreground border-2 border-dashed rounded-xl">
                No documents yet. Go to{" "}
                <Link to="/knowledge" className="text-accent underline font-medium">
                  Knowledge Base
                </Link>{" "}
                to upload one.
              </div>
            )}
          </CardContent>
        </Card>

        {/* Live system health */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4 text-emerald-500" />
              System health
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <HealthRow
              label="Vector DB"
              value={
                health
                  ? health.vector_db.status === "connected"
                    ? "Connected"
                    : "Error"
                  : "Checking..."
              }
              ok={health?.vector_db.status === "connected"}
              sub={
                health?.vector_db.status === "connected"
                  ? `${health.vector_db.latency_ms}ms`
                  : health?.vector_db.error
              }
            />
            <HealthRow
              label="File watcher"
              value={
                health
                  ? health.watcher.status === "running"
                    ? "Running"
                    : "Stopped"
                  : "Checking..."
              }
              ok={health?.watcher.status === "running"}
            />
            <HealthRow
              label="Knowledge base dir"
              value={
                health
                  ? health.knowledge_base_dir.status === "accessible"
                    ? "Accessible"
                    : "Error"
                  : "Checking..."
              }
              ok={health?.knowledge_base_dir.status === "accessible"}
              sub={
                health?.knowledge_base_dir.file_count != null
                  ? `${health.knowledge_base_dir.file_count} files`
                  : undefined
              }
            />
            <HealthRow
              label="Embedding model"
              value="voyage-4-large"
              ok={backendOnline === true}
            />
            <HealthRow
              label="Reranker"
              value="rerank-2.5"
              ok={backendOnline === true}
            />

            {health && (
              <div className="pt-2 mt-2 border-t text-xs text-muted-foreground flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <Database className="h-3 w-3" />
                  {health.vector_db.chunk_count ?? 0} chunks stored
                </span>
                <span className="flex items-center gap-1.5">
                  <Eye className="h-3 w-3" />
                  {health.vector_db.doc_count ?? 0} docs
                </span>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function HealthRow({
  label,
  value,
  ok,
  sub,
}: {
  label: string;
  value: string;
  ok?: boolean;
  sub?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0">
        <span className="text-muted-foreground text-xs">{label}</span>
        {sub && <div className="text-[10px] text-muted-foreground/60 truncate">{sub}</div>}
      </div>
      <span className="flex items-center gap-1.5 font-medium text-xs shrink-0">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            ok === undefined
              ? "bg-muted-foreground/30"
              : ok
              ? "bg-emerald-500"
              : "bg-red-500"
          } ${ok ? "animate-pulse" : ""}`}
        />
        {value}
      </span>
    </div>
  );
}
