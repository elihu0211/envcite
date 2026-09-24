# env-law-consult

接地服務：以本體服務供給的法規事實，為呼叫端的語言模型備妥可引用的封閉證據集（`L1`、`L2`…，附條文），或拒答。本服務不執行生成模型。租戶隔離。

- 契約：倉庫根 `proto/env_law/consult/v2/consult.proto`（唯一真值）
- API 文件與呼叫端接法：倉庫根 `proto/README.md`
- 上游：`services/ontology`（法規事實的唯一來源）
- 開發指引：本目錄 `AGENTS.md`；倉庫層規範見根 `AGENTS.md`

```bash
uv sync
uv run python -m pytest
```
