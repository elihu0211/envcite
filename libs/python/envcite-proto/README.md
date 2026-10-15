# envcite-proto

倉庫根 [`proto/`](../../../proto/README.md) 的 Python 產生碼，由各服務共用。產生碼提交進版控，不手改。

| 往上 | 往旁 |
|------|------|
| [倉庫總覽](../../../README.md) · [`libs/python/AGENTS.md`](../AGENTS.md)（產生碼規則） | 契約來源：[`proto/README.md`](../../../proto/README.md)；使用端：[`services/ontology`](../../../services/ontology/README.md)、[`services/consult`](../../../services/consult/README.md) |

改完 `proto/` 後重新產生：

```bash
./scripts/generate.sh
```

`uv lock --upgrade` 若升級了 `grpcio-tools`、`mypy-protobuf` 或 `protobuf`，產生碼隨之過期（CI 會擋下）：重新產生並一起提交，下界（`pyproject.toml`）跟著產生碼裡的版本調整。
