"""Database introspection: turn any SQLAlchemy-reachable database into a schema catalog the LLM
can reason about (tables, columns, keys, comments and sample values)."""

import logging
from collections.abc import Iterable, Sequence
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from sqlagent.semantic import SemanticLayer

log = logging.getLogger(__name__)

# SQLAlchemy dialect name -> sqlglot dialect name.
SQLGLOT_DIALECTS = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
    "mssql": "tsql",
    "oracle": "oracle",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "duckdb": "duckdb",
    "redshift": "redshift",
    "trino": "trino",
}

SAMPLE_SIZE = 5
MAX_SAMPLE_CHARS = 40
MAX_TABLES_TO_SAMPLE = 150


class Column(BaseModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    comment: str | None = None
    samples: list[str] = Field(default_factory=list)


class ForeignKey(BaseModel):
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


class Table(BaseModel):
    schema_name: str | None = None
    name: str
    kind: str = "table"
    comment: str | None = None
    columns: list[Column] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}" if self.schema_name else self.name


class Catalog(BaseModel):
    dialect: str
    tables: list[Table]

    def get(self, name: str) -> Table | None:
        """Case-insensitive lookup by bare or schema-qualified name."""
        key = name.strip('"`[]').lower()
        for table in self.tables:
            if key in (table.qualified_name.lower(), table.name.lower()):
                return table
        return None

    def resolve(self, names: Iterable[str]) -> list[Table]:
        seen: dict[str, Table] = {}
        for name in names:
            table = self.get(name)
            if table is not None:
                seen.setdefault(table.qualified_name, table)
        return list(seen.values())

    def with_neighbors(self, tables: list[Table]) -> list[Table]:
        """Add tables one foreign-key hop away (in either direction) so joins are possible."""
        wanted = {t.qualified_name for t in tables}
        for table in self.tables:
            refs = {self._ref_name(fk) for fk in table.foreign_keys}
            if table.qualified_name in wanted:
                wanted |= refs
            elif refs & {t.qualified_name for t in tables}:
                wanted.add(table.qualified_name)
        return [t for t in self.tables if t.qualified_name in wanted]

    def _ref_name(self, fk: ForeignKey) -> str:
        ref = self.get(fk.ref_table)
        return ref.qualified_name if ref else fk.ref_table

    def render_compact(self) -> str:
        """One line per table - enough to pick relevant tables in a large schema."""
        lines = []
        for t in self.tables:
            cols = ", ".join(c.name for c in t.columns)
            note = f"  -- {t.comment}" if t.comment else ""
            lines.append(f"{t.qualified_name}({cols}){note}")
        return "\n".join(lines)

    def render_detailed(self, tables: list[Table] | None = None) -> str:
        """DDL-like description with keys, comments and sample values."""
        blocks = []
        for t in tables if tables is not None else self.tables:
            header = f"{t.kind.upper()} {t.qualified_name}"
            if t.comment:
                header += f"  -- {t.comment}"
            lines = [header]
            for c in t.columns:
                flags = [c.type]
                if c.primary_key:
                    flags.append("PK")
                if not c.nullable:
                    flags.append("NOT NULL")
                line = f"  {c.name} {' '.join(flags)}"
                extras = []
                if c.comment:
                    extras.append(c.comment)
                if c.samples:
                    extras.append("e.g. " + ", ".join(repr(s) for s in c.samples))
                if extras:
                    line += "  -- " + "; ".join(extras)
                lines.append(line)
            for fk in t.foreign_keys:
                lines.append(
                    f"  FK ({', '.join(fk.columns)}) -> {fk.ref_table}({', '.join(fk.ref_columns)})"
                )
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


def sqlglot_dialect(engine: Engine) -> str:
    return SQLGLOT_DIALECTS.get(engine.dialect.name, engine.dialect.name)


def build_catalog(
    engine: Engine,
    *,
    schemas: list[str] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    semantic: SemanticLayer | None = None,
    sample_values: bool = True,
) -> Catalog:
    semantic = semantic or SemanticLayer()
    inspector = sa.inspect(engine)
    include_set = {n.lower() for n in include or []}
    exclude_set = {n.lower() for n in exclude or []}
    masked = semantic.masked_column_names()

    def wanted(schema: str | None, name: str) -> bool:
        keys = {name.lower(), f"{schema}.{name}".lower()}
        if include_set and not keys & include_set:
            return False
        return not keys & exclude_set

    tables: list[Table] = []
    targets: list[str | None] = list(schemas) if schemas else [None]
    for schema in targets:
        relations = [(n, "table") for n in inspector.get_table_names(schema=schema)]
        relations += [(n, "view") for n in inspector.get_view_names(schema=schema)]
        for name, kind in relations:
            if wanted(schema, name):
                tables.append(_describe(inspector, schema, name, kind, semantic))

    if sample_values and len(tables) <= MAX_TABLES_TO_SAMPLE:
        with engine.connect() as conn:
            for table in tables:
                _collect_samples(conn, table, masked)

    log.info("Catalog built: %d relations (%s)", len(tables), engine.dialect.name)
    return Catalog(dialect=sqlglot_dialect(engine), tables=tables)


def _describe(
    inspector: sa.Inspector, schema: str | None, name: str, kind: str, semantic: SemanticLayer
) -> Table:
    pk = set(inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
    notes = semantic.table_notes(f"{schema}.{name}" if schema else name)
    column_notes = {k.lower(): v for k, v in (notes.columns if notes else {}).items()}

    columns = [
        Column(
            name=col["name"],
            type=_type_name(col["type"]),
            nullable=bool(col.get("nullable", True)),
            primary_key=col["name"] in pk,
            comment=column_notes.get(col["name"].lower()) or col.get("comment"),
        )
        for col in inspector.get_columns(name, schema=schema)
    ]
    fks = [
        ForeignKey(
            columns=fk["constrained_columns"],
            ref_table=(
                f"{fk['referred_schema']}.{fk['referred_table']}"
                if fk.get("referred_schema")
                else fk["referred_table"]
            ),
            ref_columns=fk["referred_columns"],
        )
        for fk in inspector.get_foreign_keys(name, schema=schema)
    ]
    try:
        db_comment = inspector.get_table_comment(name, schema=schema).get("text")
    except NotImplementedError:
        db_comment = None

    return Table(
        schema_name=schema,
        name=name,
        kind=kind,
        comment=(notes.description if notes else None) or db_comment,
        columns=columns,
        foreign_keys=fks,
    )


def _type_name(type_: object) -> str:
    try:
        return str(type_)
    except Exception:  # some dialect types cannot compile without a dialect
        return type(type_).__name__.upper()


def _is_textual(type_name: str) -> bool:
    t = type_name.upper()
    return any(k in t for k in ("CHAR", "TEXT", "STRING", "ENUM", "CLOB"))


def _collect_samples(conn: sa.Connection, table: Table, masked: set[str]) -> None:
    for column in table.columns:
        if column.primary_key or column.name.lower() in masked or not _is_textual(column.type):
            continue
        col: sa.ColumnClause[Any] = sa.column(column.name)
        rel = sa.table(table.name, col, schema=table.schema_name)
        stmt = sa.select(col).select_from(rel).where(col.is_not(None)).distinct().limit(SAMPLE_SIZE)
        values: Sequence[Any]
        try:
            values = conn.execute(stmt).scalars().all()
        except sa.exc.SQLAlchemyError as exc:
            log.debug("Sampling %s.%s failed: %s", table.name, column.name, exc)
            conn.rollback()
            continue
        column.samples = [s[:MAX_SAMPLE_CHARS] for s in map(str, values)]
