"""Checks the exact request the Claude adapter sends, against a local fake API server."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import anthropic
import pytest

from sqlagent.agent.schemas import Plan
from sqlagent.llm import LLMError
from sqlagent.llm.anthropic import AnthropicLLM


class FakeAPI:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response: dict[str, Any] = {}


def message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }


@pytest.fixture
def fake_api() -> Iterator[tuple[FakeAPI, str]]:
    api = FakeAPI()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            api.requests.append({"headers": dict(self.headers), "json": json.loads(body)})
            payload = json.dumps(api.response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield api, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def make_llm(base_url: str, **kwargs: Any) -> AnthropicLLM:
    client = anthropic.Anthropic(api_key="test-key", base_url=base_url, max_retries=0)
    return AnthropicLLM(client=client, **kwargs)


def ask(llm: AnthropicLLM) -> Plan:
    return llm.structured(
        instructions="plan it",
        context="TABLE t (id INT)",
        messages=[{"role": "user", "content": "how many?"}],
        schema=Plan,
    )


def test_request_shape_and_parsing(fake_api: tuple[FakeAPI, str]) -> None:
    api, url = fake_api
    api.response = message(
        json.dumps({"intent": "query", "standalone_question": "q", "tables": ["t"]})
    )

    plan = ask(make_llm(url, effort="low"))

    assert plan.tables == ["t"]
    sent = api.requests[0]["json"]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in sent  # adaptive by default on this model
    assert sent["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert sent["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in api.requests[0]["headers"]["anthropic-beta"]


def test_no_fallbacks_for_models_without_support(fake_api: tuple[FakeAPI, str]) -> None:
    api, url = fake_api
    api.response = message(json.dumps({"intent": "out_of_scope", "standalone_question": "hi"}))
    ask(make_llm(url, model="claude-haiku-4-5"))
    assert "fallbacks" not in api.requests[0]["json"]


def test_refusal_becomes_llm_error(fake_api: tuple[FakeAPI, str]) -> None:
    api, url = fake_api
    api.response = message("", stop_reason="refusal")
    with pytest.raises(LLMError, match="declined"):
        ask(make_llm(url))
