# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `services/ontology/`。

## Project

- **Name:** env-law-ontology (Python package `env_law_ontology`)
- **Role:** 環境法規本體論服務（shared, non-tenant data）；由同倉的 `services/platform`與 `services/consult` 呼叫
- **Stack:** Python 3.14 · uv · gRPC（grpcio，同步伺服器）· PostgreSQL 18（psycopg 3）· ruff · mypy (strict) · pytest
- **Data model:** [ELI ontology](http://data.europa.eu/eli/ontology)，以**關聯式實表**承載，schema `corpus`（`legal_resource`、`legal_expression`、`provision`／`provision_version`、`lifecycle_event`）、`retrieval`（`lexical_index`：內容定址、逐段落）、`vocab`（受控詞彙）與 `annotation`（規範陳述、要件、但書、優先、罰則；錨定（單元，內容雜湊），不錨定版本列）。法規以（識別碼方案，在地識別碼）為鍵，全國法規資料庫為 pcode；效力逐條文單元記載，查詢逐單元解析（`corpus.provisions_at`）。標題掛在 `legal_expression`——`eli:title` 的定義域是表達層。綱要設計見 `env-law-docs` 的 02-04；其餘表隨其管線加入。RDF 是由實表投影的導出視圖，方向不可顛倒；論據見 `env-law-docs` 的 01-03 與 02-02。
- **Status:** gRPC `OntologyService`（GetLaw、ListArticles、SearchArticles）讀 PostgreSQL；SearchArticles 為字面檢索（分詞由本服務以 `env-law-segmenter` 做，詞典於啟動時自資料庫讀取）；資料庫預設是空的；全國法規資料庫的現行版本由 `env-law-sync moj`（或分步的 `env-law-acquire`、`env-law-import`）寫入，並補齊檢索投影 `retrieval.lexical_index`（以段落文字的雜湊為鍵，不指向版本列，所以更正後無從陳舊；缺列由 `retrieval.missing_lexical` 列出）；規範標註由抽取器（語言模型）寫成 JSON，經 `env-law-annotate <檔>` 載入（`src/env_law_ontology/annotate.py`，格式見其模組說明），推論只取已確認或規則推導的陳述（`annotation.statements_at`），委任要件的門檻值取授權命令當日有效的限值（`annotation.threshold_at`）；服務端尚未對外提供標註

## Commands

Use `uv` only (no pip/venv/poetry). Run from **this directory** (`services/ontology/`).

```bash
uv sync --all-packages       # install the whole workspace (.venv and uv.lock live at the repo root)
uv add --raw <pkg>           # runtime dep, name only — the version lives in uv.lock
uv run python -m pytest       # test
uv run ruff check .           # lint (add --fix to autofix)
uv run ruff format .          # format
uv run python -m mypy src tests   # typecheck (strict)
../../libs/python/env-law-proto/scripts/generate.sh   # regenerate gRPC code after editing protos
ONTOLOGY_TEST_DSN=postgresql://... uv run python -m pytest   # use an existing database
ONTOLOGY_INTERNAL_TOKEN=dev ONTOLOGY_DSN=postgresql://... uv run env-law-ontology   # run server (:50051)
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-acquire moj   # acquire one source (CronJob entrypoint)
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-import moj [--dry-run]   # snapshot → corpus; --dry-run parses and gates only
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-sync moj   # the scheduled cycle: acquire → import → projection (CronJob entrypoint)
docker build -f Dockerfile ../..   # image (context = repo root; CI job `ontology-image` pushes :<sha> on main)
```

- Proto lint／`buf breaking` 從倉庫根執行，見根 `AGENTS.md`。
- Tool config (ruff, mypy, pytest) lives in the repo-root `pyproject.toml`. Code in `src/env_law_ontology/`, tests in `tests/`; generated gRPC code is imported from `env-law-proto` (`env_law.ontology.v1`).
- Server env：`ONTOLOGY_INTERNAL_TOKEN`（必填；呼叫端送 `authorization: Bearer <token>`）、`ONTOLOGY_DSN`（必填，PostgreSQL 連線字串）、`ONTOLOGY_GRPC_PORT`（50051）。
- 取得（`env-law-acquire <來源>`，`src/env_law_ontology/acquire/`）：引擎負責下載、SHA-256、冪等判斷、以雜湊為名存檔與寫入 `source.*`；來源只實作 `Source`（`feeds`、`tls()`、`read()`），在 `cli.SOURCES` 登記。環境變數 `ONTOLOGY_DSN`（可寫端點）、`ACQUIRE_SNAPSHOT_DIR`。結束碼 0 成功或未變、1 暫時性失敗（可重試）、2 資料不合預期（重試無益）。
- 匯入（`env-law-import <來源> [--dry-run]`，`src/env_law_ontology/ingest/`）：每個來源取最新快照，`law.parse` 為純函式（條文切分 `articles`、沿革 `history`、生效日 `commencement`）；整批比例過 `audit.LIMITS` 才寫入，否則結束碼 2。單一法規的問題記入 `source.import_problem`，error 使該部不寫入。**改變解析輸出的修改必須遞增 `law.PARSER_VERSION`**，已匯入的法規才會以新版重寫；已匯入的法規內容改變是增量（`ingest/write.py` 的 `apply`）：新增版本、更正、更新推導、拒絕（`version_regressed`）；更正與推導修正以新增列取代舊列（`superseded_at`），查詢只看現行列；下層單元的文字改變時，所屬條的列也換新（原為舊索引以條的列為鍵而設；索引現已內容定址，不再依賴，行為暫時保留）。匯入成功後 `projection.rebuild` 補齊索引：分詞器識別碼（含標題詞典雜湊）不符的列與被取代版本的列先刪，再補缺的條；服務端的詞典啟動時讀一次，重建之後要重啟服務。
- 標註（`env-law-annotate <檔>`，環境變數 `ONTOLOGY_DSN`）：每個陳述附它所讀的條文原文，載入時算雜湊並要求該單元現行的某個版本列確有這個雜湊，否則整份拒絕（結束碼 2）；狀態取文件的 `review`，預設 `extracted`，載入不接受任何陳述；不確定法律概念或裁量的要件標 `judgement: true`（系統不從事實判定它，限值不得標）；一個罰則陳述一個效果：改善命令等非處罰的行政命令用效果 `order`，按次處罰用 `repeating: true`；一次載入是一個交易，不冪等（要修正，把舊陳述改為 `rejected` 再載入）。
- 推論（`src/env_law_ontology/reasoning/`，`evaluate(conn, as_of, case)`）：通用推論程式 `rules.lp`（clingo）不含任何法條；`facts.py` 把某日有效、已確認（或規則推導）的陳述經 `annotation.statement_versions_at` 與 `annotation.threshold_source_at` 解析後寫成 ASP 事實，案件事實由呼叫端給。結論都是候選：違反、合規、附條件、被排除、不適用、無法判定；罰則只列候選，罰則自己的要件也涵攝；缺的事實、需判斷的要件、矛盾的事實、未決的衝突與罰則競合分開回報。**改 `rules.lp` 要跑 `tests/test_reasoning.py`（真實 schema、不用替身）。** 目前只是程式庫，沒有 gRPC 方法對外提供；沒有人審過的標註不產生任何結論。
- 驗證（`src/env_law_ontology/verification/`，`env-law-verify --require <方法,…>`）：**沒有人審核標註**。抽取器（`extract.py`，模型是一個 `Model` 介面）寫入的陳述是 `extracted`，由閘門驗證、依政策接受：閘門是 `Gate`（`numbers`：額度與帶單位的門檻值對原文；`references`：罰則所處罰的陳述是原文引用的那一條；`executable`：推論程式可評估且結論如要件所預期地翻轉），每次結果寫一列 `annotation.verification`；`annotation.settle(必要方法)` 在必要方法最近一次都 pass 或 not_applicable、至少一個 pass、且沒有任何 fail 時升為 `confirmed`，有 fail 則 `rejected`，每次升降也記一列。抽取者不得驗證自己的陳述（資料庫強制）。**確定性閘門不證明語意忠於原文**；模型閘門在 `model_gates.py`：`roundtrip`（標註 → 確定性描述 → 另一家族的模型重抽 → 比對結構）、`independent_test`（另一個模型只讀原文產生案例，丟進推論程式比對）、`panel`（不同家族的評審團取三分之二多數）；驗證者不得是抽取者，**預設也不得與抽取者同一家族**，合格者不足則 `inconclusive`，所以 `env-law-verify`（只跑確定性閘門）用預設政策不接受任何陳述，只用確定性閘門接受要明說 `--require numbers,references,executable`。`calibration.ppi_mean` 以外部既有的標準答案估計準確度，不另請人標。整條線（抽取 → 驗證 → 接受）是 `env-law-pipeline --law <pcode> --extractor anthropic:<模型> --verifier <供應商:模型>…`（`models.py`：`Model` 介面有名字與家族；兩個轉接器：`anthropic:<模型>`（需要 `uv sync --extra llm` 與 `ANTHROPIC_API_KEY`），與 `chat:<供應商/模型>`（OpenAI 相容的 Chat Completions，只用標準函式庫；端點取自 `CHAT_BASE_URL`，必填、沒有預設，金鑰取自 `CHAT_API_KEY`；斜線前的供應商就是家族，例如 `chat:openai/gpt-oss-120b`、`chat:qwen/…`，用來湊出不同家族的驗證者）。測試全用有名字與家族的替身，**沒有真的呼叫過任何模型**。
- 週期（`env-law-sync <來源>`，`src/env_law_ontology/sync.py`）：取得、匯入、檢索投影依序執行，每步冪等，每個週期都全跑。結束碼：0 成功（含無事可做）；1 暫時性（連線、資料庫、下載到的不是 zip）；2 重試無益（`InvalidSnapshot`：資料形狀不合、快照檔不存在；`BatchRejected`：整批比例越線）。只為本機開發：沒有寫入者互斥、執行紀錄與觀測信號，投影重建期間與識別碼改變後、重啟服務前，字面檢索查不到東西。設計見 env-law-docs 03-04。
- 測試需要**真實的 PostgreSQL**：設 `ONTOLOGY_TEST_DSN` 指向現成的資料庫，或留空讓 testcontainers 起一個（需要 Docker daemon）。不用替身，因為本服務一半的讀取邏輯在 SQL 裡（逐單元解析、時態鍵、整數陣列排序），對替身測試等於不測那一半。
- 綱要由 `migrations/*.sql` 描述，依檔名順序套用。**已套用的檔案不可修改**——要改就新增一個檔，這條由 `schema_migration` 的雜湊比對強制，不靠記得。

## Contract

- 本服務契約是倉庫根的 `proto/env_law/ontology/v1/ontology.proto`（單一真值與 breaking 規則見根 `AGENTS.md`）。
- Data is not tenant-scoped; `x-tenant-id` / `x-request-id` metadata are for logging/audit only.

## Safety

- Do not hand-edit generated paths: the repo-root `uv.lock` (change via `uv add/remove/lock`), `.venv/`, `libs/python/env-law-proto/src/**/*_pb2*` (run its `scripts/generate.sh`).
- All data from 全國法規資料庫 must cite it (政府資料開放授權條款－第1版, attribution required). Test data (`tests/sample.py`) excerpts it (2026-09-18) and is imported through the real importer.

## Done when

- ruff, mypy, pytest and `buf lint` pass — enforced by `.gitlab-ci.yml` (`ontology` job; `python-proto` fails if generated gRPC code is stale; `proto` job runs `buf breaking` against the MR target branch).

