"""Structured outputs the model is asked to produce at each step."""

from typing import Literal

from pydantic import BaseModel, Field


class Plan(BaseModel):
    intent: Literal["query", "clarify", "out_of_scope"] = Field(
        description="'query' if the question can be answered from the database, 'clarify' if it "
        "is too ambiguous to answer, 'out_of_scope' for anything else (greetings, unrelated)."
    )
    standalone_question: str = Field(
        description="The user's question rewritten to be self-contained, resolving references "
        "to earlier turns (e.g. 'and in 2012?' -> 'Total sales per country in 2012')."
    )
    tables: list[str] = Field(
        default_factory=list,
        description="Tables (as named in the schema) needed to answer. Empty unless intent=query.",
    )
    reply: str | None = Field(
        default=None,
        description="For 'clarify': one concise clarifying question. For 'out_of_scope': a short "
        "reply that explains what this assistant can help with. Otherwise null.",
    )


class SqlDraft(BaseModel):
    reasoning: str = Field(description="Brief plan: tables, joins, filters, aggregation.")
    sql: str = Field(description="A single read-only SELECT statement in the target dialect.")


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "pie", "none"] = Field(
        description="'line' for time series, 'bar' for category comparisons, 'pie' only for "
        "shares of a whole with <= 6 slices, 'none' if a chart would not help."
    )
    x: str | None = Field(default=None, description="Result column for the x axis / labels.")
    y: list[str] = Field(default_factory=list, description="Numeric result column(s) to plot.")
    title: str | None = None


class Answer(BaseModel):
    answer: str = Field(
        description="Direct answer to the question in Markdown, grounded only in the result "
        "rows. Lead with the key number or finding; keep it under ~120 words."
    )
    chart: ChartSpec
