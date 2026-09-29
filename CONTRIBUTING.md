# 參與開發

## 快速入門

```bash
# 1. 開發用 PostgreSQL（整台機器起一次，各分支共用）
docker run -d --name env-law-dev-pg --restart unless-stopped \
  -p 127.0.0.1:5432:5432 -e POSTGRES_PASSWORD=postgres \
  -v env-law-dev-pg:/var/lib/postgresql \
  pgvector/pgvector:0.8.1-pg18

# 2. 開分支：建 worktree 並裝好依賴
wt switch --create feat/<簡述>

# 3. 起服務（各開一個終端機；自動建本分支的資料庫並遷移）
wt ontology
wt consult
wt platform
wt list        # URL 欄＝本分支 platform 的位址

# 4. 合併後收尾：刪 worktree、分支與本分支的資料庫
wt remove feat/<簡述>
```

需要的工具見文末「準備工具」。

## 連線資訊

全是本機值，不是機密。

### PostgreSQL

帳號 `postgres`、密碼 `postgres`，位址 `localhost:5432`。

| 資料庫 | 誰建立、何時刪除 |
|--------|------------------|
| `ontology_<分支代號>` | `wt ontology` 時自動建立；`wt remove` 時刪除 |
| `platform_<分支代號>` | `wt platform` 時自動建立；`wt remove` 時刪除 |
| `envlaw_platform` | 不經 `wt`、直接 `dotnet run` platform 時 |

**分支代號**由分支名推導（`.config/wt.toml` 的 `sanitize_db`）：轉小寫、非英數字轉底線，再加 3 碼雜湊避免撞名。例如 `main` → `main_vfz`，`feat/import-pipeline` → `feat_import_pipeline_wq5`。所以 `main` 分支的 ontology 資料庫是 `ontology_main_vfz`，連線字串：

```text
postgresql://postgres:postgres@localhost:5432/ontology_main_vfz
```

```bash
wt step eval '{{ branch | sanitize_db }}'                              # 在該分支的 worktree 內執行，印出分支代號
docker exec -it env-law-dev-pg psql -U postgres -l                     # 列出所有資料庫（不需在本機安裝 psql）
docker exec -it env-law-dev-pg psql -U postgres -d ontology_main_vfz   # 進入某個資料庫
```

### 其他

| 項目 | 值 |
|------|----|
| 服務之間的內部 token | `dev` |
| platform 營運者 key（`X-Admin-Key`） | 未設定（`/admin` 停用）；要用就設環境變數 `Platform__AdminKeySha256`＝key 的 SHA-256 hex |

## 常用指令

各服務的指令從**該服務目錄**執行；完整清單與環境變數見各服務的 `AGENTS.md`。

| | `services/platform/` | `services/ontology/`、`services/consult/` |
|---|---|---|
| 測試 | `dotnet test` | `uv run python -m pytest` |
| Lint | `dotnet format --verify-no-changes` | `uv run ruff check .` |
| 格式化 | `dotnet format` | `uv run ruff format .` |
| 型別檢查 | 建置即檢查（`dotnet build`，警告視為錯誤） | `uv run python -m mypy src tests` |
| 改 proto 後重新產生 | 建置時自動產生 | `./scripts/generate-proto.sh` |

測試自起 Testcontainers 資料庫，需要 Docker，不用開發資料庫。

契約（從倉庫根執行）：

```bash
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 breaking --against '.git#branch=main'
```

## 從開發到上線

1. **開分支**：`wt switch --create <type>/<簡述>`，type 用 `feat`、`fix`、`docs`、`refactor`、`chore` 等。
2. **Commit**：[Conventional Commits](https://www.conventionalcommits.org)，`type(scope): 說明`；破壞性變更加 `!`（如 `feat(consult)!: …`）。
3. **開 MR 到 `main`**：`main` 受保護，不直接推送。**pipeline 成功才能合併**，以 squash 合併，合併後刪除來源分支。
4. **上線**：合併後 CI 把受影響服務的映像推送為 `:<commit-sha>`，之後自動部署，不需人工。
5. **收尾**：`wt remove <分支>`。

兩條規則：

- **改契約**：兩側的產生碼在同一個 MR 更新；破壞性變更開新版本（`v2`），不改 `v1`。
- **回退**：在本倉庫 revert 有問題的 commit，照常合併上線。

CI 依改動路徑分流，只跑受影響的部分：

| 改動 | 跑什麼 |
|------|--------|
| 任何改動 | `gate`（讓只改文件的 MR 也有 pipeline 可以合併） |
| `services/platform/` | `dotnet`、`platform-image` |
| `services/ontology/`、`libs/python/` | `python`（含產生碼是否過期）、`ontology-image` |
| `services/consult/`、`libs/python/` | `consult`、`consult-image` |
| `libs/python/env-law-segmenter/` | `segmenter` |
| `proto/` | `proto`（`buf lint`、`buf breaking`），以及所有消費它的服務 |

非 `main` 分支的映像 job 只驗證能建置，不推送。


## 環境說明

- **開發用 PostgreSQL** 與 ontology 測試同一個映像（`services/ontology/tests/conftest.py`），內含 `vector` 與 `btree_gist`。資料在具名 volume `env-law-dev-pg`，刪容器不會刪資料；要從零開始：`docker rm -f env-law-dev-pg && docker volume rm env-law-dev-pg`。只綁 `127.0.0.1`，不對區網開放。
- **每條分支一棵 worktree**（`.config/wt.toml`）：port 與資料庫名由分支推導，平行的分支不相撞。

## 準備工具

| 工具 | 版本 | 用途 |
|------|------|------|
| Git | — | |
| Docker 相容的容器執行環境 | — | 開發用 PostgreSQL、測試、映像建置 |
| .NET SDK | 10.0（`services/platform/global.json`） | platform |
| [uv](https://docs.astral.sh/uv/) | CI 用 0.12.18 | ontology、consult、`libs/python`；Python 3.14 由 uv 自動安裝 |
| [Worktrunk](https://worktrunk.dev)（`wt`） | — | 分支與本機環境 |
| Node.js | — | 以 `npx` 執行 buf |
