# 參與開發

## 快速入門

```bash
docker compose up -d              # 開發用 PostgreSQL（compose.yaml；各分支共用）
wt switch --create feat/<簡述>    # 開分支：建 worktree 並裝好依賴

wt ontology                       # 以下各開一個終端機；自動建本分支的資料庫並遷移
wt consult
wt platform
wt list                           # URL 欄＝本分支 platform 的位址

wt remove feat/<簡述>             # 合併後收尾：刪 worktree、分支與本分支的資料庫
```

## 連線資訊

### PostgreSQL

帳號 `postgres`、密碼 `postgres`，位址 `localhost:5432`。

| 資料庫 | 建立與刪除 |
|--------|------------|
| `ontology_<分支代號>` | `wt ontology` 時建立；`wt remove` 時刪除 |
| `platform_<分支代號>` | `wt platform` 時建立；`wt remove` 時刪除 |
| `envlaw_platform` | 不經 `wt`、直接 `dotnet run` platform 時建立 |

**分支代號**由分支名推導（`.config/wt.toml` 的 `sanitize_db`）：轉小寫、非英數字轉底線，再加 3 碼雜湊。例如 `main` → `main_vfz`，`feat/import-pipeline` → `feat_import_pipeline_wq5`。`main` 分支的 ontology 資料庫：

```text
postgresql://postgres:postgres@localhost:5432/ontology_main_vfz
```

```bash
wt step eval '{{ branch | sanitize_db }}'                          # 在該分支的 worktree 內執行，印出分支代號
docker compose exec postgres psql -U postgres -l                   # 列出所有資料庫
docker compose exec postgres psql -U postgres -d ontology_main_vfz # 進入某個資料庫
```

### 其他

| 項目 | 值 |
|------|----|
| 服務之間的內部 token | `dev` |
| platform 營運者 key（`X-Admin-Key`） | 未設定（`/admin` 停用）；要用就設環境變數 `Platform__AdminKeySha256`＝key 的 SHA-256 hex |

## 常用指令

從**該服務目錄**執行；完整清單與環境變數見各服務的 `AGENTS.md`。Python 各服務與 `libs/python/` 是同一個 uv workspace：依賴在倉庫根一次裝好（`uv sync --all-packages`，`wt` 建樹時已執行）。

| | `services/platform/` | `services/ontology/`、`services/consult/` |
|---|---|---|
| 測試 | `dotnet test` | `uv run python -m pytest` |
| Lint | `dotnet format --verify-no-changes` | `uv run ruff check .` |
| 格式化 | `dotnet format` | `uv run ruff format .` |
| 型別檢查 | `dotnet build`（警告視為錯誤） | `uv run python -m mypy src tests` |
| 改 proto 後重新產生 | 建置時自動產生 | `libs/python/env-law-proto/scripts/generate.sh` |

測試以 Testcontainers 自起資料庫，不用開發資料庫。

契約，從倉庫根執行：

```bash
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 breaking --against '.git#branch=main'
```

## 升級版本

版本以手動升級。依賴集中在單一檔案；工具與映像的版本若出現在多處，表中各位置在同一次提交一起改。

| 版本 | 位置 |
|------|------|
| Python 套件 | 倉庫根 `uv lock --upgrade`（或 `--upgrade-package <pkg>`）；升到 `grpcio-tools`、`mypy-protobuf`、`protobuf` 時重新產生 `libs/python/env-law-proto` |
| .NET 套件 | `services/platform/Directory.Packages.props` |
| .NET SDK 與映像 | `services/platform/global.json`、`services/platform/Dockerfile`、`.gitlab-ci.yml` 的 `dotnet` job |
| Python | `.python-version`、`.gitlab-ci.yml` 的 `PYTHON_VERSION`、`services/{ontology,consult}/Dockerfile` 的 `ARG PYTHON_VERSION` |
| uv | `.gitlab-ci.yml` 的 `UV_VERSION`、`services/{ontology,consult}/Dockerfile` 的 `ARG UV_VERSION`、各 `pyproject.toml` 的 `uv_build` 範圍 |
| PostgreSQL | `compose.yaml`、`.gitlab-ci.yml` 的 `ontology` job、`services/ontology/tests/conftest.py`、`services/platform/tests/…/PlatformFactory.cs`；主版本與 env-law-deploy 的 `Cluster` 相同 |
| buf | `.gitlab-ci.yml` 的 `proto` job、本檔與 `AGENTS.md` 的指令 |
| oasdiff | `.gitlab-ci.yml` 的 `openapi` job |
| Docker（映像建置） | `.gitlab-ci.yml` 的 `.image` |

## 從開發到上線

1. **開分支**：`wt switch --create <type>/<簡述>`，type 用 `feat`、`fix`、`docs`、`refactor`、`chore` 等。
2. **Commit**：[Conventional Commits](https://www.conventionalcommits.org)，`type(scope): 說明`；破壞性變更加 `!`（如 `feat(consult)!: …`）。
3. **開 MR 到 `main`**：`main` 受保護，不直接推送。**pipeline 成功才能合併**，以 squash 合併，合併後刪除來源分支。
4. **上線**：合併後 CI 把受影響服務的映像推送為 `:<commit-sha>`，之後自動部署。
5. **收尾**：`wt remove <分支>`。

- **改契約**：兩側的產生碼在同一個 MR 更新；破壞性變更開新版本（`v2`），不改 `v1`。
- **回退**：revert 有問題的 commit，照常合併上線。

CI 依改動路徑分流：

| 改動 | 跑什麼 |
|------|--------|
| 任何改動 | `gate`（讓只改文件的 MR 也有 pipeline 可以合併） |
| `services/platform/` | `dotnet`（含對外 REST 規格是否過期）、`openapi`（對外 REST 的破壞性變更）、`platform-image` |
| `services/ontology/`、`libs/python/` | `ontology`、`ontology-image` |
| `services/consult/`、`libs/python/` | `consult`、`consult-image` |
| `libs/python/env-law-segmenter/` | `segmenter` |
| `libs/python/env-law-proto/` | `python-proto`（產生碼是否與 `proto/` 一致） |
| 倉庫根 `pyproject.toml`、`uv.lock` | 所有 Python job 與其映像 |
| `proto/` | `proto`（`buf lint`、`buf breaking`），以及所有消費它的服務 |

非 `main` 分支的映像 job 只驗證能建置，不推送。

## 環境說明

- **開發用 PostgreSQL**（`compose.yaml`）：與 ontology 測試同一個映像（綱要只用 PostgreSQL 自帶的 `btree_gist`）；只綁 `127.0.0.1`。資料在 volume `env-law_pgdata`，`docker compose down` 不刪資料，從零開始用 `docker compose down -v`。
- **每條分支一棵 worktree**（`.config/wt.toml`）：port 與資料庫名由分支推導，平行的分支不相撞。

## 準備工具

| 工具 | 版本 | 用途 |
|------|------|------|
| Git | — | |
| Docker 與 Docker Compose | — | 開發用 PostgreSQL、測試、映像建置 |
| .NET SDK | 10.0（`services/platform/global.json`） | platform |
| [uv](https://docs.astral.sh/uv/) | CI 用 0.12.18 | Python workspace；Python 版本見倉庫根 `.python-version`，由 uv 自動安裝 |
| [Worktrunk](https://worktrunk.dev)（`wt`） | — | 分支與本機環境 |
| Node.js | — | 以 `npx` 執行 buf |
