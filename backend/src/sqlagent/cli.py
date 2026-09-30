"""Command-line interface: ``sqlagent schema | ask | chat | serve | eval``."""

import logging
from pathlib import Path

import typer
from langgraph.checkpoint.memory import InMemorySaver
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from sqlagent.config import get_settings
from sqlagent.service import Event, SqlAgent, build_deps

app = typer.Typer(add_completion=False, help="Ask questions about any SQL database.")
console = Console()


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING)


@app.command()
def schema() -> None:
    """Print the schema catalog the agent sees."""
    settings = get_settings()
    from sqlagent.db.catalog import build_catalog
    from sqlagent.db.executor import create_engine
    from sqlagent.semantic import SemanticLayer

    catalog = build_catalog(
        create_engine(settings.database_url),
        schemas=settings.schemas or None,
        include=settings.include_tables,
        exclude=settings.exclude_tables,
        semantic=SemanticLayer.load(settings.semantic_layer_path),
        sample_values=settings.sample_values,
    )
    console.print(f"[bold]{len(catalog.tables)} relations[/] ({catalog.dialect})\n")
    console.print(catalog.render_detailed())


@app.command()
def ask(question: str) -> None:
    """Answer a single question."""
    agent = SqlAgent(build_deps(get_settings()), checkpointer=InMemorySaver())
    for event in agent.stream(question, agent.new_thread()):
        _render(event)


@app.command()
def chat() -> None:
    """Interactive multi-turn session."""
    agent = SqlAgent(build_deps(get_settings()), checkpointer=InMemorySaver())
    thread = agent.new_thread()
    console.print("[dim]Ask about your data. Ctrl+C to exit.[/]")
    while True:
        try:
            question = console.input("\n[bold cyan]you >[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if question:
            for event in agent.stream(question, thread):
                _render(event)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("sqlagent.api:app", host=host, port=port, reload=reload)


@app.command("eval")
def run_eval(
    suite: Path = typer.Argument(Path("evals/chinook.yaml")),
    limit: int | None = typer.Option(None, help="Only run the first N cases."),
    output: Path = typer.Option(Path("evals/results/latest.json")),
    min_accuracy: float = typer.Option(0.0, help="Exit non-zero below this accuracy (0-1)."),
) -> None:
    """Measure execution accuracy against a suite of questions with gold SQL."""
    from sqlagent.evals import run_suite

    report = run_suite(suite, limit=limit, output=output, console=console)
    raise typer.Exit(0 if report.accuracy >= min_accuracy else 1)


def _render(event: Event) -> None:
    data = event.data
    if event.type == "plan":
        console.print(f"[dim]tables → {', '.join(data['tables'])}[/]")
    elif event.type == "validated":
        console.print(Syntax(data["sql"] or "", "sql", theme="ansi_dark", word_wrap=True))
    elif event.type == "retry":
        console.print(f"[yellow]↻ {data['stage']} error:[/] {data['error']}")
    elif event.type == "result":
        table = Table(show_lines=False, header_style="bold")
        for col in data["columns"]:
            table.add_column(str(col))
        for row in data["rows"][:15]:
            table.add_row(*["NULL" if v is None else str(v) for v in row])
        console.print(table)
        more = data["row_count"] - 15
        if more > 0:
            console.print(f"[dim]… {more} more rows[/]")
    elif event.type == "answer":
        style = "green" if data["status"] == "answered" else "yellow"
        console.print(Panel(Markdown(data["content"]), border_style=style))


if __name__ == "__main__":
    app()
