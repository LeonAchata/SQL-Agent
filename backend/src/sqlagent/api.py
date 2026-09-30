"""HTTP API. Chat responses stream as Server-Sent Events so the UI can show each step live."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field

from sqlagent.config import get_settings
from sqlagent.service import SqlAgent, build_deps

log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    thread_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    Path(settings.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_path) as saver:
        app.state.agent = SqlAgent(build_deps(settings), checkpointer=saver)
        yield
    app.state.agent.deps.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SQL Agent", version="2.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def agent(request: Request) -> SqlAgent:
        return request.app.state.agent  # type: ignore[no-any-return]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/schema")
    def schema(request: Request) -> dict[str, Any]:
        catalog = agent(request).deps.catalog
        return {
            "dialect": catalog.dialect,
            "tables": [
                {
                    "name": t.qualified_name,
                    "kind": t.kind,
                    "comment": t.comment,
                    "columns": [{"name": c.name, "type": c.type} for c in t.columns],
                }
                for t in catalog.tables
            ],
            "examples": [e.question for e in agent(request).deps.semantic.examples],
        }

    @app.get("/api/threads/{thread_id}")
    async def thread(thread_id: str, request: Request) -> dict[str, Any]:
        snapshot = await agent(request).graph.aget_state({"configurable": {"thread_id": thread_id}})
        if not snapshot or not snapshot.values:
            raise HTTPException(404, "Thread not found")
        return {"thread_id": thread_id, "messages": snapshot.values.get("messages", [])}

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        sql_agent = agent(request)
        thread_id = body.thread_id or sql_agent.new_thread()

        async def events() -> AsyncIterator[str]:
            yield _sse("thread", {"thread_id": thread_id})
            try:
                async for event in sql_agent.astream(body.message, thread_id):
                    yield _sse(event.type, event.data)
            except Exception:
                log.exception("Chat turn failed")
                yield _sse("error", {"message": "Internal error while answering."})
            yield _sse("done", {})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


app = create_app()
