# Consult 服務映像，以倉庫根為建置情境：
#
#   docker build -f services/consult/Dockerfile .
#
# Python workspace（根的 pyproject.toml 與 uv.lock）由所有服務共用，但只複製本服務的成員。
# uv 無法對看不到的成員檢查鎖檔，所以兩次 sync 都用 `--frozen`，完全照 uv.lock 安裝；
# 鎖檔本身由 CI 的 `uv sync --locked` 檢查。
#
# 兩個版本由 CI 的變數傳入，預設值供本機建置。
#
# 建置中不下載任何東西：wheel 來自 CI 的 `deps-python` job 備好的 uv 快取，以 build context
# `uv-cache` 傳入；每個 uv 步驟都以 `--network=none` 加 UV_OFFLINE 執行，缺套件即失敗，
# 不會連 PyPI。快取是 bind mount，不會成為映像層。
#
#   UV_CACHE_DIR=$PWD/.uv-cache uv sync --locked --all-packages
#   docker buildx build -f services/consult/Dockerfile --build-context uv-cache=.uv-cache .

ARG UV_VERSION=0.12.18
ARG PYTHON_VERSION=3.14
# registry 是參數：CI 讓所有基底映像只走 runner 主機的 pull-through 快取，沒有就失敗，不退回上游。
# 預設是真正的 registry，供本機建置。
ARG DOCKER_HUB=docker.io
ARG GHCR=ghcr.io

FROM ${GHCR}/astral-sh/uv:${UV_VERSION}-python${PYTHON_VERSION}-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_OFFLINE=1 \
    UV_CACHE_DIR=/uv-cache
WORKDIR /app
# 先裝依賴，純原始碼變動時沿用這一層。
COPY pyproject.toml uv.lock ./
RUN --network=none --mount=type=bind,from=uv-cache,target=/uv-cache,rw \
    uv sync --frozen --no-dev --package envcite-consult --no-install-workspace
COPY libs/python/envcite-grpc libs/python/envcite-grpc
COPY libs/python/envcite-proto libs/python/envcite-proto
COPY services/consult/pyproject.toml services/consult/README.md services/consult/
COPY services/consult/src services/consult/src
RUN --network=none --mount=type=bind,from=uv-cache,target=/uv-cache,rw \
    uv sync --frozen --no-dev --package envcite-consult \
 && .venv/bin/python -m compileall -q services/consult/src libs/python

# 與上面的 uv 映像同一個基底，venv 的直譯器路徑才對得上。
FROM ${DOCKER_HUB}/library/python:${PYTHON_VERSION}-slim-trixie
# Pod 的根檔案系統唯讀，位元組碼在建置時編譯。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
COPY --from=build /app /app
WORKDIR /app/services/consult
# 與部署庫 base/consult 的 runAsUser／runAsGroup 一致。
USER 1000:1000
EXPOSE 50052
CMD ["envcite-consult"]
