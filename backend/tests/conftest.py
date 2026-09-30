from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import BaseModel

from sqlagent.config import Settings
from sqlagent.db.catalog import Catalog, build_catalog
from sqlagent.db.executor import create_engine
from sqlagent.llm.base import LLMMessage

SCHEMA = """
CREATE TABLE customer (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    country TEXT
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    total NUMERIC NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE product (id INTEGER PRIMARY KEY, title TEXT);
"""

ROWS = """
INSERT INTO customer VALUES (1, 'Ada', 'ada@example.com', 'UK'),
                            (2, 'Linus', 'l@example.com', 'Finland'),
                            (3, 'Grace', 'g@example.com', 'USA');
INSERT INTO orders VALUES (1, 1, 10.5, '2024-01-02'), (2, 1, 20, '2024-02-10'),
                          (3, 2, 5, '2024-02-11'), (4, 3, 99.99, '2024-03-01');
"""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "shop.sqlite"
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        for stmt in (SCHEMA + ROWS).split(";"):
            if stmt.strip():
                conn.exec_driver_sql(stmt)
    engine.dispose()
    return path


@pytest.fixture
def engine(db_path: Path) -> sa.Engine:
    return create_engine(f"sqlite:///{db_path}")


@pytest.fixture
def catalog(engine: sa.Engine) -> Catalog:
    return build_catalog(engine)


@pytest.fixture
def settings(db_path: Path, tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{db_path}",
        checkpoint_path=str(tmp_path / "threads.sqlite"),
        max_attempts=3,
        _env_file=None,  # type: ignore[call-arg]
    )


class FakeLLM:
    """Returns scripted responses per output schema and records every request."""

    def __init__(self, script: dict[type[BaseModel], list[Any]]) -> None:
        self.queues: dict[type[BaseModel], deque[Any]] = defaultdict(deque)
        for schema, items in script.items():
            self.queues[schema].extend(items)
        self.calls: list[dict[str, Any]] = []

    def structured(
        self,
        *,
        instructions: str,
        context: str,
        messages: list[LLMMessage],
        schema: type[BaseModel],
    ) -> Any:
        self.calls.append(
            {
                "schema": schema,
                "instructions": instructions,
                "context": context,
                "messages": messages,
            }
        )
        item = self.queues[schema].popleft()
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, BaseModel) else schema.model_validate(item)
