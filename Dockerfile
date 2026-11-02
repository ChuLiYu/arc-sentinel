# Arc Sentinel runtime image (SPEC §11). Single image runs indexer or API via CMD override.
FROM python:3.12-slim

# Pin the build tool as well as Python dependencies.
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# deps first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project --frozen

# then the package itself
COPY app ./app
COPY alembic.ini ./
RUN uv sync --no-dev --frozen

RUN useradd --uid 10001 --create-home sentinel
USER sentinel

EXPOSE 8000

# default: API. Compose overrides command for the indexer worker.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
