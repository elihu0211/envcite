# env-law-consult

諮詢服務：以本體服務供給的法規事實為據，產出可反查引用的答案。租戶隔離。

- 契約：倉庫根 `proto/env_law/consult/v1/consult.proto`（唯一真值）
- 上游：`services/ontology`（法規事實的唯一來源）
- 開發指引：本目錄 `AGENTS.md`；倉庫層規範見根 `AGENTS.md`

```bash
uv sync
uv run python -m pytest
```
