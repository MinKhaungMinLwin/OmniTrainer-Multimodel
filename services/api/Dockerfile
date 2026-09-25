FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --only-group platform
COPY services ./services
COPY data/environment ./data/environment
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8001
CMD ["uvicorn", "services.api.omni_api.main:app", "--host", "0.0.0.0", "--port", "8001"]
