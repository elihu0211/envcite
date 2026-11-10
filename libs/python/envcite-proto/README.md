<!--
type: reference
applies-to: all
owns: [envcite-proto-generated]
-->

# envcite-proto

[`proto/`](../../../proto/README.md) 的 Python 產生碼，提交進版控，`src/**/*_pb2*` 不手改。改了 `proto/` 後重新產生，產生碼與 `proto/` 在同一個 commit（CI 擋不一致）：

```bash
./scripts/generate.sh
```

`uv lock --upgrade` 升級了 `grpcio-tools`、`mypy-protobuf` 或 `protobuf` 時產生碼隨之過期：重新產生並一起提交，`pyproject.toml` 的下界跟著產生碼裡的版本調整。
