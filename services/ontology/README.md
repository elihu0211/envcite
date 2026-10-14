# env-law-ontology

法規範資料、檢索與推論，以 gRPC 供 `services/platform` 呼叫。資料不分租戶。

- 契約：`proto/env_law/ontology/v1/ontology.proto`（法規與檢索）、`reasoning.proto`（標註與推論）。
- 開發指引：本目錄 `AGENTS.md`；倉庫規範見根 `AGENTS.md`。
- 資料來源：全國法規資料庫（政府資料開放授權條款－第1版，需標示出處）。

## 指令

```bash
uv sync --all-packages                                 # 整個 workspace
uv run python -m pytest

ONTOLOGY_INTERNAL_TOKEN=dev uv run env-law-ontology    # gRPC 伺服器 :50051

# 以下需要 ONTOLOGY_DSN=postgresql://… 與 ACQUIRE_SNAPSHOT_DIR=<目錄>
uv run env-law-sync moj                                # 排程的整個週期：取得、匯入、檢索投影
uv run env-law-import moj --dry-run                    # 只解析與驗收；去掉 --dry-run 才寫入語料
uv run env-law-pipeline --pending --limit 10 --models models.toml   # 標註：抽取、驗證、接受
```

## 模型配置

標註管線（`env-law-pipeline`）的模型與角色全部寫在一個 TOML 檔，由 `--models` 指定，沒有別的設定途徑。範本：`models.example.toml`。

```toml
[endpoint.<名>]   # OpenAI 相容的服務：網址與金鑰的位置
[model.<別名>]    # 用哪個端點、送出什麼名稱、屬於哪個家族
[roles]           # 四個角色各指名模型別名
```

缺角色、不認得的鍵、參照不存在的名稱，載入時一律報錯。

### 端點

| 鍵 | 說明 |
|----|------|
| `base_url` / `base_url_env` | 服務網址（含 `/v1`）/ 存放網址的環境變數名稱；恰好給一個 |
| `api_key_file` / `api_key_env` | 金鑰檔路徑（相對於 `models.toml`）/ 存放金鑰的環境變數名稱；至多一個，都不給表示不需金鑰 |

### 模型

| 鍵 | 說明 |
|----|------|
| `name` | 必填。原樣送給端點的模型識別 |
| `endpoint`、`family` | `provider = "chat"`（預設）時必填。`family` 由人宣告 |
| `provider` | `chat` 或 `anthropic`。`anthropic` 不經端點，不可寫 `endpoint`、`family`；金鑰取自 `ANTHROPIC_API_KEY`，需 `uv sync --extra llm` |
| `max_tokens` | 回答長度上限，預設 8192；推理模型的思考計入輸出長度 |
| `max_retries` | 暫時性失敗的重試次數，預設 3，`0` 不重試 |

### 角色與獨立性

| 角色 | 做什麼 |
|------|--------|
| `extractor` | 讀條文，寫出結構化陳述 |
| `roundtrip` | 沒看過原文，只依陳述的確定性描述重抽，比對結構 |
| `independent_test` | 只讀原文設計案例，丟進推論程式比對 |
| `panel`（別名清單） | 評審團，三分之二多數 |

驗證者與抽取者同一家族時，該閘門不採計，結果停在 `inconclusive`。家族不從名字推測：託管服務上的模型名稱可能是別名，同一個模型掛在幾個名字下，名字不同不等於家族不同。開跑前管線對所有模型問兩個固定問題，答案逐字相同就拒絕開跑。

### 金鑰檔

- 內容只有金鑰，前後空白會被去掉。
- 位於 git 工作樹內且未被忽略的金鑰檔會被拒絕載入。本機放 `services/ontology/.secrets/`（已忽略）；`models.toml` 也已忽略。
- 部署時把設定檔與金鑰檔放在同一個目錄，`api_key_file` 寫檔名。
