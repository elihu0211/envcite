# envcite-grpc

兩個以上服務共用的 gRPC 管線零件。**只放已經有第二個消費端的東西**——判準見倉庫根 [`AGENTS.md`](../../../AGENTS.md) 的目錄配置章節。

| 往上 | 往旁 |
|------|------|
| [倉庫總覽](../../../README.md) · [`libs/python/AGENTS.md`](../AGENTS.md)（本套件的連線選項慣例） | 使用端：[`services/ontology`](../../../services/ontology/README.md)、[`services/consult`](../../../services/consult/README.md)；platform 端的對應設定見 [`services/platform/AGENTS.md`](../../../services/platform/AGENTS.md) 的 Contract |

workspace 成員，不發佈；使用端：

```toml
[tool.uv.sources]
envcite-grpc = { workspace = true }
```
