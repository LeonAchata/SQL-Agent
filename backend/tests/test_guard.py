import pytest

from sqlagent.db.catalog import Catalog
from sqlagent.safety.guard import GuardError, guard_sql


def test_accepts_select_and_adds_limit(catalog: Catalog) -> None:
    out = guard_sql("SELECT name FROM customer", catalog, max_rows=50)
    assert out.limited
    assert "LIMIT 50" in out.sql
    assert out.tables == ["customer"]


def test_keeps_smaller_limit(catalog: Catalog) -> None:
    out = guard_sql("SELECT name FROM customer LIMIT 3", catalog, max_rows=50)
    assert not out.limited
    assert "LIMIT 3" in out.sql


def test_caps_larger_limit(catalog: Catalog) -> None:
    out = guard_sql("SELECT name FROM customer LIMIT 100000", catalog, max_rows=50)
    assert out.limited
    assert "LIMIT 50" in out.sql


def test_allows_ctes_joins_and_unions(catalog: Catalog) -> None:
    sql = """
        WITH big AS (SELECT customer_id, SUM(total) AS spent FROM orders GROUP BY customer_id)
        SELECT c.name, b.spent FROM customer c JOIN big b ON b.customer_id = c.id
        UNION ALL SELECT 'none', 0 FROM product
    """
    out = guard_sql(sql, catalog, max_rows=10)
    assert set(out.tables) == {"orders", "customer", "product"}


def test_allows_double_dash_inside_strings(catalog: Catalog) -> None:
    guard_sql("SELECT name FROM customer WHERE name <> '--'", catalog, max_rows=10)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customer",
        "UPDATE customer SET name = 'x'",
        "DROP TABLE customer",
        "INSERT INTO customer (name) VALUES ('x')",
        "SELECT 1; DROP TABLE customer",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE 'evil.db' AS evil",
        "SELECT load_extension('evil')",
        "SELECT * INTO backup FROM customer",
    ],
)
def test_rejects_writes_and_side_effects(catalog: Catalog, sql: str) -> None:
    with pytest.raises(GuardError):
        guard_sql(sql, catalog, max_rows=10)


def test_rejects_unknown_tables(catalog: Catalog) -> None:
    with pytest.raises(GuardError, match="sqlite_master"):
        guard_sql("SELECT sql FROM sqlite_master", catalog, max_rows=10)


def test_rejects_forbidden_functions_postgres(catalog: Catalog) -> None:
    pg = catalog.model_copy(update={"dialect": "postgres"})
    with pytest.raises(GuardError, match="pg_sleep"):
        guard_sql("SELECT pg_sleep(10) FROM customer", pg, max_rows=10)
    with pytest.raises(GuardError, match="pg_read_file"):
        guard_sql("SELECT pg_read_file('/etc/passwd') FROM customer", pg, max_rows=10)


def test_tsql_uses_top(catalog: Catalog) -> None:
    ms = catalog.model_copy(update={"dialect": "tsql"})
    out = guard_sql("SELECT name FROM customer", ms, max_rows=25)
    assert "TOP 25" in out.sql


def test_rejects_garbage(catalog: Catalog) -> None:
    with pytest.raises(GuardError):
        guard_sql("SELEC name FRM customer", catalog, max_rows=10)
