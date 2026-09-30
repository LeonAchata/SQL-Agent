"""Runtime configuration, loaded from environment variables (prefix ``SQLAGENT_``) or ``.env``."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SQLAGENT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database -----------------------------------------------------------------------------
    database_url: str = Field(
        default="sqlite:///data/chinook.sqlite",
        description="Any SQLAlchemy URL: postgresql+psycopg://, mysql+pymysql://, sqlite:///, ...",
    )
    schemas: list[str] = Field(
        default_factory=list, description="Schemas to expose. Empty means the default schema."
    )
    include_tables: list[str] = Field(
        default_factory=list, description="Allow-list of tables. Empty means all tables."
    )
    exclude_tables: list[str] = Field(default_factory=list)
    sample_values: bool = Field(
        default=True, description="Collect a few distinct values per text column for grounding."
    )

    # --- Execution guard-rails ----------------------------------------------------------------
    max_rows: int = Field(default=200, ge=1, le=10_000)
    statement_timeout_ms: int = Field(default=15_000, ge=100)
    max_attempts: int = Field(default=3, ge=1, le=6, description="SQL generate/repair attempts.")

    # --- Schema context -----------------------------------------------------------------------
    full_schema_threshold: int = Field(
        default=40,
        description="Up to this many tables the whole schema goes into the prompt; above it the "
        "agent first selects relevant tables.",
    )
    semantic_layer_path: Path | None = Field(
        default=None, description="Optional YAML with descriptions, glossary and example queries."
    )

    # --- LLM ----------------------------------------------------------------------------------
    llm_provider: Literal["anthropic"] = "anthropic"
    model: str = "claude-opus-5-5"
    effort: Effort = "medium"
    refusal_fallbacks: bool = True

    # --- Persistence / API --------------------------------------------------------------------
    checkpoint_path: str = Field(
        default="data/threads.sqlite", description="SQLite file for conversation history."
    )
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
