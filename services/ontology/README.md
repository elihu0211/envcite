# env-law-ontology

環境法規本體論服務：法規範資料與檢索，以 gRPC 供 `services/platform` 呼叫。資料不分租戶。

- 契約：倉庫根 `proto/env_law/ontology/v1/ontology.proto`（法規與檢索）與 `reasoning.proto`（標註與推論）（唯一真值）
- 開發指引：本目錄 `AGENTS.md`；倉庫層規範見根 `AGENTS.md`
- 資料來源：全國法規資料庫（政府資料開放授權條款－第1版，需標示出處）

```bash
uv sync --all-packages   # 整個 workspace（倉庫根的 uv.lock）
uv run python -m pytest
ONTOLOGY_INTERNAL_TOKEN=dev uv run env-law-ontology   # :50051
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-acquire moj   # 取得全國法規資料庫快照
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-import moj --dry-run   # 解析與驗收；去掉 --dry-run 即寫入語料
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-sync moj   # 排程執行的整個週期：取得、匯入、檢索投影
```
