import { useEffect, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { FileText, Download } from "lucide-react";
import { apiFetch, apiUrl } from "@/lib/api";

function renderParsedContent(text: string) {
  if (!text) return null;
  const lines = text.split("\n");
  const elements: React.ReactNode[] = [];

  let inTable = false;
  let tableHeaders: string[] = [];
  let tableRows: string[][] = [];

  const flushTable = (key: string | number) => {
    if (tableRows.length > 0 || tableHeaders.length > 0) {
      elements.push(
        <div
          key={`table-${key}`}
          className="my-3 overflow-x-auto rounded-lg border border-border bg-card shadow-sm max-w-full"
        >
          <table className="w-full border-collapse text-left text-xs min-w-[400px]">
            <thead>
              <tr className="border-b bg-muted/60">
                {tableHeaders.map((h, idx) => (
                  <th
                    key={idx}
                    className="p-2.5 border-r last:border-r-0 border-border font-semibold text-foreground/90 uppercase tracking-wider text-[10px]"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row, rIdx) => (
                <tr
                  key={rIdx}
                  className="border-b last:border-b-0 hover:bg-muted/30 transition-colors"
                >
                  {row.map((cell, cIdx) => (
                    <td
                      key={cIdx}
                      className="p-2 border-r last:border-r-0 border-border text-foreground/80 whitespace-nowrap"
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    tableHeaders = [];
    tableRows = [];
    inTable = false;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed.startsWith("|")) {
      const cells = line
        .split("|")
        .map((c) => c.trim())
        .filter((_, idx, arr) => idx > 0 && idx < arr.length - 1);

      // Check if it's a separator line (contains only dashes, colons, spaces, and pipes)
      const isSeparator = cells.every((c) => c.replace(/[:\-]/g, "").length === 0);

      if (isSeparator) {
        inTable = true;
        continue;
      }

      if (!inTable) {
        tableHeaders = cells;
        inTable = true;
      } else {
        tableRows.push(cells);
      }
    } else {
      if (inTable) {
        flushTable(i);
      }

      if (trimmed.startsWith("## ")) {
        elements.push(
          <h2 key={i} className="text-sm font-bold mt-4 mb-2 text-foreground border-b pb-1">
            {trimmed.slice(3)}
          </h2>
        );
      } else if (trimmed.startsWith("# ")) {
        elements.push(
          <h1 key={i} className="text-base font-bold mt-5 mb-2.5 text-foreground border-b pb-1.5">
            {trimmed.slice(2)}
          </h1>
        );
      } else if (trimmed) {
        elements.push(
          <div
            key={i}
            className="font-mono text-[11px] leading-relaxed text-foreground/80 whitespace-pre-wrap"
          >
            {line}
          </div>
        );
      } else {
        elements.push(<div key={i} className="h-1.5" />);
      }
    }
  }

  if (inTable) {
    flushTable(lines.length);
  }

  return elements;
}

export function SourceDrawer({
  file,
  snippet,
  open,
  onOpenChange,
}: {
  file: string | null;
  snippet?: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [previewText, setPreviewText] = useState("Loading preview...");

  useEffect(() => {
    if (!open || !file) return;
    
    setPreviewText("Loading preview...");
    const fetchPreview = async () => {
      try {
        const response = await apiFetch(`/api/documents/${encodeURIComponent(file)}/preview`);
        if (response.ok) {
          const data = await response.json();
          setPreviewText(data.preview || "Preview unavailable.");
        } else {
          setPreviewText("Document preview unavailable.");
        }
      } catch (e) {
        setPreviewText("Connection to backend failed. Could not load preview.");
      }
    };
    
    fetchPreview();
  }, [file, open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-xl md:max-w-2xl lg:max-w-3xl overflow-y-auto">
        <SheetHeader>
          <SheetTitle className="flex items-center justify-between w-full">
            <span className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-indigo-500" />
              {file ?? "Source"}
            </span>
            {file && (
              <a
                href={apiUrl(`/api/documents/${encodeURIComponent(file)}/download`)}
                download
                className="inline-flex items-center gap-1.5 text-xs font-normal text-indigo-500 hover:text-indigo-600 transition-colors mr-6 border border-indigo-200 rounded-md px-2.5 py-1.5 bg-indigo-50/50 hover:bg-indigo-50"
              >
                <Download className="h-3.5 w-3.5" />
                Download Original
              </a>
            )}
          </SheetTitle>
          <SheetDescription>
            Retrieved section from the ingested knowledge base.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 space-y-4 px-4 pb-6">
          {snippet && (
            <div className="rounded-md border-l-2 border-indigo-500 bg-muted/40 p-3 text-sm">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Matched snippet
              </p>
              <p className="mt-1 whitespace-pre-wrap">{snippet}</p>
            </div>
          )}
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
              Full document preview
            </p>
            <div className="rounded-md border bg-muted/30 p-4 max-h-[70vh] overflow-y-auto">
              {renderParsedContent(previewText)}
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
