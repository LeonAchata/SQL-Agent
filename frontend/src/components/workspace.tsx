"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getSchema, streamChat, type SchemaInfo } from "@/lib/api";
import { applyEvent, newTurn, type Turn } from "@/lib/turns";
import { SchemaPanel } from "./schema-panel";
import { TurnView } from "./turn-view";

const FALLBACK_EXAMPLES = [
  "Which 5 countries generate the most revenue?",
  "Show monthly revenue for 2012",
  "Who are our top 3 customers by total spend?",
  "Which genres sell best?",
];

export function Workspace() {
  const [schema, setSchema] = useState<SchemaInfo | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getSchema(controller.signal)
      .then(setSchema)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setSchemaError(String(err));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const ask = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || busy) return;
      const turn = newTurn(text);
      setTurns((prev) => [...prev, turn]);
      setInput("");
      setBusy(true);

      const controller = new AbortController();
      abortRef.current = controller;
      const update = (fn: (t: Turn) => Turn) =>
        setTurns((prev) => prev.map((t) => (t.id === turn.id ? fn(t) : t)));

      try {
        for await (const event of streamChat(text, threadId, controller.signal)) {
          if (event.type === "thread") setThreadId(event.data.thread_id);
          else update((t) => applyEvent(t, event));
        }
      } catch (err) {
        const message = controller.signal.aborted ? "Stopped." : `Connection error: ${String(err)}`;
        update((t) => ({ ...t, status: "failed", error: message }));
      } finally {
        update((t) => (t.status === "running" ? { ...t, status: "failed", error: t.error ?? "No answer received." } : t));
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, threadId],
  );

  const reset = () => {
    abortRef.current?.abort();
    setTurns([]);
    setThreadId(null);
  };

  const examples = schema?.examples.length ? schema.examples : FALLBACK_EXAMPLES;

  return (
    <div className="flex h-full">
      <aside
        className={`${sidebarOpen ? "fixed inset-0 z-20 flex" : "hidden"} w-full flex-col border-r border-border bg-surface md:static md:flex md:w-72 md:shrink-0`}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="text-sm font-medium">Schema</span>
          <button
            className="text-sm text-muted hover:text-fg md:hidden"
            onClick={() => setSidebarOpen(false)}
          >
            Close
          </button>
        </div>
        <SchemaPanel schema={schema} error={schemaError} />
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border px-4 py-3 md:px-8">
          <div className="flex items-center gap-3">
            <button
              className="rounded-md border border-border px-2 py-1 text-xs text-muted md:hidden"
              onClick={() => setSidebarOpen(true)}
            >
              Schema
            </button>
            <h1 className="text-sm font-semibold tracking-tight">SQL Agent</h1>
            {schema && (
              <span className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-muted">
                {schema.dialect} · {schema.tables.length} tables
              </span>
            )}
          </div>
          {turns.length > 0 && (
            <button onClick={reset} className="text-sm text-muted hover:text-fg">
              New chat
            </button>
          )}
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-10 px-4 py-8 md:px-8">
            {turns.length === 0 ? (
              <EmptyState examples={examples} onPick={ask} />
            ) : (
              turns.map((turn) => <TurnView key={turn.id} turn={turn} />)
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          className="border-t border-border bg-bg px-4 py-4 md:px-8"
          onSubmit={(e) => {
            e.preventDefault();
            void ask(input);
          }}
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-xl border border-border bg-surface p-2 focus-within:border-accent">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void ask(input);
                }
              }}
              rows={1}
              placeholder="Ask a question about your data…"
              aria-label="Question"
              className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-muted"
            />
            {busy ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="rounded-lg border border-border px-4 py-2 text-sm"
              >
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
              >
                Ask
              </button>
            )}
          </div>
        </form>
      </main>
    </div>
  );
}

function EmptyState({ examples, onPick }: { examples: string[]; onPick: (q: string) => void }) {
  return (
    <div className="flex flex-col gap-6 pt-10">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Ask your database anything.</h2>
        <p className="mt-2 max-w-xl text-muted">
          Questions become validated, read-only SQL. Every step is shown: the tables chosen, the
          query, retries, the raw result and a chart when it helps.
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {examples.map((q) => (
          <button
            key={q}
            onClick={() => onPick(q)}
            className="rounded-lg border border-border bg-surface px-4 py-3 text-left text-sm transition-colors hover:border-accent"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
