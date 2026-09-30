import type { QueryResult } from "@/lib/api";

const MAX_VISIBLE = 100;

export function ResultTable({ result }: { result: QueryResult }) {
  if (result.row_count === 0) {
    return <p className="rounded-md bg-surface-2 p-3 text-sm text-muted">No rows returned.</p>;
  }
  return (
    <div className="max-h-96 overflow-auto rounded-md border border-border">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="sticky top-0 bg-surface-2">
          <tr>
            {result.columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.slice(0, MAX_VISIBLE).map((row, i) => (
            <tr key={i} className="border-t border-border">
              {row.map((v, j) => (
                <td
                  key={j}
                  className={`whitespace-nowrap px-3 py-1.5 ${typeof v === "number" ? "text-right font-mono tabular-nums" : ""}`}
                >
                  {format(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function format(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}
