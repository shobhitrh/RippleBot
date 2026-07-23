import { useCallback, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import {
  UploadCloud,
  FileText,
  RefreshCw,
  Trash2,
  Download,
  Zap,
  X,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { type KnowledgeFile } from "@/lib/mock-data";
import { apiFetch, useBackoffPoll } from "@/lib/api";

export const Route = createFileRoute("/knowledge")({
  component: KnowledgePage,
});

function KnowledgePage() {
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [pending, setPending] = useState<File[]>([]);
  const [dept, setDept] = useState("Engineering");
  const [category, setCategory] = useState<KnowledgeFile["category"]>("Policy");
  const [uploading, setUploading] = useState(false);

  const refreshFiles = useCallback(async () => {
    try {
      const response = await apiFetch("/api/documents");
      if (response.ok) {
        const data = await response.json();
        const mapped: KnowledgeFile[] = data.map((f: any) => {
          const isFireflies = f.filename.startsWith("FF_");
          const sizeStr = f.size > 1024 * 1024
            ? `${(f.size / 1024 / 1024).toFixed(1)} MB`
            : `${Math.max(1, Math.round(f.size / 1024))} KB`;
          return {
            id: f.filename,
            name: f.filename,
            source: isFireflies ? "fireflies" : "manual",
            dateAdded: f.modified ? f.modified.slice(0, 10) : new Date().toISOString().slice(0, 10),
            size: sizeStr,
            chunks: f.vector_count || 0,
            status: f.index_status === "indexed" ? "indexed" : f.index_status === "failed" ? "error" : "processing",
            errorMessage: f.error_message,
            department: f.department || "General",
            category: f.category || (isFireflies ? "Meeting" : "Policy"),
            preview: f.filename,
          };
        });
        setFiles(mapped);
        return true;
      }
      return false;
    } catch (e) {
      console.error("Failed to load documents from backend:", e);
      return false;
    }
  }, []);

  useBackoffPoll(refreshFiles, { baseMs: 3000, maxMs: 30000 });

  const handleFiles = useCallback((list: FileList | null) => {
    if (!list || list.length === 0) return;
    const incoming = Array.from(list);
    // Append to the batch, de-duping by name+size so re-dropping doesn't double up.
    setPending((cur) => {
      const seen = new Set(cur.map((f) => `${f.name}:${f.size}`));
      const merged = [...cur];
      for (const f of incoming) {
        const key = `${f.name}:${f.size}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(f);
        }
      }
      return merged;
    });
  }, []);

  const removePending = (name: string, size: number) =>
    setPending((cur) => cur.filter((f) => !(f.name === name && f.size === size)));

  const commitUpload = async () => {
    if (pending.length === 0) return;
    const batch = pending;
    setUploading(true);

    const formData = new FormData();
    for (const f of batch) formData.append("files", f);
    formData.append("department", dept);
    formData.append("category", category || "Policy");
    formData.append("uploaded_by", "User");

    // Scale the timeout to the payload — many/large files need longer than a
    // single small upload. ~4s per MB on top of a 60s floor, capped at 10 min.
    const totalBytes = batch.reduce((n, f) => n + f.size, 0);
    const timeoutMs = Math.min(600000, 60000 + Math.round((totalBytes / (1024 * 1024)) * 4000));

    toast.info(
      batch.length === 1
        ? `Uploading ${batch[0].name}…`
        : `Uploading ${batch.length} files to the knowledge base…`
    );

    try {
      const response = await apiFetch("/api/documents/upload-batch", {
        method: "POST",
        body: formData,
        timeoutMs,
      });
      if (response.ok) {
        const data = await response.json().catch(() => ({}));
        const savedN = data?.saved?.length ?? batch.length;
        const failedN = data?.failed?.length ?? 0;
        if (failedN > 0) {
          toast.warning(
            `${savedN} file(s) ingested, ${failedN} failed: ${data.failed
              .map((x: any) => x.filename)
              .join(", ")}`
          );
        } else {
          toast.success(`${savedN} file(s) uploaded. Indexing started.`);
        }
        setPending([]);
        await refreshFiles();
      } else {
        const err = await response.json().catch(() => ({}));
        toast.error(`Upload failed: ${err.detail || response.statusText}`);
      }
    } catch (e) {
      toast.error("Connection to backend failed");
    } finally {
      setUploading(false);
    }
  };

  const downloadFile = async (filename: string) => {
    // Fetch as a blob via apiFetch so the X-Company-Id header is sent (a plain
    // <a href> can't send headers → backend hits the wrong tenant → 404).
    // Works for any file format.
    try {
      const res = await apiFetch(`/api/documents/${encodeURIComponent(filename)}/download`, {
        timeoutMs: 60000,
      });
      if (!res.ok) {
        toast.error("File not found");
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success(`Downloading ${filename}...`);
    } catch {
      toast.error("Download failed — is the backend reachable?");
    }
  };

  const reindex = async (id: string) => {
    toast.info("Checking document indexing status…");
    await refreshFiles();
    toast.success("Document status refreshed.");
  };

  const remove = async (id: string) => {
    toast.info(`Deleting ${id} from knowledge base...`);
    const prev = files;
    setFiles((cur) => cur.filter((f) => f.id !== id));
    try {
      const response = await apiFetch(`/api/documents/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (response.ok) {
        toast.success("File and its embeddings deleted");
        await refreshFiles();
      } else {
        toast.error("Failed to delete file from backend");
        setFiles(prev);
      }
    } catch (e) {
      toast.error("Connection to backend failed");
      setFiles(prev);
    }
  };

  return (
    <div className="mx-auto max-w-7xl space-y-4 sm:space-y-6 p-3.5 sm:p-6 animate-in-fade">
      <div>
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">Knowledge Base</h1>
        <p className="text-xs sm:text-sm text-muted-foreground">
          Upload documents and monitor sync to the backend directory and vector store.
        </p>
      </div>

      <div className="w-full">
        <Card className="w-full">
          <CardHeader className="px-4 sm:px-6 pt-4 sm:pt-6 pb-2 sm:pb-4">
            <CardTitle className="text-base sm:text-lg">Upload documents</CardTitle>
          </CardHeader>
          <CardContent className="px-4 sm:px-6 pb-4 sm:pb-6">
            <label
              onDragOver={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragActive(false);
                handleFiles(e.dataTransfer.files);
              }}
              className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-5 sm:p-10 text-center transition ${
                dragActive
                  ? "border-accent bg-accent/10"
                  : "border-muted-foreground/25 hover:border-accent/60 hover:bg-muted/40"
              }`}
            >
              <UploadCloud className="h-7 w-7 sm:h-8 sm:w-8 text-accent" />
              <div className="text-xs sm:text-sm font-medium">
                Drop files here, or <span className="text-accent">browse</span>
              </div>
              <div className="text-[11px] sm:text-xs text-muted-foreground">
                PDF, Excel (.xlsx, .xls, .csv), Markdown, DOCX, TXT — select multiple, up to 20 MB each
              </div>
              <input
                type="file"
                multiple
                className="hidden"
                accept=".pdf,.md,.docx,.txt,.xlsx,.xls,.csv,.tsv"
                onChange={(e) => {
                  handleFiles(e.target.files);
                  e.target.value = ""; // allow re-selecting the same file(s)
                }}
              />
            </label>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="px-4 sm:px-6 pt-4 sm:pt-6 pb-2 sm:pb-4">
          <CardTitle className="text-base sm:text-lg">Knowledge directory</CardTitle>
        </CardHeader>
        <CardContent className="px-3 sm:px-6 pb-4 sm:pb-6">
          {/* Mobile Card List View */}
          <div className="space-y-3 md:hidden">
            {files.map((f) => (
              <div
                key={f.id}
                className="rounded-xl border bg-card p-3 space-y-2 shadow-xs"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-muted/60">
                      <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                    </div>
                    <div className="min-w-0">
                      <div className="font-medium text-xs truncate">{f.name}</div>
                      <div className="text-[10px] text-muted-foreground">
                        {f.dateAdded} &middot; {f.size}
                      </div>
                    </div>
                  </div>
                  <StatusBadge status={f.status} errorMessage={f.errorMessage} />
                </div>

                <div className="flex items-center justify-between pt-1.5 border-t text-xs">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                      {f.source === "fireflies" ? "Fireflies" : "Manual"}
                    </Badge>
                    <span className="text-muted-foreground text-[10px]">{f.chunks} chunks</span>
                  </div>
                  <div className="flex items-center gap-0.5">
                    <Button size="icon" variant="ghost" className="h-7 w-7" title="Download original file" onClick={() => downloadFile(f.name)}>
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="icon" variant="ghost" className="h-7 w-7" title="Re-index status" onClick={() => reindex(f.id)}>
                      <RefreshCw className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive" title="Delete document" onClick={() => remove(f.id)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </div>
            ))}
            {files.length === 0 && (
              <div className="py-8 text-center text-xs text-muted-foreground border border-dashed rounded-lg">
                No documents found.
              </div>
            )}
          </div>

          {/* Desktop Table View */}
          <div className="hidden md:block overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>File name</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Added</TableHead>
                  <TableHead>Size</TableHead>
                  <TableHead>Chunks</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {files.map((f) => (
                  <TableRow key={f.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <FileText className="h-4 w-4 text-muted-foreground" />
                        <span className="font-medium">{f.name}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {f.source === "fireflies" ? "Fireflies" : "Manual"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{f.dateAdded}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{f.size}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{f.chunks}</TableCell>
                    <TableCell>
                      <StatusBadge status={f.status} errorMessage={f.errorMessage} />
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="icon" variant="ghost" title="Download original file" onClick={() => downloadFile(f.name)}>
                          <Download className="h-4 w-4" />
                        </Button>
                        <Button size="icon" variant="ghost" title="Re-index status" onClick={() => reindex(f.id)}>
                          <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Button size="icon" variant="ghost" title="Delete document" onClick={() => remove(f.id)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={pending.length > 0} onOpenChange={(o) => !o && !uploading && setPending([])}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Tag and ingest{pending.length > 1 ? ` (${pending.length} files)` : ""}
            </DialogTitle>
            <DialogDescription>
              These tags apply to{" "}
              <span className="font-medium">
                {pending.length === 1 ? "this file" : `all ${pending.length} files`}
              </span>
              . They'll be ingested together and indexed in one pass.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4">
            <div className="max-h-44 overflow-y-auto rounded-md border divide-y">
              {pending.map((f) => (
                <div key={`${f.name}:${f.size}`} className="flex items-center gap-2 px-3 py-2">
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-sm">{f.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(f.size)}</span>
                  {!uploading && (
                    <button
                      type="button"
                      onClick={() => removePending(f.name, f.size)}
                      className="shrink-0 rounded-sm opacity-60 hover:opacity-100"
                      aria-label={`Remove ${f.name}`}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>

            <div className="grid gap-2">
              <Label>Department / Project</Label>
              <Input value={dept} onChange={(e) => setDept(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label>Category</Label>
              <Select
                value={category}
                onValueChange={(v) => setCategory(v as KnowledgeFile["category"])}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Policy">Policy</SelectItem>
                  <SelectItem value="Architecture">Architecture</SelectItem>
                  <SelectItem value="Q&A">Q&A</SelectItem>
                  <SelectItem value="Meeting">Meeting</SelectItem>
                  <SelectItem value="Other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending([])} disabled={uploading}>
              Cancel
            </Button>
            <Button onClick={commitUpload} disabled={uploading || pending.length === 0}>
              <Zap className="h-4 w-4" />
              {uploading
                ? "Ingesting…"
                : pending.length > 1
                ? `Ingest ${pending.length} files`
                : "Ingest"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function StatusBadge({ status, errorMessage }: { status: KnowledgeFile["status"]; errorMessage?: string }) {
  const map = {
    indexed: { label: "Indexed", cls: "bg-emerald-500/10 text-emerald-600 border-emerald-500/30" },
    processing: { label: "Processing", cls: "bg-amber-500/10 text-amber-600 border-amber-500/30" },
    error: { label: "Error", cls: "bg-red-500/10 text-red-600 border-red-500/30 cursor-help" },
  } as const;
  const v = map[status];
  return (
    <Badge variant="outline" className={`${v.cls} capitalize`} title={status === "error" ? (errorMessage || "Document processing failed. Click re-index to retry.") : undefined}>
      {v.label}
    </Badge>
  );
}
