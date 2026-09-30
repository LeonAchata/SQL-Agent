"""Read-only query execution with a statement timeout and a hard row cap."""

import datetime as dt
import decimal
import logging
import time
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

REDACTED = "***"


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class ExecutionError(RuntimeError):
    """Database error, with a message short enough to feed back to the LLM for repair."""


def create_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # Open SQLite files read-only at the driver level.
        engine = sa.create_engine(url, connect_args={"check_same_thread": False})

        @sa.event.listens_for(engine, "connect")
        def _sqlite_read_only(dbapi_conn: Any, _: Any) -> None:
            dbapi_conn.execute("PRAGMA query_only = ON")

        return engine
    return sa.create_engine(url, pool_pre_ping=True)


def execute_read_only(
    engine: Engine,
    sql: str,
    *,
    max_rows: int,
    timeout_ms: int,
    masked_columns: set[str] | None = None,
) -> QueryResult:
    masked_columns = masked_columns or set()
    start = time.perf_counter()
    with engine.connect() as conn:
        try:
            _begin_read_only(conn, timeout_ms)
            result = conn.execute(sa.text(sql))
            columns = list(result.keys())
            fetched = result.fetchmany(max_rows + 1)
        except sa.exc.DBAPIError as exc:
            raise ExecutionError(_db_message(exc)) from exc
        finally:
            conn.rollback()  # never persist anything, whatever happened
            if conn.dialect.name == "sqlite" and conn.connection.dbapi_connection is not None:
                conn.connection.dbapi_connection.set_progress_handler(None, 0)

    truncated = len(fetched) > max_rows
    redact = [c.lower() in masked_columns for c in columns]
    rows = [
        [REDACTED if redact[i] else _jsonable(v) for i, v in enumerate(row)]
        for row in fetched[:max_rows]
    ]
    elapsed = int((time.perf_counter() - start) * 1000)
    log.info("Query returned %d rows in %d ms", len(rows), elapsed)
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), truncated=truncated, elapsed_ms=elapsed
    )


def _begin_read_only(conn: sa.Connection, timeout_ms: int) -> None:
    dialect = conn.dialect.name
    if dialect == "postgresql":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
    elif dialect in ("mysql", "mariadb"):
        conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET SESSION max_execution_time = {int(timeout_ms)}")
    elif dialect == "sqlite":
        _sqlite_timeout(conn, timeout_ms)
    # Other dialects rely on the SQL guard, the rollback above and a read-only DB role.


def _sqlite_timeout(conn: sa.Connection, timeout_ms: int) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    raw = conn.connection.dbapi_connection
    if raw is None:
        return

    def _abort_when_late() -> int:
        return 1 if time.monotonic() > deadline else 0

    raw.set_progress_handler(_abort_when_late, 10_000)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value) if value % 1 else int(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


def _db_message(exc: sa.exc.DBAPIError) -> str:
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "interrupted" in message.lower() or "canceling statement" in message.lower():
        return "Query timed out. Simplify it or filter more aggressively."
    return message.strip().splitlines()[0][:500]
