#!/usr/bin/env bash
# 重新產生倉庫根 proto/ 下每個 .proto 的 Python 程式碼，產出一併提交。
set -euo pipefail
cd "$(dirname "$0")/.."
protos=$(cd ../../../proto && /usr/bin/find envcite -name '*.proto' | sort)
(cd ../../../proto && uv run python -m grpc_tools.protoc -I. \
  --python_out="$OLDPWD/src" --mypy_out="$OLDPWD/src" \
  --grpc_python_out="$OLDPWD/src" --mypy_grpc_out="$OLDPWD/src" $protos)
/usr/bin/find src/envcite -type d -not -name __pycache__ -exec touch {}/__init__.py ';'
