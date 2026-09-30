"""Wires settings, database, catalog and LLM into a ready-to-use agent, and turns graph
updates into a stable stream of UI events."""

import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from sqlagent.agent.graph import AgentDeps, AgentState, build_graph
from sqlagent.config import Settings
from sqlagent.db.catalog import build_catalog
from sqlagent.db.executor import create_engine
from sqlagent.llm import LLM, create_llm
from sqlagent.semantic import SemanticLayer

log = logging.getLogger(__name__)


@dataclass
class Event:
    type: str
    data: dict[str, Any]


def build_deps(settings: Settings, llm: LLM | None = None) -> AgentDeps:
    engine = create_engine(settings.database_url)
    semantic = SemanticLayer.load(settings.semantic_layer_path)
    catalog = build_catalog(
        engine,
        schemas=settings.schemas or None,
        include=settings.include_tables,
        exclude=settings.exclude_tables,
        semantic=semantic,
        sample_values=settings.sample_values,
    )
    return AgentDeps(
        engine=engine,
        catalog=catalog,
        semantic=semantic,
        llm=llm or create_llm(settings),
        settings=settings,
    )


class SqlAgent:
    def __init__(
        self, deps: AgentDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
    ) -> None:
        self.deps = deps
        self.graph: CompiledStateGraph[Any, Any, Any, Any] = build_graph(deps, checkpointer)

    @staticmethod
    def new_thread() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _input(message: str) -> AgentState:
        return {"messages": [{"role": "user", "content": message}]}

    def stream(self, message: str, thread_id: str) -> Iterator[Event]:
        state = self._input(message)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        for update in self.graph.stream(state, config, stream_mode="updates"):
            yield from to_events(update)

    async def astream(self, message: str, thread_id: str) -> AsyncIterator[Event]:
        state = self._input(message)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        async for update in self.graph.astream(state, config, stream_mode="updates"):
            for event in to_events(update):
                yield event

    def ask(self, message: str, thread_id: str | None = None) -> dict[str, Any]:
        """Run one turn and return the final state (convenient for scripts and evals)."""
        config: RunnableConfig = {"configurable": {"thread_id": thread_id or self.new_thread()}}
        state = self._input(message)
        return dict(self.graph.invoke(state, config))

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        return list(snapshot.values.get("messages", [])) if snapshot else []


def to_events(update: dict[str, Any]) -> Iterator[Event]:
    """Map one LangGraph ``updates`` chunk to UI events."""
    for node, data in update.items():
        if not isinstance(data, dict):
            continue
        if node == "plan" and data.get("tables"):
            yield Event("plan", {"question": data.get("question"), "tables": data["tables"]})
        elif node == "generate_sql" and data.get("sql"):
            yield Event("sql", {"sql": data["sql"], "attempt": data.get("attempts", 1)})
        elif node in ("validate_sql", "execute_sql") and data.get("error"):
            stage = "validation" if node == "validate_sql" else "execution"
            yield Event("retry", {"stage": stage, "error": data["error"]})
        elif node == "validate_sql":
            yield Event("validated", {"sql": data.get("sql")})
        elif node == "execute_sql" and data.get("result"):
            yield Event("result", data["result"])
        elif node in ("answer", "respond", "give_up"):
            message = (data.get("messages") or [{}])[-1]
            yield Event(
                "answer",
                {
                    "content": message.get("content", ""),
                    "sql": message.get("sql"),
                    "chart": data.get("chart"),
                    "status": data.get("status") or "answered",
                },
            )
