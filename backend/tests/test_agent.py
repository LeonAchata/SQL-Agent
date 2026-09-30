from langgraph.checkpoint.memory import InMemorySaver

from sqlagent.agent.schemas import Answer, Plan, SqlDraft
from sqlagent.config import Settings
from sqlagent.llm import LLMError
from sqlagent.service import SqlAgent, build_deps
from tests.conftest import FakeLLM

ANSWER = {"answer": "Ada spent the most.", "chart": {"type": "bar", "x": "name", "y": ["spent"]}}


def make_agent(settings: Settings, llm: FakeLLM) -> SqlAgent:
    return SqlAgent(build_deps(settings, llm=llm), checkpointer=InMemorySaver())


def test_happy_path_streams_every_step(settings: Settings) -> None:
    llm = FakeLLM(
        {
            Plan: [
                {
                    "intent": "query",
                    "standalone_question": "Top customer",
                    "tables": ["orders", "customer"],
                }
            ],
            SqlDraft: [
                {
                    "reasoning": "join and sum",
                    "sql": "SELECT c.name, SUM(o.total) AS spent FROM customer c "
                    "JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY spent DESC",
                }
            ],
            Answer: [ANSWER],
        }
    )
    agent = make_agent(settings, llm)
    events = list(agent.stream("who spends most?", "t1"))
    types = [e.type for e in events]
    assert types == ["plan", "sql", "validated", "result", "answer"]
    result = events[3].data
    assert result["columns"] == ["name", "spent"]
    assert result["rows"][0][0] == "Grace"
    answer = events[-1].data
    assert answer["status"] == "answered"
    assert answer["chart"] == {"type": "bar", "x": "name", "y": ["spent"], "title": None}
    assert "LIMIT" in answer["sql"]


def test_repairs_invalid_sql_then_succeeds(settings: Settings) -> None:
    llm = FakeLLM(
        {
            Plan: [{"intent": "query", "standalone_question": "count", "tables": ["customer"]}],
            SqlDraft: [
                {"reasoning": "", "sql": "SELECT COUNT(*) FROM customers"},  # unknown table
                {"reasoning": "", "sql": "SELECT COUNT(nope) FROM customer"},  # runtime error
                {"reasoning": "", "sql": "SELECT COUNT(*) AS n FROM customer"},
            ],
            Answer: [{"answer": "3", "chart": {"type": "none"}}],
        }
    )
    events = list(make_agent(settings, llm).stream("how many customers", "t1"))
    retries = [e.data["stage"] for e in events if e.type == "retry"]
    assert retries == ["validation", "execution"]
    assert events[-1].data["status"] == "answered"
    assert events[-1].data["chart"] is None
    # the repair prompt carries the previous error back to the model
    repair_prompt = llm.calls[2]["messages"][0]["content"]
    assert "customers" in repair_prompt and "Unknown" in repair_prompt


def test_gives_up_after_max_attempts(settings: Settings) -> None:
    bad = {"reasoning": "", "sql": "DELETE FROM customer"}
    llm = FakeLLM(
        {
            Plan: [{"intent": "query", "standalone_question": "x", "tables": ["customer"]}],
            SqlDraft: [bad, bad, bad],
        }
    )
    events = list(make_agent(settings, llm).stream("delete everything", "t1"))
    assert events[-1].data["status"] == "failed"
    assert sum(e.type == "sql" for e in events) == settings.max_attempts


def test_clarify_and_out_of_scope_skip_sql(settings: Settings) -> None:
    llm = FakeLLM(
        {
            Plan: [
                {"intent": "clarify", "standalone_question": "sales", "reply": "Which period?"},
                {
                    "intent": "out_of_scope",
                    "standalone_question": "hi",
                    "reply": "Hi! Ask me about orders.",
                },
            ]
        }
    )
    agent = make_agent(settings, llm)
    first = list(agent.stream("sales?", "t1"))
    assert [e.type for e in first] == ["answer"]
    assert first[0].data == {
        "content": "Which period?",
        "sql": None,
        "chart": None,
        "status": "clarify",
    }
    second = list(agent.stream("hello", "t1"))
    assert second[0].data["status"] == "out_of_scope"


def test_follow_up_sees_history_and_previous_sql(settings: Settings) -> None:
    sql = "SELECT COUNT(*) AS n FROM orders"
    llm = FakeLLM(
        {
            Plan: [
                {"intent": "query", "standalone_question": "orders", "tables": ["orders"]},
                {"intent": "query", "standalone_question": "orders in March", "tables": ["orders"]},
            ],
            SqlDraft: [
                {"reasoning": "", "sql": sql},
                {"reasoning": "", "sql": sql + " WHERE created_at LIKE '2024-03%'"},
            ],
            Answer: [
                {"answer": "4", "chart": {"type": "none"}},
                {"answer": "1", "chart": {"type": "none"}},
            ],
        }
    )
    agent = make_agent(settings, llm)
    list(agent.stream("how many orders?", "t1"))
    list(agent.stream("and in March?", "t1"))
    plan_prompt = llm.calls[3]["messages"][0]["content"]
    assert "how many orders?" in plan_prompt and "SQL used" in plan_prompt
    sql_prompt = llm.calls[4]["messages"][0]["content"]
    assert "previous turn" in sql_prompt
    assert [m["role"] for m in agent.history("t1")] == ["user", "assistant", "user", "assistant"]


def test_llm_failure_is_reported_not_raised(settings: Settings) -> None:
    llm = FakeLLM({Plan: [LLMError("The model is rate limited; try again shortly.")]})
    events = list(make_agent(settings, llm).stream("anything", "t1"))
    assert events[-1].data["status"] == "failed"
    assert "rate limited" in events[-1].data["content"]


def test_large_schema_sends_compact_then_selected_tables(settings: Settings) -> None:
    settings = settings.model_copy(update={"full_schema_threshold": 1})
    llm = FakeLLM(
        {
            Plan: [{"intent": "query", "standalone_question": "q", "tables": ["orders"]}],
            SqlDraft: [{"reasoning": "", "sql": "SELECT COUNT(*) FROM orders"}],
            Answer: [{"answer": "4", "chart": {"type": "none"}}],
        }
    )
    list(make_agent(settings, llm).stream("q", "t1"))
    plan_ctx, sql_ctx = llm.calls[0]["context"], llm.calls[1]["context"]
    assert "orders(id, customer_id, total, created_at)" in plan_ctx
    assert "TABLE orders" in sql_ctx and "TABLE customer" in sql_ctx  # FK neighbour included
    assert "TABLE product" not in sql_ctx
