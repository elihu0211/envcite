# env-law-ontology

臺灣環境法規的語料、檢索與推論服務。以 gRPC 供 `services/platform` 與 `services/consult` 呼叫，資料不分租戶。PostgreSQL 是唯一真值。

- 契約：`proto/env_law/ontology/v1/ontology.proto`（法規與檢索）、`reasoning.proto`（標註與推論）
- 開發指引：本目錄 `AGENTS.md`；倉庫規範見根 `AGENTS.md`
- 資料來源：全國法規資料庫（政府資料開放授權條款－第1版，須標示出處）

## 資料流

```
全國法規資料庫 ─ env-law-acquire ─▶ 快照檔
                 env-law-import  ─▶ 語料（corpus）＋ 檢索索引（retrieval）
                                      │
                 env-law-pipeline ◀───┘ 讀條文，由語言模型抽取、閘門驗證
                        │
                        ▼
                 標註（annotation）：只有 confirmed 的陳述進入推論
                        │
         gRPC 伺服器 ◀──┘ 提供 GetLaw／ListArticles／SearchArticles 與 ListStatements／Evaluate
```

- **語料與檢索**：`env-law-sync` 一次做完取得、匯入、檢索投影，排程執行。
- **標註**：`env-law-pipeline` 是語言模型的唯一使用處；推論本身（clingo）不用模型。
- **推論**：只取已確認的標註，結論都是候選，不替呼叫端做最後認定。

## 先決條件

