import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Building2, Plus, Save, Globe } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DomainInput } from "@/components/domain-input";
import { AddCompanyDialog } from "@/components/add-company-dialog";
import { useCompany, updateCompany, type Company } from "@/lib/company";

export const Route = createFileRoute("/settings")({
  component: CompanySettingsPage,
});

function CompanySettingsPage() {
  const [, companies] = useCompany();
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div className="mx-auto max-w-4xl space-y-4 sm:space-y-6 p-3.5 sm:p-6 animate-in-fade">
      <div className="flex flex-col xs:flex-row xs:items-start justify-between gap-2 sm:gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">Company Settings</h1>
          <p className="text-xs sm:text-sm text-muted-foreground">
            Each company is an isolated workspace. Configure the email domains used to
            auto-route Fireflies meetings — changes save straight to the backend.
          </p>
        </div>
        <Button size="sm" className="self-start shrink-0 text-xs" onClick={() => setAddOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          Add company
        </Button>
      </div>

      <div className="space-y-3 sm:space-y-4">
        {companies.map((c) => (
          <CompanyCard key={c.id} company={c} />
        ))}
      </div>

      <AddCompanyDialog open={addOpen} onOpenChange={setAddOpen} />
    </div>
  );
}

function CompanyCard({ company }: { company: Company }) {
  const [name, setName] = useState(company.name);
  const [domains, setDomains] = useState<string[]>(company.domains ?? []);
  const [saving, setSaving] = useState(false);

  // Re-sync local draft if the backing record changes (e.g. after a refresh).
  useEffect(() => {
    setName(company.name);
    setDomains(company.domains ?? []);
  }, [company.name, JSON.stringify(company.domains)]);

  const dirty =
    name.trim() !== company.name ||
    JSON.stringify(domains) !== JSON.stringify(company.domains ?? []);

  const save = async () => {
    if (!name.trim()) {
      toast.error("Company name can't be empty");
      return;
    }
    setSaving(true);
    const ok = await updateCompany(company.id, { name: name.trim(), domains });
    setSaving(false);
    if (ok) toast.success(`${name.trim()} settings saved`);
    else toast.error("Save failed — check the backend connection");
  };

  return (
    <Card>
      <CardHeader className="p-4 sm:p-6 pb-2 sm:pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
            <Building2 className="h-4 w-4 text-accent" />
            {company.name}
          </CardTitle>
          <Badge variant="outline" className="font-mono text-[10px] sm:text-xs">
            {company.id}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="p-4 sm:p-6 pt-0 space-y-4">
        <div className="grid gap-2">
          <Label className="text-xs sm:text-sm">Display name</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="text-sm"
          />
        </div>

        <div className="grid gap-2">
          <Label className="flex items-center gap-1.5 text-xs sm:text-sm">
            <Globe className="h-3.5 w-3.5 text-muted-foreground" />
            Email domains (Fireflies auto-routing)
          </Label>
          <DomainInput value={domains} onChange={setDomains} />
          {domains.length === 0 ? (
            <p className="text-[11px] sm:text-xs text-muted-foreground">
              No domains set. Meetings won't auto-route to {company.name} until you add at
              least one (e.g. <code className="rounded bg-muted px-1 py-0.5 font-mono">{company.id}.com</code>).
            </p>
          ) : (
            <p className="text-[11px] sm:text-xs text-muted-foreground">
              Fireflies meetings whose attendees use these domains are filed under{" "}
              {company.name} automatically.
            </p>
          )}
        </div>

        <div className="flex justify-end">
          <Button size="sm" className="text-xs" onClick={save} disabled={!dirty || saving}>
            <Save className="h-3.5 w-3.5" />
            {saving ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
