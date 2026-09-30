import pytest
import sqlalchemy as sa

from sqlagent.db.catalog import Catalog, build_catalog
from sqlagent.db.executor import REDACTED, ExecutionError, execute_read_only
from sqlagent.semantic import SemanticLayer, TableNotes


def test_catalog_introspects_keys_and_samples(catalog: Catalog) -> None:
    assert {t.name for t in catalog.tables} == {"customer", "orders", "product"}
    orders = catalog.get("ORDERS")
    assert orders is not None
    assert [c.name for c in orders.columns if c.primary_key] == ["id"]
    assert orders.foreign_keys[0].ref_table == "customer"
    country = next(c for c in catalog.get("customer").columns if c.name == "country")  # type: ignore[union-attr]
    assert set(country.samples) == {"UK", "Finland", "USA"}


def test_neighbors_follow_foreign_keys_both_ways(catalog: Catalog) -> None:
    names = {t.name for t in catalog.with_neighbors(catalog.resolve(["customer"]))}
    assert names == {"customer", "orders"}
    names = {t.name for t in catalog.with_neighbors(catalog.resolve(["orders"]))}
    assert names == {"customer", "orders"}


def test_semantic_layer_and_filters(engine: sa.Engine) -> None:
    semantic = SemanticLayer(
        tables={"orders": TableNotes(description="Purchases", columns={"total": "USD"})},
        masked_columns=["customer.email"],
    )
    cat = build_catalog(engine, exclude=["product"], semantic=semantic)
    assert cat.get("product") is None
    orders = cat.get("orders")
    assert orders is not None and orders.comment == "Purchases"
    assert "USD" in cat.render_detailed()
    email = next(c for c in cat.get("customer").columns if c.name == "email")  # type: ignore[union-attr]
    assert email.samples == []  # masked columns are never sampled


def test_executor_returns_rows_and_truncates(engine: sa.Engine) -> None:
    result = execute_read_only(
        engine, "SELECT id FROM orders ORDER BY id", max_rows=2, timeout_ms=5000
    )
    assert result.rows == [[1], [2]]
    assert result.truncated


def test_executor_masks_columns(engine: sa.Engine) -> None:
    result = execute_read_only(
        engine,
        "SELECT name, email FROM customer",
        max_rows=10,
        timeout_ms=5000,
        masked_columns={"email"},
    )
    assert all(row[1] == REDACTED for row in result.rows)


def test_executor_is_read_only_even_if_guard_is_bypassed(engine: sa.Engine) -> None:
    with pytest.raises(ExecutionError):
        execute_read_only(engine, "DELETE FROM customer", max_rows=10, timeout_ms=5000)
    count = execute_read_only(engine, "SELECT COUNT(*) FROM customer", max_rows=1, timeout_ms=5000)
    assert count.rows == [[3]]


def test_executor_times_out(engine: sa.Engine) -> None:
    slow = """
        WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 50000000)
        SELECT SUM(x) FROM n
    """
    with pytest.raises(ExecutionError, match="timed out"):
        execute_read_only(engine, slow, max_rows=1, timeout_ms=200)
    # the connection is healthy afterwards
    ok = execute_read_only(engine, "SELECT 1", max_rows=1, timeout_ms=5000)
    assert ok.rows == [[1]]


def test_demo_semantic_layer_is_valid() -> None:
    from pathlib import Path

    layer = SemanticLayer.load(Path(__file__).parent.parent / "semantic" / "chinook.postgres.yaml")
    assert layer.table_notes("public.invoice") is not None
    assert "email" in layer.masked_column_names()
    assert "revenue" in layer.render()