| 項目 | 說明 |
|------|------|
| Python 3.14、[uv](https://docs.astral.sh/uv/) | 版本見根 `.python-version`，由 uv 安裝 |
| PostgreSQL 18 | 連線字串放 `ONTOLOGY_DSN`；建法見根 `CONTRIBUTING.md` |
| Docker | 測試的 Testcontainers 用；已有資料庫則設 `ONTOLOGY_TEST_DSN` |

```bash
uv sync --all-packages        # 整個 workspace；標註管線用 Anthropic 模型時加 --extra llm
```

## 指令

下表每個指令都從本目錄用 `uv run --env-file .env <指令>` 執行。

| 指令 | 做什麼 | 需要的環境變數 |
|------|--------|----------------|
| `env-law-migrate` | 套用 `migrations/*.sql`（已套用的檔案不可修改，要改就新增一個） | `ONTOLOGY_DSN` |
| `env-law-sync moj` | 取得、匯入、檢索投影，三步冪等，排程用 | `ONTOLOGY_DSN`、`ACQUIRE_SNAPSHOT_DIR` |
| `env-law-acquire moj` | 只取得快照 | 同上 |
| `env-law-import moj [--dry-run]` | 快照寫入語料；`--dry-run` 只解析與驗收 | 同上 |
| `env-law-ontology` | gRPC 伺服器，預設 :50051 | `ONTOLOGY_DSN`、`ONTOLOGY_INTERNAL_TOKEN`、選用 `ONTOLOGY_GRPC_PORT` |
| `env-law-pipeline` | 標註：抽取、驗證、接受（見下節） | `ONTOLOGY_DSN`、模型配置 |
| `env-law-annotate <檔>` | 載入一份人寫的標註 JSON | `ONTOLOGY_DSN` |
| `env-law-verify --require <方法,…>` | 只跑確定性閘門的驗證 | `ONTOLOGY_DSN` |

`env-law-sync`／`acquire`／`import` 的結束碼：`0` 成功或無事可做，`1` 暫時性失敗（可重試），`2` 資料不合預期（重試無益）。

### 第一次在本機跑起來

```bash
cp .env.example .env                       # 資料庫連線、快照目錄、內部 token；依需要修改
uv run --env-file .env env-law-migrate
uv run --env-file .env env-law-sync moj
uv run --env-file .env env-law-ontology    # 重建索引後要重啟服務
```

`--env-file` 是 `uv run` 的選項（`.env` 在目前目錄）；環境裡已有的同名變數優先於檔案。

## 標註管線

`env-law-pipeline` 讀現行條文，由語言模型抽取成結構化陳述，再經閘門驗證，通過才升為 `confirmed`。

```bash
uv run --env-file .env env-law-pipeline --law <pcode> --models models.toml          # 指定法規
uv run --env-file .env env-law-pipeline --pending --limit 10 --models models.toml   # 自找待辦，最多 10 部
```

- `--pending` 找「現行文字還沒被此抽取者與版本答過」的法規；答過的單元會留紀錄，重跑會收斂。`--limit` 是每次的模型費用上限。
- 同一時間只跑一個（PostgreSQL 諮詢鎖）；每個陳述驗完就 commit，中斷不丟進度。
- 抽取者不得驗證自己；沒有人審核標註，只有通過閘門的才進推論。

### 模型配置（`models.toml`）

模型與角色全部寫在一個 TOML 檔，由 `--models` 指定，沒有別的設定途徑。範本是 `models.example.toml`；本機的 `models.toml` 已被 `.gitignore` 忽略。

1. `cp models.example.toml models.toml`，改端點網址。
2. 在 `.env` 填入 `CHAT_API_KEY`（見下方「金鑰」）。
3. 確認端點、金鑰與每個模型都通（每個模型問兩個固定問題，不連資料庫）：

   ```bash
   uv run --env-file .env env-law-pipeline --models models.toml --check
   ```

```toml
[endpoint.<名>]   # OpenAI 相容的服務：網址與金鑰的位置
[model.<別名>]    # 用哪個端點、送出什麼名稱、屬於哪個家族
[roles]           # 四個角色各指名模型別名
```

缺角色、不認得的鍵、參照不存在的名稱，載入時一律報錯。

**端點**

| 鍵 | 說明 |
|----|------|
| `base_url` / `base_url_env` | 服務網址（含 `/v1`）/ 存放網址的環境變數名稱；恰好給一個 |
| `api_key_env` / `api_key_file` | 存放金鑰的環境變數名稱 / 金鑰檔路徑（相對於 `models.toml`）；至多一個，都不給表示不需金鑰 |

**模型**

| 鍵 | 說明 |
|----|------|
| `name` | 必填。原樣送給端點的模型識別 |
| `endpoint`、`family` | `provider = "chat"`（預設）時必填；`family` 由人宣告 |
| `provider` | `chat` 或 `anthropic`；`anthropic` 不寫 `endpoint`、`family`，金鑰取自 `ANTHROPIC_API_KEY`，需 `--extra llm` |
| `max_tokens` | 回答長度上限，預設 8192；推理模型的思考計入輸出長度 |
| `max_retries` | 暫時性失敗的重試次數，預設 3，`0` 不重試 |

**角色與獨立性**

| 角色 | 做什麼 |
|------|--------|
| `extractor` | 讀條文，寫出結構化陳述 |
| `roundtrip` | 沒看過原文，只依陳述的確定性描述重抽，比對結構 |
| `independent_test` | 只讀原文設計案例，丟進推論程式比對 |
| `panel`（別名清單） | 評審團，三分之二多數 |

- 驗證者與抽取者同一家族時，該閘門不採計，結果停在 `inconclusive`。
- 家族不從名字推測：託管服務上的模型名稱可能是別名，同一個模型掛在幾個名字下。開跑前管線對所有模型問兩個固定問題，答案逐字相同就拒絕開跑。

### 金鑰

金鑰只放 `.env` 的 `CHAT_API_KEY`（`.env` 已被 `.gitignore` 忽略，不會被提交）；`models.toml` 的端點以 `api_key_env = "CHAT_API_KEY"` 引用，管線只讀這個環境變數。不要把金鑰寫進 `models.toml`、貼進對話或提交。

## 開發

```bash
uv run python -m pytest        # 測試用真實 PostgreSQL，不用替身
uv run ruff check .            # lint；ruff format . 格式化
uv run python -m mypy src tests
../../libs/python/env-law-proto/scripts/generate.sh   # 改了 proto 之後重新產生程式碼
docker build -f Dockerfile ../..                      # 映像；建置內容為倉庫根
```

- 工具設定在根 `pyproject.toml`；CI 依路徑分流強制 lint、型別、測試與 `buf lint`。
- 可觀測性：設了 `OTEL_EXPORTER_OTLP_ENDPOINT` 才匯出追蹤與指標，沒設就什麼都不做。
