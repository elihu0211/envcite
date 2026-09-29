# Ontology service image. Build from the repo root:
#
#   docker build -f services/ontology/Dockerfile .
#
# One image, two entrypoints: the server (default) and `env-law-migrate`, which
# the migration Job runs. The layout under /app mirrors the repo because the
# migrator finds migrations/ relative to its own source file.
#
# The Python workspace (root pyproject.toml and uv.lock) is shared by every
# service, but only this service's members are copied in. uv cannot check the
# lockfile against members it does not see, so both syncs are `--frozen`:
# they install exactly what uv.lock says. The lockfile itself is checked by
# `uv sync --locked` in CI.
#
# CI passes both versions from its own variables; the defaults are for local builds.

ARG UV_VERSION=0.12.18
ARG PYTHON_VERSION=3.14

FROM ghcr.io/astral-sh/uv:${UV_VERSION}-python${PYTHON_VERSION}-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first, so a source-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --package env-law-ontology --no-install-workspace
COPY libs/python/env-law-grpc libs/python/env-law-grpc
COPY libs/python/env-law-proto libs/python/env-law-proto
COPY libs/python/env-law-segmenter libs/python/env-law-segmenter
COPY services/ontology/pyproject.toml services/ontology/README.md services/ontology/
COPY services/ontology/src services/ontology/src
COPY services/ontology/migrations services/ontology/migrations
RUN uv sync --frozen --no-dev --package env-law-ontology \
 && .venv/bin/python -m compileall -q services/ontology/src libs/python

# Same base as the uv image above, so the venv's interpreter path resolves.
FROM python:${PYTHON_VERSION}-slim-trixie
# The pod runs with a read-only root filesystem; bytecode is compiled at build.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
COPY --from=build /app /app
WORKDIR /app/services/ontology
# Matches runAsUser/runAsGroup in env-law-deploy base/ontology.
USER 1000:1000
EXPOSE 50051
CMD ["env-law-ontology"]
