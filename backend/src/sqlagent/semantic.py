"""Optional semantic layer: business knowledge the database itself cannot express.

Example ``semantic.yaml``::

    tables:
      invoice:
        description: One row per customer purchase.
        columns:
          total: Invoice amount in USD, taxes included.
    glossary:
      - term: revenue
        definition: SUM(invoice_line.unit_price * invoice_line.quantity)
    examples:
      - question: Top 5 artists by revenue
        sql: SELECT ...
    masked_columns: [customer.email, customer.phone]
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class TableNotes(BaseModel):
    description: str | None = None
    columns: dict[str, str] = Field(default_factory=dict)


class GlossaryTerm(BaseModel):
    term: str
    definition: str


class ExampleQuery(BaseModel):
    question: str
    sql: str


class SemanticLayer(BaseModel):
    tables: dict[str, TableNotes] = Field(default_factory=dict)
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    examples: list[ExampleQuery] = Field(default_factory=list)
    masked_columns: list[str] = Field(
        default_factory=list, description="'table.column' entries whose values are redacted."
    )

    @classmethod
    def load(cls, path: Path | None) -> "SemanticLayer":
        if path is None:
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def table_notes(self, table: str) -> TableNotes | None:
        key = table.lower()
        for name, notes in self.tables.items():
            if name.lower() == key or name.lower() == key.rsplit(".", 1)[-1]:
                return notes
        return None

    def masked_column_names(self) -> set[str]:
        """Bare column names to redact in result sets (the table part is advisory)."""
        return {entry.rsplit(".", 1)[-1].lower() for entry in self.masked_columns}

    def render(self) -> str:
        parts: list[str] = []
        if self.glossary:
            parts.append("Business glossary:")
            parts += [f"- {g.term}: {g.definition}" for g in self.glossary]
        if self.examples:
            parts.append("\nVerified example queries:")
            for ex in self.examples:
                parts.append(f"Q: {ex.question}\nSQL: {ex.sql.strip()}")
        return "\n".join(parts)
