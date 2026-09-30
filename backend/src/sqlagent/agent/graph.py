"""The agent as a LangGraph state machine.

start -> plan -> (clarify | out_of_scope) -> respond
              -> generate_sql -> validate_sql -> execute_sql -> answer
                    ^               |               |
                    +---- repair ---+---------------+   (up to max_attempts)
"""

import logging
import operator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.engine import Engine

from sqlagent.agent import prompts
from sqlagent.agent.schemas import Answer, Plan, SqlDraft
from sqlagent.config import Settings
from sqlagent.db.catalog import Catalog
from sqlagent.db.executor import ExecutionError, QueryResult, execute_read_only
from sqlagent.llm import LLM, LLMError, LLMMessage
from sqlagent.safety.guard import GuardError, guard_sql
from sqlagent.semantic import SemanticLayer

log = logging.getLogger(__name__)

HISTORY_TURNS = 10
RESULT_ROWS_FOR_ANSWER = 50


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str
    sql: NotRequired[str | None]


Status = Literal["running", "answered", "clarify", "out_of_scope", "failed"]


class AgentState(TypedDict, total=False):
    messages: Annotated[list[ChatMessage], operator.add]
    question: str
    tables: list[str]
    sql: str | None
    attempts: int
    error: str | None
    result: dict[str, Any] | None
    chart: dict[str, Any] | None
    status: Status
    reply: str | None


@dataclass
class AgentDeps:
    engine: Engine
    catalog: Catalog
    semantic: SemanticLayer
    llm: LLM
    settings: Settings


def build_graph(
    deps: AgentDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    settings = deps.settings
    catalog = deps.catalog
    semantic_text = deps.semantic.render()
    large_schema = len(catalog.tables) > settings.full_schema_threshold
    full_context = prompts.schema_context(catalog.dialect, catalog.render_detailed(), semantic_text)
    plan_context = (
        prompts.schema_context(catalog.dialect, catalog.render_compact(), semantic_text)
        if large_schema
        else full_context
    )
    sql_instructions = prompts.SQL_INSTRUCTIONS.format(
        dialect=catalog.dialect, max_rows=settings.max_rows
    )

    # ---- nodes -----------------------------------------------------------------------------

    def start(state: AgentState) -> AgentState:
        question = state["messages"][-1]["content"]
        return {
            "question": question,
            "tables": [],
            "sql": None,
            "attempts": 0,
            "error": None,
            "result": None,
            "chart": None,
            "reply": None,
            "status": "running",
        }

    def plan(state: AgentState) -> AgentState:
        history = _render_history(state["messages"][:-1])
        prompt = f"{history}Latest user message:\n{state['question']}"
        try:
            result = deps.llm.structured(
                instructions=prompts.PLAN_INSTRUCTIONS,
                context=plan_context,
                messages=[_user(prompt)],
                schema=Plan,
            )
        except LLMError as exc:
            return _failed(str(exc))

        if result.intent != "query":
            return {"status": result.intent, "reply": result.reply or "Could you rephrase that?"}
        tables = [t.qualified_name for t in catalog.resolve(result.tables)]
        return {"question": result.standalone_question, "tables": tables}

    def generate_sql(state: AgentState) -> AgentState:
        selected = catalog.resolve(state.get("tables") or [])
        if large_schema:
            context = prompts.schema_context(
                catalog.dialect,
                catalog.render_detailed(catalog.with_neighbors(selected)),
                semantic_text,
            )
        else:
            context = full_context

        prompt = f"Question: {state['question']}"
        previous = _last_sql(state["messages"])
        if previous:
            prompt += f"\n\nSQL used for the previous turn (for follow-ups):\n{previous}"
        if state.get("error") and state.get("sql"):
            prompt += (
                f"\n\nYour previous attempt failed.\nSQL:\n{state['sql']}\n"
                f"Error: {state['error']}\nFix the query."
            )
        try:
            draft = deps.llm.structured(
                instructions=sql_instructions,
                context=context,
                messages=[_user(prompt)],
                schema=SqlDraft,
            )
        except LLMError as exc:
            return _failed(str(exc))
        return {"sql": draft.sql, "attempts": state.get("attempts", 0) + 1, "error": None}

    def validate_sql(state: AgentState) -> AgentState:
        try:
            guarded = guard_sql(state["sql"] or "", catalog, max_rows=settings.max_rows)
        except GuardError as exc:
            log.info("Guard rejected SQL: %s", exc)
            return {"error": str(exc)}
        return {"sql": guarded.sql}

    def execute_sql(state: AgentState) -> AgentState:
        try:
            result = execute_read_only(
                deps.engine,
                state["sql"] or "",
                max_rows=settings.max_rows,
                timeout_ms=settings.statement_timeout_ms,
                masked_columns=deps.semantic.masked_column_names(),
            )
        except ExecutionError as exc:
            log.info("Execution failed: %s", exc)
            return {"error": str(exc)}
        return {"result": result.model_dump()}

    def answer(state: AgentState) -> AgentState:
        result = QueryResult.model_validate(state["result"])
        prompt = (
            f"Question: {state['question']}\n\nSQL:\n{state['sql']}\n\n"
            f"Result ({result.row_count} rows{', truncated' if result.truncated else ''}):\n"
            f"{_render_rows(result)}"
        )
        try:
            out = deps.llm.structured(
                instructions=prompts.ANSWER_INSTRUCTIONS,
                context=f"Database dialect: {catalog.dialect}",
                messages=[_user(prompt)],
                schema=Answer,
            )
        except LLMError:
            text, chart = f"The query returned {result.row_count} row(s).", None
        else:
            text, chart = out.answer, _valid_chart(out.chart.model_dump(), result.columns)
        return {
            "status": "answered",
            "chart": chart,
            "messages": [{"role": "assistant", "content": text, "sql": state["sql"]}],
        }

    def respond(state: AgentState) -> AgentState:
        text = state.get("reply") or "Sorry, I could not answer that."
        return {
            "status": state.get("status", "failed"),
            "messages": [{"role": "assistant", "content": text, "sql": None}],
        }

    def give_up(state: AgentState) -> AgentState:
        text = (
            "I couldn't build a working query for that question. "
            f"Last error: {state.get('error')}. Try rephrasing or being more specific."
        )
        return {
            "status": "failed",
            "reply": text,
            "messages": [{"role": "assistant", "content": text, "sql": state.get("sql")}],
        }

    # ---- routing ---------------------------------------------------------------------------

    def after_plan(state: AgentState) -> str:
        if state.get("status") == "failed":
            return "respond"
        return "respond" if state.get("reply") else "generate_sql"

    def after_generate(state: AgentState) -> str:
        return "respond" if state.get("status") == "failed" else "validate_sql"

    def retry_or(next_node: str) -> Any:
        def route(state: AgentState) -> str:
            if not state.get("error"):
                return next_node
            return "generate_sql" if state.get("attempts", 0) < settings.max_attempts else "give_up"

        return route

    graph = StateGraph(AgentState)
    for name, fn in [
        ("start", start),
        ("plan", plan),
        ("generate_sql", generate_sql),
        ("validate_sql", validate_sql),
        ("execute_sql", execute_sql),
        ("answer", answer),
        ("respond", respond),
        ("give_up", give_up),
    ]:
        graph.add_node(name, fn)

    graph.add_edge(START, "start")
    graph.add_edge("start", "plan")
    graph.add_conditional_edges("plan", after_plan, ["respond", "generate_sql"])
    graph.add_conditional_edges("generate_sql", after_generate, ["respond", "validate_sql"])
    graph.add_conditional_edges(
        "validate_sql", retry_or("execute_sql"), ["execute_sql", "generate_sql", "give_up"]
    )
    graph.add_conditional_edges(
        "execute_sql", retry_or("answer"), ["answer", "generate_sql", "give_up"]
    )
    for terminal in ("answer", "respond", "give_up"):
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=checkpointer)


