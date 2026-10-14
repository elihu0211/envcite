# 參與開發

本機環境、分支與上線流程。回[倉庫總覽](README.md)；agent 規範見 [`AGENTS.md`](AGENTS.md)。

## 先決條件

| 工具 | 版本 | 用途 |
|------|------|------|
| Git | — | |
| [Worktrunk](https://worktrunk.dev)（`wt`） | — | 分支與本機環境 |
| Docker | — | 測試（Testcontainers）、映像建置；可用來起開發用資料庫 |
| PostgreSQL | 18 | 開發用資料庫 |
| .NET SDK | 10.0（`services/platform/global.json`） | platform |
| [uv](https://docs.astral.sh/uv/) | 版本見 `.gitlab-ci.yml` 的 `UV_VERSION` | Python workspace；Python 版本見 `.python-version`，由 uv 自動安裝 |
| Node.js | — | 以 `npx` 執行 buf |

## 快速入門

```mermaid
%%{init: {"flowchart": {"curve": "basis"}}}%%
flowchart LR
    db["① 備妥資料庫<br/>PostgreSQL 18"] --> br["② wt switch --create<br/>建樹 · 裝依賴"]
    br --> svc["③ wt ontology · consult · platform<br/>各一個終端機"]
    svc --> imp["④ envcite-sync moj<br/>首次匯入法規"]
    imp --> restart["重啟 wt ontology"]
    restart --> list["⑤ wt list<br/>取得 platform URL"]
    list --> done(["合併後 wt remove"])

    classDef step fill:#eef2ff,stroke:#6366f1,color:#1e1b4b
    classDef end_ fill:#ecfdf5,stroke:#10b981,color:#064e3b
    class db,br,svc,imp,restart,list step
    class done end_
```

1. 備妥[開發用資料庫](#開發用資料庫)。
2. 開分支：`wt switch --create feat/<簡述>`（建 worktree 並裝好依賴）。
3. 各開一個終端機啟動服務：

   ```bash
   wt ontology    # 先遷移，再啟動 gRPC 伺服器
   wt consult
   wt platform
   ```

4. 匯入法規資料（首次；ontology 的庫預設是空的）：

   ```bash
   cd services/ontology
   ONTOLOGY_DSN=postgresql://envcite:envcite@localhost:5432/envcite \
   ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots \
   uv run envcite-sync moj
   ```

   完成後重啟 `wt ontology`（詞典只在啟動時讀取）。
5. `wt list` 的 URL 欄是本分支 platform 的位址；登入見[第一組資料](#第一組資料platform)。

合併後以 `wt remove feat/<簡述>` 刪除 worktree 與分支。

## 開發用資料庫

需要 PostgreSQL 18，內含兩個資料庫：`envcite`（ontology）、`platform`（platform）。連線的帳號要能在 `envcite` 建立 `btree_gist` 擴充。

用 Docker 起一個拋棄式的庫（符合預設值）：

```bash
docker run -d --name envcite-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=envcite -e POSTGRES_PASSWORD=envcite -e POSTGRES_DB=envcite postgres:18
docker exec envcite-pg createdb -U envcite platform
```

連線由環境變數指定，未設定時 `wt` 別名與 `appsettings.Development.json` 用預設值：

| 服務 | 環境變數 | 預設值 |
|------|----------|--------|
| ontology | `ONTOLOGY_DSN` | `postgresql://envcite:envcite@localhost:5432/envcite` |
| platform | `ConnectionStrings__Platform` | `Host=localhost;Port=5432;Database=platform;Username=envcite;Password=envcite` |

各分支共用這兩個庫：同一時間只跑一條分支的服務，否則兩邊的遷移會互相覆寫。測試不使用它們——Testcontainers 每次自起資料庫。

ontology 遷移檔改過（已套用的檔案內容改變）時，清掉綱要後重新遷移並重新匯入資料：

```bash
psql "${ONTOLOGY_DSN:-postgresql://envcite:envcite@localhost:5432/envcite}" \
  -c 'DROP SCHEMA corpus, source, retrieval CASCADE' -c 'DROP TABLE schema_migration'
```

### 第一組資料（platform）

`wt platform` 在空的庫第一次啟動時（僅 Development，`Platform:SeedDevelopmentData`）會建立：

- 租戶 `dev` 與擁有者 `owner@example.test`；
- 營運者 `operator@example.test`（庫內沒有任何營運者時才建）。

一次性密碼**只印在該終端機一次**，不進日誌，程式裡沒有預設密碼。

| 角色 | 登入位址 | 第一次登入 |
|------|----------|-----------|
| 擁有者 | `http://localhost:<port>/portal/dev/login` | 改密碼 |
| 營運者 | `http://localhost:<port>/ui/login` | 改密碼，並以任何 TOTP 驗證器註冊兩步驟驗證（無本機例外） |

之後再啟動不會再印。忘記密碼：清掉 `platform` 庫重來，或以營運者 key 重設（`POST /admin/operators/{id}/password-reset`）。已有成員的 `dev` 租戶、已有營運者的庫不會被改動。

### 其他本機設定

| 項目 | 值 |
|------|----|
| 營運者 key（`X-Admin-Key`） | 未設定時 `/admin` 停用；要用就設 `Platform__AdminKeySha256`＝key 的 SHA-256 hex |
| 營運後台（`/ui`） | Development 一律開；其他環境設 `Platform__Ui__Enabled=true`。帳號以營運者 key 建（`POST /admin/operators`），無自助註冊 |

## 常用指令

從**該服務目錄**執行；完整清單與環境變數見各服務的 `AGENTS.md`。Python 服務與 `libs/python/` 是同一個 uv workspace，依賴在倉庫根一次裝好（`uv sync --all-packages`，`wt` 建樹時已執行）。

| | `services/platform/` | `services/ontology/`、`services/consult/` |
|---|---|---|
| 測試 | `dotnet test` | `uv run python -m pytest` |
| Lint | `dotnet format --verify-no-changes` | `uv run ruff check .` |
| 格式化 | `dotnet format` | `uv run ruff format .` |
| 型別檢查 | `dotnet build`（警告視為錯誤） | `uv run python -m mypy src tests` |
| 改 proto 後重新產生 | 建置時自動產生 | `libs/python/envcite-proto/scripts/generate.sh` |

契約，從倉庫根執行：

```bash
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 breaking --against '.git#branch=main'
```

## 從開發到上線

1. **開分支**：`wt switch --create <type>/<簡述>`，type 用 `feat`、`fix`、`docs`、`refactor`、`chore` 等。
2. **Commit**：[Conventional Commits](https://www.conventionalcommits.org)，`type(scope): 說明`；破壞性變更加 `!`（如 `feat(consult)!: …`）。
3. **開 MR 到 `main`**：`main` 受保護，不直接推送；本機的 `.githooks/pre-commit` 也擋下 main 上的 commit（`wt` 建樹時啟用，既有 clone 執行 `git config core.hooksPath .githooks`）。pipeline 成功才能合併，以 squash 合併，合併後刪除來源分支。
4. **上線**：合併後 CI 把受影響服務的映像推送為 `:<commit-sha>`，之後自動部署。
5. **收尾**：`wt remove <分支>`。

- **改契約**：兩側的產生碼在同一個 MR 更新；破壞性變更開新版本（`v2`），不改 `v1`。
- **回退**：revert 有問題的 commit，照常合併上線。

CI 依改動路徑分流：

| 改動 | 跑什麼 |
|------|--------|
| 任何改動 | `gate`（讓只改文件的 MR 也有 pipeline 可合併） |
| `services/platform/` | `dotnet`（測試，含對外 REST 規格是否過期）、`dotnet-format`、`openapi`（對外 REST 的破壞性變更）、`platform-image` |
| `services/ontology/`、`libs/python/` | `ontology`、`ontology-image` |
| `services/consult/`、`libs/python/` | `consult`、`consult-image` |
| `libs/python/envcite-segmenter/` | `segmenter` |
| `libs/python/envcite-proto/` | `python-proto`（產生碼是否與 `proto/` 一致） |
| 倉庫根 `pyproject.toml`、`uv.lock` | 所有 Python job 與其映像 |
| `proto/` | `proto`（`buf lint`、`buf breaking`），以及所有消費它的服務 |

非 `main` 分支的映像 job 只驗證能建置，不推送。

## 升級版本

依賴集中在單一檔案，由 Renovate（`renovate.json`）提 MR 更新；`.gitlab-ci.yml` 的 `variables:` 上方的 `# renovate:` 註記是它辨識版本的依據，新增版本變數時一併加上（Dockerfile 的 `ARG` 由 Renovate 內建的 Dockerfile 解析讀取，不需註記）。手動升級時，表中各位置在同一次提交一起改。

執行端：`thuaisse-lab/chepb-1150011621` 的每週排程集中執行（週一台北時間 06:00 前），本倉庫不放令牌也不跑 job。同一工具出現在多處者（python、uv、.NET SDK 與映像）已設為同一個 MR。**不自動合併**：合併進 `main` 會建置映像並由 Argo CD Image Updater 自動上線，依賴更新須先看過 MR。

| 版本 | 位置 |
|------|------|
| Python 套件 | 倉庫根 `uv lock --upgrade`（或 `--upgrade-package <pkg>`）；升 `grpcio-tools`、`mypy-protobuf`、`protobuf` 時重新產生 `libs/python/envcite-proto` |
| .NET 套件 | `services/platform/Directory.Packages.props` |
| .NET SDK 與映像 | `services/platform/Dockerfile`、`.gitlab-ci.yml` 的 `DOTNET_VERSION`（`global.json` 的 `sdk.version` 只是下限，Renovate 不動它） |
| Python | `.python-version`、`.gitlab-ci.yml` 的 `PYTHON_VERSION`、`services/{ontology,consult}/Dockerfile` 的 `ARG PYTHON_VERSION` |
| uv | `.gitlab-ci.yml` 的 `UV_VERSION`、`services/{ontology,consult}/Dockerfile` 的 `ARG UV_VERSION`、各 `pyproject.toml` 的 `uv_build` 範圍 |
| PostgreSQL | `.gitlab-ci.yml` 的 `POSTGRES_MAJOR`、`services/ontology/tests/conftest.py`、`services/platform/tests/…/PlatformFactory.cs`、本檔的先決條件與開發用資料庫；主版本與正式區相同 |
| buf | `.gitlab-ci.yml` 的 `BUF_VERSION`、本檔與 `AGENTS.md` 的指令 |
| oasdiff | `.gitlab-ci.yml` 的 `OASDIFF_VERSION` |
| Docker（映像建置） | `.gitlab-ci.yml` 的 `DOCKER_VERSION` |
| hadolint | `.gitlab-ci.yml` 的 `HADOLINT_VERSION` |
