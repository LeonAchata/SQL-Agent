FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project

COPY src ./src
COPY data/chinook.sqlite ./data/chinook.sqlite
COPY semantic ./semantic
COPY evals ./evals
RUN uv sync --frozen --no-dev --all-extras

ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["sqlagent", "serve", "--host", "0.0.0.0", "--port", "8000"]
