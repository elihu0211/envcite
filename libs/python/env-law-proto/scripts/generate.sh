#!/usr/bin/env bash
# Regenerate the Python code for every .proto under the repo-root proto/. Output is committed.
set -euo pipefail
cd "$(dirname "$0")/.."
protos=$(cd ../../../proto && find env_law -name '*.proto' | sort)
(cd ../../../proto && uv run python -m grpc_tools.protoc -I. \
  --python_out="$OLDPWD/src" --mypy_out="$OLDPWD/src" \
  --grpc_python_out="$OLDPWD/src" --mypy_grpc_out="$OLDPWD/src" $protos)
find src/env_law -type d -not -name __pycache__ -exec touch {}/__init__.py ';'
