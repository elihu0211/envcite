# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `services/ontology/`。

## Project

- **Name:** env-law-ontology (Python package `env_law_ontology`)
- **Role:** 環境法規本體論服務（shared, non-tenant data）；由同倉的 `services/platform`（C# SaaS：租戶、計費、API 金鑰）與 `services/consult` 呼叫
- **Stack:** Python 3.14 · uv · gRPC（grpcio，同步伺服器）· PostgreSQL 18（psycopg 3）· ruff · mypy (strict) · pytest
- **Data model:** [ELI ontology](http://data.europa.eu/eli/ontology)，以**關聯式實表**承載，schema `corpus`（`legal_resource`、`legal_expression`、`provision`／`provision_version`、`lifecycle_event`）與 `retrieval`（`article_index`）。法規以（識別碼方案，在地識別碼）為鍵，全國法規資料庫為 pcode；效力逐條文單元記載，查詢逐單元解析（`corpus.provisions_at`）。標題掛在 `legal_expression`——`eli:title` 的定義域是表達層。綱要設計見 `env-law-docs` 的 02-04；其餘表隨其管線加入。RDF 是由實表投影的導出視圖，方向不可顛倒；論據見 `env-law-docs` 的 01-03 與 02-02。
- **Status:** gRPC `OntologyService`（GetLaw、ListArticles、SearchArticles）讀 PostgreSQL；SearchArticles 為字面檢索（分詞由本服務以 `env-law-segmenter` 做，詞典於啟動時自資料庫讀取）；資料庫預設只有**樣本**（`migrations/0003_sample_fixture.sql`）；全國法規資料庫的現行版本由 `env-law-import` 寫入，尚未排程，檢索投影（`retrieval.article_index`）尚無建置管線

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
docker build -f Dockerfile ../..   # image (context = repo root; CI job `ontology-image` pushes :<sha> on main)
```

- Proto lint／`buf breaking` 從倉庫根執行，見根 `AGENTS.md`。
- Tool config (ruff, mypy, pytest) lives in the repo-root `pyproject.toml`. Code in `src/env_law_ontology/`, tests in `tests/`; generated gRPC code is imported from `env-law-proto` (`env_law.ontology.v1`).
- Server env：`ONTOLOGY_INTERNAL_TOKEN`（必填；呼叫端送 `authorization: Bearer <token>`）、`ONTOLOGY_DSN`（必填，PostgreSQL 連線字串）、`ONTOLOGY_GRPC_PORT`（50051）。
- 取得（`env-law-acquire <來源>`，`src/env_law_ontology/acquire/`）：引擎負責下載、SHA-256、冪等判斷、以雜湊為名存檔與寫入 `source.*`；來源只實作 `Source`（`feeds`、`tls()`、`read()`），在 `cli.SOURCES` 登記。環境變數 `ONTOLOGY_DSN`（可寫端點）、`ACQUIRE_SNAPSHOT_DIR`。結束碼 0 成功或未變、1 暫時性失敗（可重試）、2 資料不合預期（重試無益）。
- 匯入（`env-law-import <來源> [--dry-run]`，`src/env_law_ontology/ingest/`）：每個來源取最新快照，`law.parse` 為純函式（條文切分 `articles`、沿革 `history`、生效日 `commencement`）；整批比例過 `audit.LIMITS` 才寫入，否則結束碼 2。單一法規的問題記入 `source.import_problem`，error 使該部不寫入。**改變解析輸出的修改必須遞增 `law.PARSER_VERSION`**，已匯入的法規才會以新版重寫；已匯入的法規內容改變（增量）尚未實作，只記 `changed_after_import`。
- 測試需要**真實的 PostgreSQL**：設 `ONTOLOGY_TEST_DSN` 指向現成的資料庫，或留空讓 testcontainers 起一個（需要 Docker daemon）。不用替身，因為本服務一半的讀取邏輯在 SQL 裡（逐單元解析、時態鍵、整數陣列排序），對替身測試等於不測那一半。
- 綱要由 `migrations/*.sql` 描述，依檔名順序套用。**已套用的檔案不可修改**——要改就新增一個檔，這條由 `schema_migration` 的雜湊比對強制，不靠記得。

## Contract

- 本服務契約是倉庫根的 `proto/env_law/ontology/v1/ontology.proto`（單一真值與 breaking 規則見根 `AGENTS.md`）。
- Data is not tenant-scoped; `x-tenant-id` / `x-request-id` metadata are for logging/audit only.

## Safety

- Do not hand-edit generated paths: the repo-root `uv.lock` (change via `uv add/remove/lock`), `.venv/`, `libs/python/env-law-proto/src/**/*_pb2*` (run its `scripts/generate.sh`).
- Sample data (`migrations/0003_sample_fixture.sql`) excerpts 全國法規資料庫 (2026-09-18) and is not the corpus; all data from that source must cite it (政府資料開放授權條款－第1版, attribution required).

## Done when

- ruff, mypy, pytest and `buf lint` pass — enforced by `.gitlab-ci.yml` (`ontology` job; `python-proto` fails if generated gRPC code is stale; `proto` job runs `buf breaking` against the MR target branch).

