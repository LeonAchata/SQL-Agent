"use client";

import { useMemo, useState } from "react";
import type { SchemaInfo } from "@/lib/api";

export function SchemaPanel({ schema, error }: { schema: SchemaInfo | null; error: string | null }) {
  const [filter, setFilter] = useState("");
  const tables = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!schema) return [];
    if (!q) return schema.tables;
    return schema.tables.filter(
      (t) => t.name.toLowerCase().includes(q) || t.columns.some((c) => c.name.toLowerCase().includes(q)),
    );
  }, [schema, filter]);

  if (error) {
    return (
      <p className="p-4 text-sm text-danger">
        Could not load the schema. Is the API running?
        <span className="mt-1 block font-mono text-xs text-muted">{error}</span>
      </p>
    );
  }
  if (!schema) return <p className="p-4 text-sm text-muted">Loading schema…</p>;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="p-3">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter tables or columns"
          aria-label="Filter schema"
          className="w-full rounded-md border border-border bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent"
        />
      </div>
      <ul className="flex-1 overflow-y-auto px-2 pb-4">
        {tables.map((t) => (
          <li key={t.name}>
            <details className="group rounded-md px-2 py-1 open:bg-surface-2">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 py-1 text-sm">
                <span className="truncate font-mono">{t.name}</span>
                <span className="shrink-0 text-xs text-muted">
                  {t.kind === "view" ? "view · " : ""}
                  {t.columns.length}
                </span>
              </summary>
              {t.comment && <p className="mb-1 text-xs text-muted">{t.comment}</p>}
              <ul className="mb-1 flex flex-col gap-0.5 pl-2">
                {t.columns.map((c) => (
                  <li key={c.name} className="flex justify-between gap-3 font-mono text-xs">
                    <span className="truncate">{c.name}</span>
                    <span className="shrink-0 text-muted">{c.type.toLowerCase()}</span>
                  </li>
                ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
