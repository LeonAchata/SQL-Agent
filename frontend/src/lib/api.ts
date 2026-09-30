export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ChartSpec = {
  type: "bar" | "line" | "pie";
  x: string;
  y: string[];
  title?: string | null;
};

export type QueryResult = {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
};

export type Status = "answered" | "clarify" | "out_of_scope" | "failed";

export type AgentEvent =
  | { type: "thread"; data: { thread_id: string } }
  | { type: "plan"; data: { question: string; tables: string[] } }
  | { type: "sql"; data: { sql: string; attempt: number } }
  | { type: "retry"; data: { stage: "validation" | "execution"; error: string } }
  | { type: "validated"; data: { sql: string } }
  | { type: "result"; data: QueryResult }
  | {
      type: "answer";
      data: { content: string; sql: string | null; chart: ChartSpec | null; status: Status };
    }
  | { type: "error"; data: { message: string } }
  | { type: "done"; data: Record<string, never> };

export type SchemaTable = {
  name: string;
  kind: string;
  comment: string | null;
  columns: { name: string; type: string }[];
};

export type SchemaInfo = { dialect: string; tables: SchemaTable[]; examples: string[] };

export async function getSchema(signal?: AbortSignal): Promise<SchemaInfo> {
  const res = await fetch(`${API_URL}/api/schema`, { signal });
  if (!res.ok) throw new Error(`Schema request failed (${res.status})`);
  return res.json();
}

/** POST a chat message and yield Server-Sent Events as they arrive. */
export async function* streamChat(
  message: string,
  threadId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Chat request failed (${res.status})`);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseEvent(block);
      if (event) yield event;
    }
  }
}

function parseEvent(block: string): AgentEvent | null {
  let type = "";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!type) return null;
  try {
    return { type, data: JSON.parse(data.join("\n") || "{}") } as AgentEvent;
  } catch {
    return null;
  }
}
