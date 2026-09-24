# Consult service image. Build from the repo root — the path dependency on
# libs/python/env-law-grpc lives outside this directory:
#
#   docker build -f services/consult/Dockerfile .
#
# Same shape as services/ontology/Dockerfile; see there for the reasoning. CI
# passes both versions from its own variables; the defaults are for local builds.

ARG UV_VERSION=0.12.18
ARG PYTHON_VERSION=3.14

FROM ghcr.io/astral-sh/uv:${UV_VERSION}-python${PYTHON_VERSION}-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app/services/consult
# Dependencies first, so a source-only change reuses this layer.
COPY libs/python/env-law-grpc /app/libs/python/env-law-grpc
COPY services/consult/pyproject.toml services/consult/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY services/consult/src ./src
COPY services/consult/README.md ./
RUN uv sync --locked --no-dev \
 && .venv/bin/python -m compileall -q src

# Same base as the uv image above, so the venv's interpreter path resolves.
FROM python:${PYTHON_VERSION}-slim-trixie
# The pod runs with a read-only root filesystem; bytecode is compiled at build.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/services/consult/.venv/bin:$PATH
COPY --from=build /app /app
WORKDIR /app/services/consult
# Matches runAsUser/runAsGroup in env-law-deploy base/consult.
USER 1000:1000
EXPOSE 50052
CMD ["env-law-consult"]
