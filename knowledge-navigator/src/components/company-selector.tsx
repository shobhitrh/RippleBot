import { Building2, Plus } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCompany, setSelectedCompany, addCompany } from "@/lib/company";

const ADD_NEW = "__add_new__";

/**
 * Global tenant switcher. Selecting a company sets the X-Company-Id sent with
 * every API call and scopes the chat history — giving each company a fully
 * isolated workspace.
 */
export function CompanySelector() {
  const [selected, companies] = useCompany();

  const onChange = (value: string) => {
    if (value === ADD_NEW) {
      const name = window.prompt("New company name:");
      if (name && name.trim()) addCompany(name.trim());
      return;
    }
    setSelectedCompany(value);
  };

  return (
    <Select value={selected} onValueChange={onChange}>
      <SelectTrigger
        className="h-9 w-[105px] xs:w-[125px] sm:w-[190px] gap-1 sm:gap-2 border-border/70 bg-card text-xs sm:text-sm px-2 sm:px-3 shrink-0"
        aria-label="Select company"
      >
        <Building2 className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0 text-accent" />
        <SelectValue placeholder="Select company" />
      </SelectTrigger>
      <SelectContent>
        {companies.map((c) => (
          <SelectItem key={c.id} value={c.id}>
            {c.name}
          </SelectItem>
        ))}
        <SelectItem value={ADD_NEW} className="text-accent">
          <span className="flex items-center gap-1.5">
            <Plus className="h-3.5 w-3.5" /> Add company…
          </span>
        </SelectItem>
      </SelectContent>
    </Select>
  );
}
