import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sqlagent import api as api_module
from sqlagent.agent.schemas import Answer, Plan, SqlDraft
from sqlagent.config import Settings
from sqlagent.service import build_deps
from tests.conftest import FakeLLM


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    llm = FakeLLM(
        {
            Plan: [{"intent": "query", "standalone_question": "n", "tables": ["customer"]}],
            SqlDraft: [{"reasoning": "", "sql": "SELECT COUNT(*) AS n FROM customer"}],
            Answer: [{"answer": "There are **3** customers.", "chart": {"type": "none"}}],
        }
    )
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(api_module, "build_deps", lambda s: build_deps(s, llm=llm))
    return TestClient(api_module.create_app())


def test_chat_streams_events_and_persists_thread(client: TestClient) -> None:
    with client:
        response = client.post("/api/chat", json={"message": "how many customers?"})
        assert response.status_code == 200
        events = parse_sse(response.text)
        names = [name for name, _ in events]
        assert names == ["thread", "plan", "sql", "validated", "result", "answer", "done"]
        thread_id = events[0][1]["thread_id"]

        history = client.get(f"/api/threads/{thread_id}").json()["messages"]
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert client.get("/api/threads/unknown").status_code == 404


def test_schema_endpoint(client: TestClient) -> None:
    with client:
        body = client.get("/api/schema").json()
    assert body["dialect"] == "sqlite"
    assert {t["name"] for t in body["tables"]} == {"customer", "orders", "product"}


def test_rejects_bad_thread_id(client: TestClient) -> None:
    with client:
        response = client.post("/api/chat", json={"message": "hi", "thread_id": "../../etc"})
    assert response.status_code == 422
