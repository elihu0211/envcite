<!--
type: tutorial
applies-to: local
owns: [dev-seed-accounts, shared-dev-db]
-->

# 參與開發

本機從裝好工具到 pull request 合併。[概念](#概念)是這一頁的層次，[索引](#索引)只連獨立文件。回[倉庫總覽](README.md)。

## 概念

同一層由上往下。依賴層只在要升版本時走。

| 層 | 節 | 做完 |
|----|----|------|
| 環境 | [先決條件](#先決條件) | 下表的工具都在 |
| 環境 | [開發用資料庫](#開發用資料庫) | `envcite` 與 `platform` 兩個庫在本機 |
| 工作 | [開分支並啟動](#開分支並啟動) | `wt list` 看得到本分支的 platform URL |
| 工作 | [本機第一個帳號](#本機第一個帳號) | 終端機印出一次性密碼，登入成功 |
| 工作 | [本機使用營運者 key](#本機使用營運者-key) | 只在要第二個營運者或重設時才做 |
| 工作 | [常用指令](#常用指令) | 在服務目錄跑完該改動要的檢查 |
| 收尾 | [從開發到上線](#從開發到上線) | pull request 已 squash 合併，worktree 已刪 |
| 依賴 | [升級版本](#升級版本) | 該版本在表上的每一處都在同一次提交裡 |

## 索引

本頁的順序在[概念](#概念)。下表是離開本頁才讀的文件。

| 層 | 完整文件 | 擁有 |
|----|----------|------|
| 帳號 | [platform README「營運者帳號」](services/platform/README.md#營運者帳號) | 欄位、端點、首次登入 |
| 帳號 | 部署庫。入口是容器層 `core-env-saas/AGENTS.md` 任務表「在 staging 建第一個營運者」 | staging 的步驟 |
| 設定 | [`CONFIGURATION.md`](CONFIGURATION.md) | 環境變數命名與機密來源 |
| 契約 | [`proto/README.md`](proto/README.md#概念) | 服務如何組合、呼叫端如何接上 |
| 契約 | [platform README「語料範圍」](services/platform/README.md#語料範圍) | 語料範圍 |
| 倉庫 | [README「目錄」](README.md#目錄) | 有哪些目錄 |
| 倉庫 | [`AGENTS.md`](AGENTS.md) | 代理的作業規範 |

## 先決條件

| 工具 | 做什麼 | 版本以何處為準 |
|------|--------|----------------|
| Git | 分支與提交 | — |
| [Worktrunk](https://worktrunk.dev)（`wt`） | 每條分支一棵 worktree，並裝好依賴 | 設定在 `.config/wt.toml` |
| Docker | 測試（Testcontainers）、映像建置、開發用資料庫 | — |
| PostgreSQL | 開發用的 `envcite`、`platform` 兩個庫 | `.github/workflows/ci.yml` 的 `POSTGRES_MAJOR`（與正式區同主版本） |
| .NET SDK | [platform](services/platform/README.md)、[consult](services/consult/README.md)、[`libs/dotnet/`](libs/dotnet/README.md) | [`global.json`](global.json) |
| [uv](https://docs.astral.sh/uv/) | [ontology](services/ontology/README.md) 與 [`libs/python/`](libs/python/AGENTS.md) | `.github/workflows/ci.yml` 的 `UV_VERSION`；Python 版本見 `.python-version`，由 uv 自動安裝 |
| Node.js | 以 `npx` 執行 buf | — |

## 開發用資料庫

| 庫 | 誰用 | 連線 |
|----|------|------|
| `envcite` | [ontology](services/ontology/README.md) | `ENVCITE_ONTOLOGY_DSN` |
| `platform` | [platform](services/platform/README.md) | `ENVCITE_ConnectionStrings__Platform` |

| 前提 | 說明 |
|------|------|
| 擴充 | 連線帳號要能在 `envcite` 建立 `btree_gist` |
| 預設值 | 未設定環境變數時，`wt` 別名（`.config/wt.toml`）與 platform 的 `appsettings.Development.json` 提供本機預設 |
| 共用 | 各分支共用這兩個庫。同一時間只跑一條分支的服務，否則遷移互相覆寫 |
| 測試 | 不用這兩個庫。Testcontainers 每次自起資料庫 |
| 遷移檔改過 | 清掉 ontology 的綱要後重新遷移並重新匯入。綱要以 [`services/ontology/migrations/`](services/ontology/migrations) 為準 |

用 Docker 起一個拋棄式的庫。帳號、密碼與庫名取 `wt` 別名的預設連線，映像標籤用[先決條件](#先決條件)的主版本：

```bash
docker run -d --name envcite-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=<帳號> -e POSTGRES_PASSWORD=<密碼> -e POSTGRES_DB=envcite postgres:<主版本>
docker exec envcite-pg createdb -U <帳號> platform
```

## 開分支並啟動

[開發用資料庫](#開發用資料庫)的兩個庫要先在。

| 順序 | 做完 |
|------|------|
| 1 | worktree 已建，依賴已裝 |
| 2 | ontology、consult、platform 都在聽。ontology 先啟動，它會先遷移再聽埠 |
| 3 | 法規已匯入，ontology 已重啟 |
| 4 | `wt list` 的 URL 是本分支 platform 的位址，下一節用它登入 |

1. `wt switch --create <type>/<簡述>`。`type` 用 `feat`、`fix`、`docs`、`refactor`、`chore`。
2. 各開一個終端機：

   ```bash
   wt ontology
   wt consult
   wt platform
   ```

3. 首次匯入法規（ontology 的庫預設是空的）。入口與環境變數見 [`services/ontology/README.md`](services/ontology/README.md)。詞典只在啟動時讀取，匯入後要重啟 `wt ontology`。

   ```bash
   cd services/ontology
   cp .env.example .env     # 填入連線與快照目錄
   uv run --env-file .env envcite-sync moj
   ```

## 本機第一個帳號

只適用本機 Development。其他環境沒有種子帳號，見[索引](#索引)。

| 何時 | 建立什麼 |
|------|----------|
| Development，且 `Platform:SeedDevelopmentData` 開著 | 空庫第一次 `wt platform` 建立種子租戶與擁有者 |
| 庫內沒有任何營運者 | 同時另建一個營運者 |
| 之後再啟動 | 不再印帳號 |

帳號與一次性密碼只印在該終端機一次，不進日誌，程式裡沒有預設密碼。

| 角色 | 登入位址 | 第一次登入 |
|------|----------|------------|
| 擁有者 | `http://localhost:<port>/portal/<租戶>/login` | 改密碼 |
| 營運者 | `http://localhost:<port>/ui/login` | 依畫面改密碼並綁定 TOTP（本機沒有例外） |

`<port>` 是 `wt list` 的 URL。忘記密碼：清掉 `platform` 庫，從[開發用資料庫](#開發用資料庫)重來，或用下一節的營運者 key 重設。欄位與端點見[索引](#索引)。

### 本機使用營運者 key

建第二個營運者，或重設，才需要這一步。

| 用途 | 呼叫 |
|------|------|
| 第二個營運者、重設密碼或驗證器 | `X-Admin-Key: $ADMIN_KEY`，位址 `http://localhost:<port>/admin/...` |

端點與欄位見[索引](#索引)的帳號列。key 與啟動必須在同一個終端機，環境變數才進得去：

```bash
read -rsp '自訂一把本機用的 key: ' ADMIN_KEY; echo
export ENVCITE_Platform__AdminKeySha256="$(printf %s "$ADMIN_KEY" | shasum -a 256 | cut -d' ' -f1)"
wt platform
```

## 常用指令

服務已經在上一節跑著。檢查從該服務目錄執行。

| 動作 | [platform](services/platform/AGENTS.md)、[consult](services/consult/AGENTS.md) | [ontology](services/ontology/AGENTS.md) |
|------|------|------|
| 測試 | `dotnet test` | `uv run python -m pytest` |
| Lint | `dotnet format --verify-no-changes` | `uv run ruff check .` |
| 格式化 | `dotnet format` | `uv run ruff format .` |
| 型別檢查 | `dotnet build`（警告視為錯誤） | `uv run python -m mypy src tests` |
| 改 proto 後重新產生 | 建置時自動產生 | `libs/python/envcite-proto/scripts/generate.sh` |

| 還要查 | 完整文件 |
|--------|----------|
| 各服務的完整指令 | 該服務的 `AGENTS.md` |
| 各服務的設定鍵 | 該服務的 README |
| 命名與機密 | [`CONFIGURATION.md`](CONFIGURATION.md) |
| .NET 共用設定 | 倉庫根 `global.json`、`Directory.*.props`、[`libs/dotnet/`](libs/dotnet/README.md) |
| Python 依賴 | [`libs/python/`](libs/python/AGENTS.md) 與 ontology 是同一個 uv workspace。倉庫根 `uv sync --all-packages`（`wt` 建樹時已執行） |
| 契約 | [`proto/`](proto/README.md)。從倉庫根執行。指令與「改 proto 的 pull request 必須同時更新兩側產生碼」見 [`AGENTS.md`](AGENTS.md) 的 Commands 與 Contract |

檢查通過後進下一節。

## 從開發到上線

分支在[開分支並啟動](#開分支並啟動)已經開好。這裡只做提交到收尾。

| 順序 | 做 | 要點 |
|------|----|------|
| 1 | Commit | [Conventional Commits](https://www.conventionalcommits.org)，`type(scope): 說明`。破壞性變更加 `!`（如 `feat(consult)!: …`） |
| 2 | 開 pull request 到 `main` | `main` 受保護，不直接推送。CI 成功才能合併，以 squash 合併，合併後刪除來源分支 |
| 3 | 上線 | 合併後 CI 把受影響服務的映像推送為 `:<commit-sha>`，之後自動部署。流程與回退見部署庫（位置見容器層 `core-env-saas/AGENTS.md`） |
| 4 | 收尾 | `wt remove <分支>` |

| 擋下在 `main` 上改動 | 在哪 |
|----------------------|------|
| commit | `.githooks/pre-commit`。`wt` 建樹時啟用；既有 clone 執行 `git config core.hooksPath .githooks` |
| 代理編輯 | `.githooks/block-main.sh`。依被編輯檔所在的 git 工作樹判斷分支，不看 hook 的工作目錄。測試見 `ci/test-block-main.sh` |

CI 跑什麼以 `ci/detect-changes.sh` 為準：

| 改動 | 跑什麼 |
|------|--------|
| 任何改動 | 一個恆有的 gate（讓只改文件的 pull request 也有 CI 可合併） |
| [`services/platform/`](services/platform/README.md) | 該服務的測試、格式、對外 REST 規格（是否過期、是否破壞相容）與映像 |
| [`services/consult/`](services/consult/README.md) | 該服務的建置、格式、測試與映像 |
| [`services/ontology/`](services/ontology/README.md) | 該服務的 lint、型別、測試與映像 |
| [`libs/dotnet/`](libs/dotnet/README.md)、倉庫根 `global.json`、`Directory.*.props`、`.editorconfig` | 所有 .NET 服務與其映像 |
| [`libs/python/`](libs/python/AGENTS.md) | 使用它的 Python 服務，以及套件自己的檢查（`envcite-proto` 含產生碼是否與 `proto/` 一致） |
| 倉庫根 `pyproject.toml`、`uv.lock` | 所有 Python 服務與其映像 |
| [`proto/`](proto/README.md) | `buf lint`、`buf breaking`，以及所有消費它的服務 |

非 `main` 分支的映像 job 只驗證能建置，不推送。

## 升級版本

上一節是日常的終點。要升依賴時才做這一節。

| 版本 | 改哪裡 | 同一次提交還要 |
|------|--------|----------------|
| Python 套件 | 倉庫根 `uv lock --upgrade`（或 `--upgrade-package <pkg>`） | 升 `grpcio-tools`、`mypy-protobuf`、`protobuf` 時重新產生 `libs/python/envcite-proto` |
| .NET 套件 | 倉庫根 `Directory.Packages.props` | — |
| .NET SDK 與映像 | `services/{platform,consult}/Dockerfile`、`.github/workflows/ci.yml` 的 `DOTNET_VERSION` | `global.json` 的 `sdk.version` 只是下限，Renovate 不動它。SDK 的 patch 決定隱含套件 `Microsoft.AspNetCore.App.Internal.Assets` 的版本，以新 SDK 執行 `dotnet restore --force-evaluate` 並更新 `packages.lock.json`，否則 CI 的 `--locked-mode` 失敗。本機 SDK 與 `DOTNET_VERSION` 同版，restore 才不會改動 lock 檔 |
| Python | `.python-version`、`.github/workflows/ci.yml` 的 `PYTHON_VERSION`、`services/ontology/Dockerfile` 的 `ARG PYTHON_VERSION` | — |
| uv | `.github/workflows/ci.yml` 的 `UV_VERSION`、`services/ontology/Dockerfile` 的 `ARG UV_VERSION`、各 `pyproject.toml` 的 `uv_build` 範圍 | — |
| PostgreSQL | `.github/workflows/ci.yml` 的 `POSTGRES_MAJOR`、`services/ontology/tests/conftest.py`、`services/ontology/tools/holdout.py`、`services/platform/tests/…/PlatformFactory.cs` | 主版本與正式區相同。ontology 要 pgvector，用 `pgvector/pgvector:pg<主版本>` |
| buf | `.github/workflows/ci.yml` 的 `BUF_VERSION` | — |
| oasdiff | `.github/workflows/ci.yml` 的 `OASDIFF_VERSION` | — |
| Docker（映像建置） | `.github/workflows/ci.yml` 的 `DOCKER_VERSION` | — |
| 後台字型（Inter、Noto Sans TC） | `services/platform/scripts/vendor-fonts.sh` 的 `INTER_VERSION`、`NOTO_VERSION` | 從 `services/platform` 執行該腳本，與重新產生的 `wwwroot/fonts/` 一起提交。Renovate 不追蹤 |
| hadolint | `.github/workflows/ci.yml` 的 `HADOLINT_VERSION` | — |

| 誰更新 | 規則 |
|--------|------|
| Renovate（`renovate.json`） | 依賴集中在單一檔案，由它提 pull request |
| `.github/workflows/ci.yml` 的 `env:` 上方 `# renovate:` 註記 | 辨識版本的依據。新增版本變數時一併加上 |
| Dockerfile 的 `ARG` | Renovate 內建的 Dockerfile 解析會讀，不需註記 |
| 執行處 | 外部排程集中執行。本倉庫不放令牌，也不跑 job |
| 同一工具出現在多處 | 已設為同一個 pull request |
| 合併 | 不自動合併。合併進 `main` 會建置映像並自動上線，須先看過 pull request |
