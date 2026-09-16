FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --only-group platform
COPY services ./services
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "services.worker.omni_worker.main"]
