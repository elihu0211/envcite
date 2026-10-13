# Consult service image. Build from the repo root:
#
#   docker build -f services/consult/Dockerfile .
#
# The Python workspace (root pyproject.toml and uv.lock) is shared by every
# service, but only this service's members are copied in. uv cannot check the
# lockfile against members it does not see, so both syncs are `--frozen`:
# they install exactly what uv.lock says. The lockfile itself is checked by
# `uv sync --locked` in CI.
#
# CI passes both versions from its own variables; the defaults are for local builds.
#
# Nothing is downloaded here. Wheels come from the uv cache that CI's `deps-python` job
# filled, passed as the build context `uv-cache`, and every uv step runs with
# `--network=none` and UV_OFFLINE: a package missing from that cache fails the build
# instead of reaching PyPI. The cache is bind-mounted, so it never becomes an image layer.
#
#   UV_CACHE_DIR=$PWD/.uv-cache uv sync --locked --all-packages
#   docker buildx build -f services/consult/Dockerfile --build-context uv-cache=.uv-cache .

ARG UV_VERSION=0.12.18
ARG PYTHON_VERSION=3.14
# Registries are parameters so CI can point every base image at the runner host's
# pull-through caches (and nowhere else: a missing image fails the build instead of falling
# back to upstream). The defaults are the real registries, for local builds.
ARG DOCKER_HUB=docker.io
ARG GHCR=ghcr.io

FROM ${GHCR}/astral-sh/uv:${UV_VERSION}-python${PYTHON_VERSION}-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_OFFLINE=1 \
    UV_CACHE_DIR=/uv-cache
WORKDIR /app
# Dependencies first, so a source-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN --network=none --mount=type=bind,from=uv-cache,target=/uv-cache,rw \
    uv sync --frozen --no-dev --package env-law-consult --no-install-workspace
COPY libs/python/env-law-grpc libs/python/env-law-grpc
COPY libs/python/env-law-proto libs/python/env-law-proto
COPY services/consult/pyproject.toml services/consult/README.md services/consult/
COPY services/consult/src services/consult/src
RUN --network=none --mount=type=bind,from=uv-cache,target=/uv-cache,rw \
    uv sync --frozen --no-dev --package env-law-consult \
 && .venv/bin/python -m compileall -q services/consult/src libs/python

# Same base as the uv image above, so the venv's interpreter path resolves.
FROM ${DOCKER_HUB}/library/python:${PYTHON_VERSION}-slim-trixie
# The pod runs with a read-only root filesystem; bytecode is compiled at build.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
COPY --from=build /app /app
WORKDIR /app/services/consult
# Matches runAsUser/runAsGroup in env-law-deploy base/consult.
USER 1000:1000
EXPOSE 50052
CMD ["env-law-consult"]
