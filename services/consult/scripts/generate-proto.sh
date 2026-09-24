#!/usr/bin/env bash
# Regenerate Python gRPC code from the repo-root proto/ (single source of truth). Output is committed.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m grpc_tools.protoc -I../../proto \
  --python_out=src --mypy_out=src \
  --grpc_python_out=src --mypy_grpc_out=src \
  ../../proto/env_law/consult/v1/consult.proto \
  ../../proto/env_law/ontology/v1/ontology.proto
touch src/env_law/__init__.py src/env_law/consult/v1/__init__.py \
  src/env_law/ontology/__init__.py src/env_law/ontology/v1/__init__.py
