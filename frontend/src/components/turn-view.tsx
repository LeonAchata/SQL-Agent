"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { phaseOf, type Turn } from "@/lib/turns";
import { ResultChart } from "./result-chart";
import { ResultTable } from "./result-table";

const PHASE_LABEL = {
  planning: "Reading the schema",
  writing: "Writing SQL",
  running: "Running query",
  explaining: "Summarising results",
  done: "",
} as const;

export function TurnView({ turn }: { turn: Turn }) {
  const phase = phaseOf(turn);
  const failedAttempts = turn.attempts.filter((a) => a.error);
  const [view, setView] = useState<"chart" | "table">("chart");
  const showChart = Boolean(turn.chart && turn.result?.row_count);

  return (
    <article className="flex flex-col gap-4">
      <p className="self-end rounded-2xl rounded-br-sm bg-surface-2 px-4 py-2 text-[15px]">
        {turn.question}
      </p>

      {(turn.tables.length > 0 || turn.sql || failedAttempts.length > 0) && (
        <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
          {turn.tables.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-muted">Tables</span>
              {turn.tables.map((t) => (
                <span key={t} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono">
                  {t}
                </span>
              ))}
            </div>
          )}

          {failedAttempts.map((a, i) => (
            <details key={i} className="text-xs">
              <summary className="cursor-pointer text-warn">
                Attempt {i + 1} rejected at {a.stage}: {a.error}
              </summary>
              <pre className="mt-2 overflow-x-auto rounded-md bg-code p-3 font-mono">{a.sql}</pre>
            </details>
          ))}

          {turn.sql && <SqlBlock sql={turn.sql} />}

          {turn.result && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-xs text-muted">
                <span>
                  {turn.result.row_count} row{turn.result.row_count === 1 ? "" : "s"}
                  {turn.result.truncated ? " (truncated)" : ""} · {turn.result.elapsed_ms} ms
                </span>
                {showChart && (
                  <div className="flex gap-1" role="tablist">
                    {(["chart", "table"] as const).map((v) => (
                      <button
                        key={v}
                        role="tab"
                        aria-selected={view === v}
                        onClick={() => setView(v)}
                        className={`rounded px-2 py-0.5 capitalize ${view === v ? "bg-surface-2 text-fg" : "hover:text-fg"}`}
                      >
                        {v}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {showChart && view === "chart" && turn.chart ? (
                <ResultChart result={turn.result} spec={turn.chart} />
              ) : (
                <ResultTable result={turn.result} />
              )}
            </div>
          )}
        </div>
      )}

      {phase !== "done" && (
        <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite">
          <span className="size-1.5 animate-pulse rounded-full bg-accent" />
          {PHASE_LABEL[phase]}…
        </p>
      )}

      {turn.answer && (
        <div
          className={`prose-answer text-[15px] leading-relaxed ${turn.status === "failed" ? "text-danger" : ""}`}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.answer}</ReactMarkdown>
        </div>
      )}
      {turn.error && !turn.answer && <p className="text-sm text-danger">{turn.error}</p>}
    </article>
  );
}

function SqlBlock({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-md bg-code p-3 pr-16 font-mono text-[13px] leading-relaxed">
        {sql}
      </pre>
      <button
        onClick={() => {
          void navigator.clipboard.writeText(sql).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
        className="absolute right-2 top-2 rounded border border-border bg-surface px-2 py-0.5 text-xs text-muted hover:text-fg"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
