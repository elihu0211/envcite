# env-law-grpc

兩個以上服務共用的 gRPC 管線零件。**只放已經有第二個消費端的東西**——判準見倉庫根 `AGENTS.md` 的目錄配置章節。

workspace 成員，不發佈；使用端：

```toml
[tool.uv.sources]
env-law-grpc = { workspace = true }
```
