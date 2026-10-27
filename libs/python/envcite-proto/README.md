# envcite-proto

[`proto/`](../../../proto/README.md) 的 Python 產生碼，提交進版控，不手改。改完 `proto/` 後重新產生：

```bash
./scripts/generate.sh
```

產生碼過期與 `uv lock --upgrade` 的處理見 [`libs/python/AGENTS.md`](../AGENTS.md)；若升級了 `grpcio-tools`、`mypy-protobuf` 或 `protobuf`，`pyproject.toml` 的下界跟著產生碼裡的版本調整。