# ---- helpers -------------------------------------------------------------------------------


def _user(content: str) -> LLMMessage:
    return {"role": "user", "content": content}


def _failed(reason: str) -> AgentState:
    return {"status": "failed", "reply": f"Sorry, something went wrong: {reason}"}


def _render_history(messages: list[ChatMessage]) -> str:
    recent = messages[-HISTORY_TURNS:]
    if not recent:
        return ""
    lines = ["Conversation so far:"]
    for m in recent:
        lines.append(f"{m['role']}: {m['content']}")
        if m.get("sql"):
            lines.append(f"(SQL used: {' '.join(str(m['sql']).split())})")
    return "\n".join(lines) + "\n\n"


def _last_sql(messages: list[ChatMessage]) -> str | None:
    for m in reversed(messages[:-1]):
        if m["role"] == "assistant" and m.get("sql"):
            return m.get("sql")
    return None


def _render_rows(result: QueryResult) -> str:
    rows = result.rows[:RESULT_ROWS_FOR_ANSWER]
    lines = [" | ".join(result.columns)]
    lines += [" | ".join("NULL" if v is None else str(v) for v in row) for row in rows]
    if result.row_count > len(rows):
        lines.append(f"... ({result.row_count - len(rows)} more rows)")
    return "\n".join(lines)


def _valid_chart(chart: dict[str, Any], columns: list[str]) -> dict[str, Any] | None:
    if chart.get("type") in (None, "none") or chart.get("x") not in columns:
        return None
    y = [c for c in chart.get("y") or [] if c in columns]
    return {**chart, "y": y} if y else None
