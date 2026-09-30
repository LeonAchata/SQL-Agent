"""Static SQL guard: parse the generated SQL into an AST and only let safe, read-only queries
against known tables through. This is the first line of defence; the executor adds a
read-only transaction and a statement timeout on top."""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from sqlagent.db.catalog import Catalog

# Statement / clause types that can change data, schema, session state or the filesystem.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Into,
    exp.Set,
    exp.Use,
    exp.Grant,
    exp.Copy,
    exp.LoadData,
    exp.Pragma,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Lock,
)

# Functions with side effects, filesystem/network access or denial-of-service potential.
FORBIDDEN_FUNCTIONS = {
    # PostgreSQL
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file",
    "pg_ls_dir", "pg_stat_file", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_rotate_logfile", "lo_import", "lo_export", "lo_get", "dblink", "dblink_exec",
    "set_config", "current_setting", "query_to_xml", "pg_advisory_lock", "txid_current",
    # MySQL
    "sleep", "benchmark", "load_file", "get_lock", "sys_exec", "sys_eval",
    # SQL Server
    "xp_cmdshell", "openrowset", "opendatasource", "openquery",
    # SQLite
    "load_extension", "readfile", "writefile", "randomblob", "zeroblob",
}  # fmt: skip


class GuardError(ValueError):
    """Raised when a query is rejected. The message is safe to show to the LLM for repair."""


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    tables: list[str]
    limited: bool


def guard_sql(sql: str, catalog: Catalog, *, max_rows: int) -> GuardedQuery:
    """Validate ``sql`` and return a normalised version with a row limit enforced.

    Raises:
        GuardError: if the SQL does not parse, is not a single read-only query, touches
            tables outside the catalog, or calls a forbidden function.
    """
    dialect = catalog.dialect
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except ParseError as exc:
        raise GuardError(f"SQL does not parse as {dialect}: {_first_line(exc)}") from exc

    if len(statements) != 1:
        raise GuardError(f"Exactly one statement is allowed, got {len(statements)}.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise GuardError(f"Only read-only queries are allowed, got {tree.key.upper()}.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardError(f"{node.key.upper()} is not allowed in a read-only query.")
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in FORBIDDEN_FUNCTIONS:
                raise GuardError(f"Function {name}() is not allowed.")

    tables = _referenced_tables(tree)
    unknown = [t for t in tables if catalog.get(t) is None]
    if unknown:
        raise GuardError(
            f"Unknown or non-permitted table(s): {', '.join(unknown)}. "
            "Use only tables from the provided schema."
        )

    tree, limited = _enforce_limit(tree, max_rows)
    return GuardedQuery(sql=tree.sql(dialect=dialect, pretty=True), tables=tables, limited=limited)


def _referenced_tables(tree: exp.Query) -> list[str]:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        if not table.name:  # table-valued functions, e.g. generate_series()
            continue
        if table.name.lower() in cte_names and not table.db:
            continue
        qualified = f"{table.db}.{table.name}" if table.db else table.name
        if qualified not in names:
            names.append(qualified)
    return names


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def _enforce_limit(tree: exp.Query, max_rows: int) -> tuple[exp.Query, bool]:
    """Cap the outermost query at ``max_rows``. Returns the tree and whether a cap was applied."""
    node = tree.args.get("limit")  # holds exp.Limit (LIMIT / TOP) or exp.Fetch (FETCH FIRST)
    if isinstance(node, exp.Limit):
        current = _literal_int(node.expression)
    elif isinstance(node, exp.Fetch):
        current = _literal_int(node.args.get("count"))
    else:
        current = None
    if current is not None and current <= max_rows:
        return tree, False
    return tree.limit(max_rows, copy=True), True


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    return None


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:300]
