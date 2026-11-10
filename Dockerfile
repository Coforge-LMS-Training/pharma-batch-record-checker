# Image for the review app and the synthetic data generator.
# Compose (docker-compose.yml) runs it twice: once as `generator`, which writes a
# dataset into the pharma_data volume, and once as `app`, which loads it and
# serves the review pages. See docs/containers.md.

# --- Build: install the locked dependencies and the package into /app/.venv ---
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so this layer is reused until uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable

# --- Runtime: the venv plus the files the app reads, run as a non-root user ---
FROM python:3.12-slim-bookworm
# /data is the pharma_data mount point. A fresh named volume takes the
# ownership of the directory it is mounted on, so the generator can write it.
RUN useradd --create-home --uid 1000 app \
    && mkdir /data \
    && chown app:app /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Migrations, the approved sample (the app's default data) and the generator's
# catalog. PHARMA_ROOT tells the app where they are.
COPY alembic.ini ./
COPY alembic/ alembic/
COPY fixtures/approved_sample/ fixtures/approved_sample/
COPY catalog/ catalog/
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PHARMA_ROOT=/app
USER app
EXPOSE 8000
CMD ["pharma-review-demo", "--host", "0.0.0.0"]
