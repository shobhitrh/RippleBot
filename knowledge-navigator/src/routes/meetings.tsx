import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Calendar, Clock, Users, RefreshCw, FileText, DownloadCloud } from "lucide-react";
import { mockMeetings, type Meeting } from "@/lib/mock-data";
import { SourceDrawer } from "@/components/source-drawer";
import { apiFetch, useBackoffPoll } from "@/lib/api";
import { useCompany } from "@/lib/company";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/meetings")({
  component: MeetingsPage,
});

function MeetingsPage() {
  const [preview, setPreview] = useState<string | null>(null);
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [selectedCompany, companies] = useCompany();

  const assign = async (filename: string, target: string) => {
    toast.info(`Assigning meeting to ${target}…`);
    try {
      const fd = new FormData();
      fd.append("target_company_id", target);
      // apiFetch attaches X-Company-Id (the source tenant) automatically.
      const res = await apiFetch(`/api/documents/${encodeURIComponent(filename)}/assign`, {
        method: "POST",
        body: fd,
        timeoutMs: 60000,
      });
      if (res.ok) {
        toast.success(`Meeting assigned to ${target}`);
        setMeetings((cur) => cur.filter((m) => m.markdownFile !== filename)); // optimistic
        await fetchMeetings();
      } else {
        toast.error("Assign failed");
      }
    } catch {
      toast.error("Backend connection failed");
    }
  };

  const fetchMeetings = async () => {
    try {
      const response = await apiFetch("/api/documents");
      if (response.ok) {
        const data = await response.json();

        // Filter out meeting files (starting with FF_)
        const meetingFiles = data.filter((f: any) => f.filename.startsWith("FF_"));

        if (meetingFiles.length > 0) {
          const mapped: Meeting[] = meetingFiles.map((f: any, idx: number) => {
            const filename = f.filename;
            // Parse filename e.g. FF_Auth_Sync_2026-03-14.md
            const cleanName = filename.replace(/^FF_/, "").replace(/\.md$/, "");
            const parts = cleanName.split("_");
            const datePart = parts.length > 1 ? parts[parts.length - 1] : f.modified.slice(0, 10);
            
            // Reconstruct title
            const titleParts = parts.length > 1 ? parts.slice(0, -1) : parts;
            const title = titleParts.join(" ");
            
            return {
              id: filename,
              title: title,
              date: datePart,
              duration: "Auto-synced",
              participants: f.uploaded_by ? [f.uploaded_by] : ["Sync Agent"],
              markdownFile: filename,
              summary: `Auto-summarized transcript. Ingested on ${f.modified ? f.modified.slice(0, 10) : "N/A"}.`
            };
          });
          setMeetings(mapped);
        } else {
          setMeetings([]);
        }
        return true;
      }
      setMeetings([]);
      return false;
    } catch (e) {
      console.error("Failed to fetch meetings from backend:", e);
      setMeetings([]);
      return false;
    }
  };

  useBackoffPoll(fetchMeetings, { baseMs: 5000, maxMs: 45000 });

  const importMeeting = async () => {
    const id = window.prompt(
      "Fireflies transcript ID to import into this company:\n(find it in the meeting's Fireflies URL, or via the API)"
    );
    if (!id || !id.trim()) return;
    toast.info(`Importing meeting ${id.trim()} into ${selectedCompany}…`);
    try {
      const fd = new FormData();
      fd.append("meeting_id", id.trim());
      const res = await apiFetch("/api/documents/import-fireflies", {
        method: "POST",
        body: fd,
        timeoutMs: 60000,
      });
      if (res.ok) {
        toast.success("Import started — the meeting will appear once indexed.");
        setTimeout(fetchMeetings, 4000);
      } else {
        toast.error("Import failed");
      }
    } catch {
      toast.error("Backend connection failed");
    }
  };

  const resummarize = async (meetingId: string) => {
    toast.info(`Triggering re-summarization webhook for ${meetingId}…`);
    try {
      const response = await apiFetch("/api/webhooks/fireflies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ meetingId: meetingId.replace(/\s+/g, "_") })
      });
      if (response.ok) {
        toast.success("Webhook re-triggered. Summarization job is running in background.");
        await fetchMeetings();
      } else {
        toast.error("Failed to trigger re-summarization");
      }
    } catch (e) {
      toast.error("Backend connection failed");
    }
  };

  return (
    <div className="mx-auto max-w-7xl space-y-4 sm:space-y-6 p-3.5 sm:p-6 animate-in-fade">
      <div className="flex flex-col xs:flex-row xs:items-start justify-between gap-2 sm:gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">Fireflies Meeting Logs</h1>
          <p className="text-xs sm:text-sm text-muted-foreground">
            Auto-ingested transcripts, converted to structured Markdown.
          </p>
        </div>
        <div className="flex items-center gap-2 self-start shrink-0">
          <Button size="sm" variant="outline" className="text-xs" onClick={importMeeting}>
            <DownloadCloud className="h-3.5 w-3.5" />
            Import by ID
          </Button>
          <Badge variant="outline" className="gap-1.5 text-xs">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-ping" />
            Webhook connected
          </Badge>
        </div>
      </div>

      <div className="grid gap-3 sm:gap-4 md:grid-cols-2">
        {meetings.map((m) => (
          <Card key={m.id} className="group hover:border-accent/50 transition-all duration-300">
            <CardHeader className="p-4 sm:p-6 pb-2 sm:pb-3">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="text-sm sm:text-base leading-snug">{m.title}</CardTitle>
                <Badge variant="secondary" className="shrink-0 text-[10px] sm:text-xs">
                  Fireflies
                </Badge>
              </div>
              <p className="text-xs sm:text-sm text-muted-foreground mt-1">{m.summary}</p>
            </CardHeader>
            <CardContent className="p-4 sm:p-6 pt-0 space-y-3">
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] sm:text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Calendar className="h-3.5 w-3.5" /> {m.date}
                </span>
                <span className="flex items-center gap-1">
                  <Clock className="h-3.5 w-3.5" /> {m.duration}
                </span>
                <span className="flex items-center gap-1">
                  <Users className="h-3.5 w-3.5" /> {m.participants.join(", ")}
                </span>
              </div>
              <div className="flex flex-col sm:flex-row gap-2 pt-1">
                <Button size="sm" variant="outline" className="w-full sm:w-auto text-xs" onClick={() => setPreview(m.markdownFile)}>
                  <FileText className="h-3.5 w-3.5" />
                  View Cleaned MD
                </Button>
                <Button size="sm" variant="ghost" className="w-full sm:w-auto text-xs" onClick={() => resummarize(m.title)}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  Re-summarize
                </Button>
                <Select value="" onValueChange={(v) => v && assign(m.markdownFile, v)}>
                  <SelectTrigger className="h-8 w-full sm:w-[150px] text-xs" aria-label="Assign to company">
                    <SelectValue placeholder={selectedCompany === "unassigned" ? "Assign to…" : "Move to…"} />
                  </SelectTrigger>
                  <SelectContent>
                    {companies
                      .filter((c) => c.id !== selectedCompany)
                      .map((c) => (
                        <SelectItem key={c.id} value={c.id} className="text-xs">
                          {c.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      {meetings.length === 0 && (
        <div className="py-12 text-center text-sm text-muted-foreground border-2 border-dashed rounded-lg">
          No meeting logs synced yet. Trigger the Fireflies webhook or upload a transcript file matching <code className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono">FF_[Title]_[Date].md</code>.
        </div>
      )}

      <SourceDrawer
        open={!!preview}
        onOpenChange={(o) => !o && setPreview(null)}
        file={preview}
      />
    </div>
  );
}
