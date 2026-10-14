# envcite-proto

倉庫根 `proto/` 的 Python 產生碼（`envcite.ontology.v1`、`envcite.consult.v2`），由各服務共用。回[倉庫總覽](../../../README.md)。產生碼提交進版控，不手改；改完 `proto/` 後重新產生：

```bash
./scripts/generate.sh
```

`uv lock --upgrade` 若升級了 `grpcio-tools`、`mypy-protobuf` 或 `protobuf`，產生碼隨之過期（CI 的 `python-proto` 會擋下）：重新產生並一起提交，下界（`pyproject.toml`）跟著產生碼裡的版本調整。
