---
type: reference
applies-to: all
owns: [ontology-entrypoints, sync-exit-codes, models-config]
---

# envcite-ontology

環境知識本體的集中處：各來源的知識各成一個本體（如法規本體 `law`），在這裡提供語料、檢索與推論。回[倉庫總覽](../../README.md)。

- 介面：gRPC `OntologyService`（法規與檢索）、`ReasoningService`（標註與推論）；契約 [`ontology.proto`](../../proto/envcite/ontology/v1/ontology.proto)、[`reasoning.proto`](../../proto/envcite/ontology/v1/reasoning.proto)；約定與錯誤原因見 [`proto/README.md`](../../proto/README.md)。
- 呼叫端 [`platform`](../platform/README.md)、[`consult`](../consult/README.md)。資料全域共用，不分租戶；PostgreSQL 為唯一真值。
- 語料與檢索由 `envcite-sync` 依序取得、匯入、投影；標註由 `envcite-pipeline` 以語言模型抽取並經閘門驗證；推論（clingo）不用模型，只取已確認的標註，結論都是候選。
- 共用套件：[`envcite-segmenter`](../../libs/python/envcite-segmenter/README.md)、[`envcite-grpc`](../../libs/python/envcite-grpc/README.md)、[`envcite-proto`](../../libs/python/envcite-proto/README.md)。

## 入口

所有入口都在同一個映像裡：預設 command 是伺服器，其餘以 command 覆寫。入口名稱是與部署庫的契約，定義在 `pyproject.toml` 的 `[project.scripts]`。

| 入口 | 型態 | 做什麼 |
|------|------|--------|
| `envcite-ontology` | 長駐 | gRPC 伺服器 |
| `envcite-migrate` | 一次性 | 套用 `migrations/*.sql` |
| `envcite-sync <來源>` | 排程 | 取得、匯入、檢索投影，三步冪等 |
| `envcite-acquire <來源>` | 一次性 | 只取得快照 |
| `envcite-import <來源>` | 一次性 | 快照寫入語料；`--dry-run` 只解析與驗收 |
| `envcite-pipeline` | 排程 | 標註：抽取、驗證、接受 |
| `envcite-annotate <檔>` | 一次性 | 載入一份人寫的標註 JSON |
| `envcite-guidance <檔>` | 一次性 | 載入行政規則（函釋、裁量基準）JSON；格式見 [`guidance.py`](src/envcite_ontology/guidance.py) |
| `envcite-verify` | 一次性 | 只跑確定性閘門的驗證；`--require <方法,…>` |

`<來源>` 登記在 [`acquire/cli.py`](src/envcite_ontology/acquire/cli.py) 的 `SOURCES`；`moj` 是全國法規資料庫。開發用工具在 [`tools/`](tools)，不進映像，用法見各檔開頭。

**環境變數**：以 [`settings.py`](src/envcite_ontology/settings.py) 為準，範本是 `.env.example`。

`envcite-sync`、`acquire`、`import` 的結束碼：`0` 成功或無事可做，`1` 暫時性失敗（可重試），`2` 資料不合預期（重試無益）。

## 標註管線

```bash
uv run --env-file .env envcite-pipeline --law <pcode> --models models.toml          # 指定法規
uv run --env-file .env envcite-pipeline --pending --limit <N> --models models.toml   # 自找待辦，最多 N 部
```

`--limit` 是每次的模型費用上限。驗證與推論的規範見 [`AGENTS.md`](AGENTS.md)；推論結果的呼叫端用法見 [`proto/README.md`](../../proto/README.md) 的〈標註與推論〉。

### 模型配置（`models.toml`）

模型、角色與檢索用的嵌入模型全部寫在一個 TOML 檔，沒有別的設定途徑：標註管線由 `--models` 指定，伺服器與同步由 `ONTOLOGY_MODELS` 指向它。鍵與角色見範本 [`models.example.toml`](models.example.toml)；`models.toml` 不進版控。

```bash
cp models.example.toml models.toml   # 改端點網址與要用的模型
cp .env.example .env                 # 用編輯器填入 CHAT_API_KEY，不要貼到對話或提交
uv run --env-file .env envcite-pipeline --models models.toml --check   # 對每個模型送探測問題，不連資料庫
```

金鑰只放 `.env`，`models.toml` 以 `api_key_env = "CHAT_API_KEY"` 引用。`[embedding]` 只需要它指名的端點備妥金鑰，伺服器不因標註管線的端點缺金鑰而起不來。
