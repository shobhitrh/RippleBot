import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

/**
 * Tag-style editor for a company's email-domain map. Type a domain and press
 * Enter or comma to add it; click × on a chip to remove. Values are normalised
 * (lower-cased, leading "@" stripped). These domains drive Fireflies auto-routing.
 */
export function DomainInput({
  value,
  onChange,
  placeholder = "pinelabs.com",
}: {
  value: string[];
  onChange: (domains: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");

  const normalize = (raw: string) =>
    raw.trim().toLowerCase().replace(/^@+/, "").replace(/,$/, "");

  const commit = (raw: string) => {
    const d = normalize(raw);
    if (!d) return;
    if (!value.includes(d)) onChange([...value, d]);
    setDraft("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(draft);
    } else if (e.key === "Backspace" && !draft && value.length) {
      onChange(value.slice(0, -1));
    }
  };

  const remove = (d: string) => onChange(value.filter((x) => x !== d));

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2 py-1.5 min-h-9 focus-within:ring-2 focus-within:ring-ring">
      {value.map((d) => (
        <Badge key={d} variant="secondary" className="gap-1 text-xs font-normal">
          {d}
          <button
            type="button"
            onClick={() => remove(d)}
            className="rounded-sm opacity-60 hover:opacity-100"
            aria-label={`Remove ${d}`}
          >
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => commit(draft)}
        placeholder={value.length ? "" : placeholder}
        className="h-6 flex-1 min-w-[120px] border-0 bg-transparent p-0 text-sm shadow-none focus-visible:ring-0"
      />
    </div>
  );
}
