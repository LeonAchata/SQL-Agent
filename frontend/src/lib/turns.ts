import type { AgentEvent, ChartSpec, QueryResult, Status } from "./api";

export type Attempt = { sql: string; error?: string; stage?: "validation" | "execution" };

export type Turn = {
  id: string;
  question: string;
  tables: string[];
  attempts: Attempt[];
  sql: string | null;
  result: QueryResult | null;
  answer: string | null;
  chart: ChartSpec | null;
  status: Status | "running";
  error: string | null;
};

export function newTurn(question: string): Turn {
  return {
    id: crypto.randomUUID(),
    question,
    tables: [],
    attempts: [],
    sql: null,
    result: null,
    answer: null,
    chart: null,
    status: "running",
    error: null,
  };
}

/** Fold one streamed event into the turn it belongs to. */
export function applyEvent(turn: Turn, event: AgentEvent): Turn {
  switch (event.type) {
    case "plan":
      return { ...turn, tables: event.data.tables };
    case "sql":
      return { ...turn, attempts: [...turn.attempts, { sql: event.data.sql }] };
    case "retry": {
      const attempts = [...turn.attempts];
      const last = attempts.at(-1);
      if (last) attempts[attempts.length - 1] = { ...last, ...event.data };
      return { ...turn, attempts };
    }
    case "validated":
      return { ...turn, sql: event.data.sql };
    case "result":
      return { ...turn, result: event.data };
    case "answer":
      return {
        ...turn,
        answer: event.data.content,
        chart: event.data.chart,
        status: event.data.status,
        sql: event.data.sql ?? turn.sql,
      };
    case "error":
      return { ...turn, status: "failed", error: event.data.message };
    default:
      return turn;
  }
}

export type Phase = "planning" | "writing" | "running" | "explaining" | "done";

export function phaseOf(turn: Turn): Phase {
  if (turn.status !== "running") return "done";
  if (turn.result) return "explaining";
  if (turn.sql) return "running";
  if (turn.tables.length || turn.attempts.length) return "writing";
  return "planning";
}
