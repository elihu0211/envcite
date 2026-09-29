# env-law-ontology

環境法規本體論服務：ELI 模型的法規範資料與檢索，以 gRPC 供 `services/platform` 呼叫。資料不分租戶。

- 契約：倉庫根 `proto/env_law/ontology/v1/ontology.proto`（唯一真值）
- 開發指引：本目錄 `AGENTS.md`；倉庫層規範見根 `AGENTS.md`
- 資料來源：全國法規資料庫（政府資料開放授權條款－第1版，需標示出處）

```bash
uv sync --all-packages   # 整個 workspace（倉庫根的 uv.lock）
uv run python -m pytest
ONTOLOGY_INTERNAL_TOKEN=dev uv run env-law-ontology   # :50051
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-acquire moj   # 取得全國法規資料庫快照
```
