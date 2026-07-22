import { useState } from "react";
import { toast } from "sonner";
import { Building2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DomainInput } from "@/components/domain-input";
import { addCompany } from "@/lib/company";

/**
 * Nice in-app modal for registering a new company (tenant), replacing the old
 * window.prompt(). Name is required; email domains are optional and can be
 * configured later from Company Settings (they start empty / NULL).
 */
export function AddCompanyDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [domains, setDomains] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const reset = () => {
    setName("");
    setDomains([]);
    setSaving(false);
  };

  const submit = async () => {
    if (!name.trim()) {
      toast.error("Company name is required");
      return;
    }
    setSaving(true);
    await addCompany(name.trim(), domains);
    toast.success(`${name.trim()} added`);
    reset();
    onOpenChange(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-accent" />
            Add company
          </DialogTitle>
          <DialogDescription>
            Create an isolated workspace. Email domains are optional — you can set
            them now or later in Company Settings.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-1">
          <div className="grid gap-2">
            <Label htmlFor="company-name">Company name</Label>
            <Input
              id="company-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Acme Corp"
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <div className="grid gap-2">
            <Label>Email domains (optional)</Label>
            <DomainInput value={domains} onChange={setDomains} />
            <p className="text-xs text-muted-foreground">
              Fireflies meetings whose attendees use these domains auto-route to this
              company. Leave empty to configure later.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving || !name.trim()}>
            {saving ? "Adding…" : "Add company"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
