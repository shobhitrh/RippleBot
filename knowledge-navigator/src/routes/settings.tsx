import { createFileRoute } from "@tanstack/react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import { Key, Eye, EyeOff, Copy } from "lucide-react";
import { useState } from "react";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const [show, setShow] = useState(false);
  const key = "sk_live_ksai_••••••••••••••8f2a";
  return (
    <div className="mx-auto max-w-4xl space-y-4 sm:space-y-6 p-3.5 sm:p-6 animate-in-fade">
      <div>
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">Settings & API Keys</h1>
        <p className="text-xs sm:text-sm text-muted-foreground">
          Manage integrations, embedding pipeline, and access.
        </p>
      </div>

      <Card>
        <CardHeader className="p-4 sm:p-6 pb-2 sm:pb-4">
          <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
            <Key className="h-4 w-4 text-accent" />
            API Keys
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 sm:p-6 pt-0 space-y-4">
          <div className="grid gap-2">
            <Label className="text-xs sm:text-sm">RippleBot API key</Label>
            <div className="flex gap-1.5 sm:gap-2 min-w-0">
              <Input
                readOnly
                value={show ? "sk_live_ksai_ab98cd12ef34gh56ij78kl90mn8f2a" : key}
                className="font-mono text-xs sm:text-sm truncate min-w-0 flex-1"
              />
              <Button variant="outline" size="icon" className="h-9 w-9 shrink-0" onClick={() => setShow((v) => !v)}>
                {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="h-9 w-9 shrink-0"
                onClick={() => {
                  navigator.clipboard.writeText("sk_live_ksai_ab98cd12ef34gh56ij78kl90mn8f2a");
                  toast.success("API key copied");
                }}
              >
                <Copy className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="grid gap-2">
            <Label className="text-xs sm:text-sm">Fireflies webhook secret</Label>
            <Input readOnly value="whsec_ff_•••••••••••••7a2c" className="font-mono text-xs sm:text-sm" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="p-4 sm:p-6 pb-2 sm:pb-4">
          <CardTitle className="text-base sm:text-lg">Ingestion pipeline</CardTitle>
        </CardHeader>
        <CardContent className="p-4 sm:p-6 pt-0 space-y-3.5 sm:space-y-4">
          <Row title="Auto-sync Fireflies transcripts" description="Ingest new meetings on webhook receipt." defaultChecked />
          <Separator />
          <Row title="Chunk overlap" description="Improve retrieval by overlapping semantic chunks." defaultChecked />
          <Separator />
          <Row title="Nightly full re-index" description="Rebuild vector store at 03:00 UTC." />
          <Separator />
          <Row title="Notify admins on Missing Info flags" description="Send email when users flag outdated answers." defaultChecked />
        </CardContent>
      </Card>
    </div>
  );
}

function Row({
  title,
  description,
  defaultChecked,
}: {
  title: string;
  description: string;
  defaultChecked?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="text-xs sm:text-sm font-medium">{title}</div>
        <div className="text-[11px] sm:text-xs text-muted-foreground">{description}</div>
      </div>
      <Switch defaultChecked={defaultChecked} className="shrink-0" />
    </div>
  );
}
