# SQL Agent

**Ask any SQL database questions in plain language.** SQL Agent introspects your schema, writes dialect-correct SQL with Claude, checks it against a SQL parser (AST) before anything runs, executes it read-only and explains the result, with a chart when one helps. Every step streams live to the UI.

[![CI](https://github.com/LeonAchata/SQL-Chatbot/actions/workflows/ci.yml/badge.svg)](https://github.com/LeonAchata/SQL-Chatbot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.x-1C3C3C)
![Claude](https://img.shields.io/badge/LLM-Claude-D97757)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs)
![License](https://img.shields.io/badge/license-MIT-blue)

![SQL Agent UI](docs/ui.png)

## Why this is different

Most text-to-SQL demos paste a hard-coded schema into a prompt and run whatever comes back. This project is built the way you would put it in front of real users:

- **Works with any schema.** The schema catalog is built at startup through SQLAlchemy introspection: tables, views, columns, types, primary and foreign keys, comments and sample values. Drivers for PostgreSQL, MySQL/MariaDB and SQLite are included; SQL Server, Snowflake and other SQLAlchemy dialects need only their driver.
- **Scales to large schemas.** Small databases send the full schema. Above a configurable threshold, the agent first picks the relevant tables from a compact index, then expands one foreign-key hop so joins still work. Only those tables are described in detail.
- **Defence in depth.** SQL is never trusted:
  1. **AST guard** ([sqlglot](https://github.com/tobymao/sqlglot)): exactly one read-only statement, no DDL/DML/`SELECT INTO`/`PRAGMA`/`SET`, only tables from the catalog, and a deny-list of dangerous functions (`pg_sleep`, `pg_read_file`, `dblink`, `load_extension`, `xp_cmdshell`, …). It also enforces a row limit, emitted in the right dialect (`LIMIT`, `TOP`, `FETCH`).
  2. **Read-only execution**: read-only transactions, statement timeouts (Postgres `statement_timeout`, MySQL `max_execution_time`, a SQLite progress handler) and an unconditional rollback.
  3. **Least-privilege role**: the Docker demo connects as a `SELECT`-only Postgres role.
  4. **PII masking**: columns listed in the semantic layer are never sampled into prompts and are redacted in results.
- **Self-correcting.** Guard rejections and database errors are fed back to the model, which repairs the query (up to `max_attempts`).
- **Asks when it should.** Genuinely ambiguous questions get a clarifying question instead of a guess, and follow-ups ("and in 2012?") are resolved against the conversation and the previous SQL.
- **Semantic layer.** Optional YAML with table and column descriptions, a business glossary ("revenue = …") and verified example queries.
- **Measured, not assumed.** An execution-accuracy benchmark compares the agent's results with hand-written gold SQL. Extra columns are tolerated and order is checked only when it matters.
- **Built for production.** Persistent conversations (LangGraph SQLite checkpointer), SSE streaming, prompt caching of the schema context, typed code (`mypy --strict`), 40+ tests that need no API key, CI and Docker.

## Architecture

```mermaid
flowchart LR
    Q([User question]) --> P[plan<br/><i>intent, tables,<br/>standalone question</i>]
    P -->|clarify / out of scope| R([reply])
    P -->|query| G[generate_sql]
    G --> V{validate_sql<br/><i>sqlglot AST guard</i>}
    V -->|rejected| G
    V -->|ok| E{execute_sql<br/><i>read-only, timeout,<br/>row cap, masking</i>}
    E -->|DB error| G
    E -->|rows| A[answer<br/><i>summary + chart spec</i>]
    G -. max attempts .-> F([give up])
```

| Layer | Tech |
| --- | --- |
| Agent orchestration | LangGraph `StateGraph` + checkpointer (per-thread memory) |
| LLM | Claude via the official `anthropic` SDK: structured outputs, prompt caching and server-side refusal fallbacks. The `LLM` protocol lets you plug in another provider. |
| Schema & execution | SQLAlchemy 2 (introspection, pooling, dialects) |
| SQL safety | sqlglot (parse, inspect, transpile) |
| API | FastAPI + Server-Sent Events |
| UI | Next.js 16, React 19, Tailwind 4, Recharts |
| Tooling | uv, ruff, mypy (strict), pytest, GitHub Actions, Docker Compose |

## Quick start

### Docker (Postgres demo, one command)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

Open <http://localhost:3000>. This starts PostgreSQL with the [Chinook](https://github.com/lerocha/chinook-database) music-store dataset, the API (connected as a read-only role) and the web UI.

### Local development

```bash
# Backend (Python 3.11+, uv)
cd backend
uv sync --all-extras
cp ../.env.example .env        # set ANTHROPIC_API_KEY
uv run sqlagent chat            # interactive terminal session on the bundled SQLite Chinook
uv run sqlagent serve --reload  # API on http://localhost:8000

# Frontend
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

Point it at your own database:

```bash
SQLAGENT_DATABASE_URL="postgresql+psycopg://readonly:***@host:5432/analytics" \
SQLAGENT_SCHEMAS='["sales","public"]' \
uv run sqlagent ask "Top 10 products by revenue last quarter"
```

### CLI

| Command | What it does |
| --- | --- |
| `sqlagent schema` | Print the catalog the agent sees (keys, comments, sample values) |
| `sqlagent ask "…"` | One-shot question with the full trace |
| `sqlagent chat` | Multi-turn terminal session |
| `sqlagent serve` | Run the HTTP API |
| `sqlagent eval [suite.yaml]` | Execution-accuracy benchmark |

## Configuration

All settings are environment variables prefixed with `SQLAGENT_` (or a `.env` file). See [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | bundled SQLite Chinook | Any SQLAlchemy URL |
| `SCHEMAS` / `INCLUDE_TABLES` / `EXCLUDE_TABLES` | all | What the agent may see and query |
| `MAX_ROWS` | `200` | Row cap enforced in SQL and at fetch time |
| `STATEMENT_TIMEOUT_MS` | `15000` | Per-query timeout |
| `MAX_ATTEMPTS` | `3` | Generate/repair attempts per question |
| `FULL_SCHEMA_THRESHOLD` | `40` | Above this many tables, select tables before writing SQL |
| `SEMANTIC_LAYER_PATH` | none | YAML with descriptions, glossary, examples, masked columns |
| `MODEL` / `EFFORT` | `claude-opus-5-5` / `medium` | Claude model and effort level |

See [`backend/semantic/chinook.postgres.yaml`](backend/semantic/chinook.postgres.yaml) for a semantic layer example.

## API

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/chat` | `{ "message": "...", "thread_id": "optional" }`. Returns an SSE stream of `thread`, `plan`, `sql`, `retry`, `validated`, `result`, `answer` and `done` events. |
| `GET` | `/api/threads/{id}` | Conversation history |
| `GET` | `/api/schema` | Catalog summary for the UI |
| `GET` | `/health` | Liveness |

## Evaluation

[`backend/evals/chinook.yaml`](backend/evals/chinook.yaml) holds 25 questions, from simple counts to multi-join aggregations, self-joins, anti-joins and time series, each with gold SQL. A case passes when the agent's result contains the gold result:

- each gold column must map to a distinct returned column with the same values;
- numbers are compared at 2-decimal precision;
- rows are compared as a multiset, or in order when the question implies an ordering.

```bash
cd backend && uv run sqlagent eval --min-accuracy 0.8
```

The suite can also be triggered manually in GitHub Actions (`Eval` workflow). It needs an `ANTHROPIC_API_KEY` secret, since it calls the real model.

## Project structure

```
backend/
  src/sqlagent/
    agent/        graph.py (LangGraph state machine), prompts, structured output schemas
    db/           catalog.py (introspection), executor.py (read-only execution)
    safety/       guard.py (sqlglot AST validation)
    llm/          provider protocol + Claude implementation
    semantic.py   semantic layer
    service.py    wiring + event stream
    api.py        FastAPI + SSE
    cli.py        Typer CLI
    evals.py      execution-accuracy harness
  tests/          unit + integration tests (fake LLM, no API key needed)
  evals/          benchmark suites
frontend/         Next.js UI (schema explorer, live trace, results table, charts)
demo/postgres/    Chinook dataset + read-only role for Docker
```

## Roadmap

- Embedding-based table retrieval for very large warehouses (1,000+ tables)
- Adapters for other LLM providers
- Query result caching and cost/latency tracing (OpenTelemetry)
- Auth and per-user row-level security passthrough

## License

MIT 

## Author

- Leon Achata
