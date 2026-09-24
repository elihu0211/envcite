# Ontology service image. Build from the repo root — the path dependency on
# libs/python/env-law-{grpc,segmenter} live outside this directory:
#
#   docker build -f services/ontology/Dockerfile .
#
# One image, two entrypoints: the server (default) and `env-law-migrate`, which
# the migration Job runs. The layout under /app mirrors the repo because the
# migrator finds migrations/ relative to its own source file.
#
# CI passes both versions from its own variables; the defaults are for local builds.

ARG UV_VERSION=0.12.18
ARG PYTHON_VERSION=3.14

FROM ghcr.io/astral-sh/uv:${UV_VERSION}-python${PYTHON_VERSION}-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app/services/ontology
# Dependencies first, so a source-only change reuses this layer.
COPY libs/python/env-law-grpc /app/libs/python/env-law-grpc
COPY libs/python/env-law-segmenter /app/libs/python/env-law-segmenter
COPY services/ontology/pyproject.toml services/ontology/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY services/ontology/src ./src
COPY services/ontology/migrations ./migrations
COPY services/ontology/README.md ./
RUN uv sync --locked --no-dev \
 && .venv/bin/python -m compileall -q src

# Same base as the uv image above, so the venv's interpreter path resolves.
FROM python:${PYTHON_VERSION}-slim-trixie
# The pod runs with a read-only root filesystem; bytecode is compiled at build.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/services/ontology/.venv/bin:$PATH
COPY --from=build /app /app
WORKDIR /app/services/ontology
# Matches runAsUser/runAsGroup in env-law-deploy base/ontology.
USER 1000:1000
EXPOSE 50051
CMD ["env-law-ontology"]
