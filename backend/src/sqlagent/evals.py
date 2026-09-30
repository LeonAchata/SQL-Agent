"""Execution-accuracy evaluation: run each question through the agent and compare its result set
with the result of a hand-written gold query."""

import itertools
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from sqlagent.config import get_settings
from sqlagent.db.executor import ExecutionError, QueryResult, execute_read_only
from sqlagent.service import SqlAgent, build_deps

COMPARE_ROWS = 10_000
MAX_MAPPINGS = 5_000


class Case(BaseModel):
    id: str
    question: str
    sql: str
    ordered: bool = False


class Suite(BaseModel):
    database_url: str | None = None
    cases: list[Case]


class CaseResult(BaseModel):
    id: str
    question: str
    passed: bool
    status: str
    predicted_sql: str | None = None
    error: str | None = None
    seconds: float


class Report(BaseModel):
    model: str
    total: int
    passed: int
    accuracy: float
    avg_seconds: float
    cases: list[CaseResult] = Field(default_factory=list)


def results_match(gold: QueryResult, pred: QueryResult, *, ordered: bool) -> bool:
    """True if every gold column maps to a distinct predicted column with the same values."""
    if gold.row_count != pred.row_count:
        return False
    if gold.row_count == 0:
        return True
    gold_cols = list(zip(*gold.rows, strict=True))
    pred_cols = list(zip(*pred.rows, strict=True))
    norm_gold = [[_norm(v) for v in col] for col in gold_cols]
    norm_pred = [[_norm(v) for v in col] for col in pred_cols]

    # Candidate predicted columns per gold column: same multiset of values.
    candidates = [
        [j for j, p in enumerate(norm_pred) if Counter(p) == Counter(g)] for g in norm_gold
    ]
    if any(not c for c in candidates):
        return False

    gold_rows = list(zip(*norm_gold, strict=True))
    for mapping in itertools.islice(itertools.product(*candidates), MAX_MAPPINGS):
        if len(set(mapping)) != len(mapping):
            continue
        projected = list(zip(*(norm_pred[j] for j in mapping), strict=True))
        if (projected == gold_rows) if ordered else (Counter(projected) == Counter(gold_rows)):
            return True
    return False


def _norm(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return round(float(stripped), 2)
        except ValueError:
            return stripped.lower()
    return value


def run_suite(
    path: Path, *, limit: int | None = None, output: Path | None = None, console: Console
) -> Report:
    suite = Suite.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    settings = get_settings()
    if suite.database_url:
        settings = settings.model_copy(update={"database_url": suite.database_url})
    deps = build_deps(settings)
    agent = SqlAgent(deps, checkpointer=InMemorySaver())
    cases = suite.cases[:limit] if limit else suite.cases

    results: list[CaseResult] = []
    for case in cases:
        started = time.perf_counter()
        final = agent.ask(case.question)
        elapsed = time.perf_counter() - started
        predicted = final.get("sql") if final.get("status") == "answered" else None
        passed, error = False, final.get("error")
        if predicted:
            try:
                gold = _run(deps.engine, case.sql, settings.statement_timeout_ms)
                pred = _run(deps.engine, predicted, settings.statement_timeout_ms)
                passed = results_match(gold, pred, ordered=case.ordered)
            except ExecutionError as exc:
                error = str(exc)
        results.append(
            CaseResult(
                id=case.id,
                question=case.question,
                passed=passed,
                status=str(final.get("status")),
                predicted_sql=predicted,
                error=error,
                seconds=round(elapsed, 2),
            )
        )
        mark = "[green]✓[/]" if passed else "[red]✗[/]"
        console.print(f"{mark} {case.id} [dim]({elapsed:.1f}s)[/]")

    passed_count = sum(r.passed for r in results)
    report = Report(
        model=settings.model,
        total=len(results),
        passed=passed_count,
        accuracy=round(passed_count / len(results), 4) if results else 0.0,
        avg_seconds=round(sum(r.seconds for r in results) / len(results), 2) if results else 0.0,
        cases=results,
    )
    _print_summary(report, console)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report.model_dump(), indent=2), encoding="utf-8")
    return report


def _run(engine: Any, sql: str, timeout_ms: int) -> QueryResult:
    return execute_read_only(engine, sql, max_rows=COMPARE_ROWS, timeout_ms=timeout_ms)


def _print_summary(report: Report, console: Console) -> None:
    table = Table(title="Execution accuracy", show_header=False)
    table.add_row("Model", report.model)
    table.add_row("Cases", str(report.total))
    table.add_row("Passed", str(report.passed))
    table.add_row("Accuracy", f"{report.accuracy:.1%}")
    table.add_row("Avg latency", f"{report.avg_seconds}s")
    console.print(table)
