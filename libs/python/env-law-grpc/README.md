# env-law-grpc

兩個以上服務共用的 gRPC 管線零件。**只放已經有第二個消費端的東西**——判準見倉庫根 `AGENTS.md` 的目錄配置章節。

以路徑相依引入，不發佈：

```toml
[tool.uv.sources]
env-law-grpc = { path = "../../libs/python/env-law-grpc" }
```
