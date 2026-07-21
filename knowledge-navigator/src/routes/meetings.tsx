import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Calendar, Clock, Users, RefreshCw, FileText } from "lucide-react";
import { mockMeetings, type Meeting } from "@/lib/mock-data";
import { SourceDrawer } from "@/components/source-drawer";
import { apiFetch, useBackoffPoll } from "@/lib/api";

export const Route = createFileRoute("/meetings")({
  component: MeetingsPage,
});

function MeetingsPage() {
  const [preview, setPreview] = useState<string | null>(null);
  const [meetings, setMeetings] = useState<Meeting[]>([]);

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
    <div className="mx-auto max-w-7xl space-y-6 p-6 animate-in-fade">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Fireflies Meeting Logs</h1>
          <p className="text-sm text-muted-foreground">
            Auto-ingested transcripts, converted to structured Markdown.
          </p>
        </div>
        <Badge variant="outline" className="gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-ping" />
          Webhook connected
        </Badge>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {meetings.map((m) => (
          <Card key={m.id} className="group hover:border-accent/50 transition-all duration-300">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="text-base leading-snug">{m.title}</CardTitle>
                <Badge variant="secondary" className="shrink-0 text-xs">
                  Fireflies
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">{m.summary}</p>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
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
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => setPreview(m.markdownFile)}>
                  <FileText className="h-3.5 w-3.5" />
                  View Cleaned MD
                </Button>
                <Button size="sm" variant="ghost" onClick={() => resummarize(m.title)}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  Re-summarize
                </Button>
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
