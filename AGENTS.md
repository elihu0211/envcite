# AGENTS.md

## Project

- **Name:** envcite（環境知識 EnvCite）— 臺灣環境知識的檢索與引用接地服務，單一倉庫、多個獨立部署的服務
- **Remote:** `git@gitlab.com:thuaisse-lab/envcite.git`

| 路徑 | 內容 | 技術棧 | 文件 |
|------|------|--------|------|
| `proto/` | gRPC 契約，**唯一真值** | protobuf · buf | [README](proto/README.md) |
| `services/platform/` | 多租戶控制平面與對外 REST | C# · ASP.NET Core · PostgreSQL | [README](services/platform/README.md) · [AGENTS](services/platform/AGENTS.md) |
| `services/ontology/` | 本體：各來源知識的集中處、檢索與推論（shared, non-tenant） | Python · uv · gRPC · PostgreSQL | [README](services/ontology/README.md) · [AGENTS](services/ontology/AGENTS.md) |
| `services/consult/` | 接地：為呼叫端的語言模型備妥封閉證據集，或拒答（租戶隔離；本身不執行生成模型） | Python · uv · gRPC | [README](services/consult/README.md) · [AGENTS](services/consult/AGENTS.md) |
| `libs/python/` | 服務之外、可獨立測試的 Python 套件（含 `envcite-proto`：`proto/` 的 Python 產生碼） | uv workspace 成員，不發佈 | [AGENTS](libs/python/AGENTS.md) · [envcite-grpc](libs/python/envcite-grpc/README.md) · [envcite-proto](libs/python/envcite-proto/README.md) · [envcite-segmenter](libs/python/envcite-segmenter/README.md) |

總覽與目錄見根 [`README.md`](README.md)；本機開發環境見 [`CONTRIBUTING.md`](CONTRIBUTING.md)。工具與執行期的版本不寫在文件裡，以 `services/platform/global.json`、`.python-version`、`pyproject.toml` 與 `uv.lock` 為準。

各服務另有巢狀 `AGENTS.md`，**該樹之內以它為準**。

## 單一倉庫，不是單體

合併倉庫**不改變部署邊界**：每個 `services/*` 仍是各自獨立部署的服務；`proto/` 放同一倉庫，是為了契約變更的原子性。**不要引入 Bazel／Pants／Nx**——各服務用自己的原生工具（`dotnet`、`uv`），CI 分流靠 GitLab 的 `rules:changes`。

## Commands

各服務的指令見其 `AGENTS.md`，從**該服務目錄**執行。跨服務的從**倉庫根**執行：

- **版本集中管理，不在各處各寫一份**：.NET 套件版本只在 `services/platform/Directory.Packages.props`（Central Package Management）；Python 是倉庫根的 uv workspace（`pyproject.toml` 列成員、開發工具與 ruff／mypy／pytest 設定，`uv.lock` 定版本），成員只列套件名稱，加依賴用 `uv add --raw <pkg>`，`uv.lock` 只經 `uv add／remove／lock` 改。唯一例外是有相容性理由的下界（如 `envcite-proto` 的 protobuf 執行期），理由寫在旁邊。工具與映像的版本無法收成單一來源時，升版依 `CONTRIBUTING.md`「升級版本」的表，所列位置在同一次提交一起改。

```bash
# 契約
DISABLE_TELEMETRY=1 npx @bufbuild/buf@<BUF_VERSION> lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@<BUF_VERSION> breaking --against '.git#branch=main'
```

`<BUF_VERSION>` 取 `.gitlab-ci.yml` 的 `BUF_VERSION`。

### 平行 worktree

每條分支一棵 [Worktrunk](https://worktrunk.dev) worktree；環境由 `.config/wt.toml` 建立，不靠手動：建樹時各服務裝好依賴。本機伺服器用別名起，port 由分支推導，平行的樹不相撞；資料庫各服務一個固定的庫（`envcite`、`platform`），各分支共用，同一時間只跑一條分支的服務：

```bash
wt platform    # dotnet run；庫 platform，自動遷移；空庫第一次啟動會種 dev 租戶與擁有者（登入資訊印在終端機）
wt ontology    # envcite-migrate（庫 envcite）→ gRPC 伺服器
wt consult     # gRPC 伺服器，指向本樹的 ontology（platform 的 /v2/ground 需要它）
wt list        # URL 欄是本樹 platform 的位址
```

資料庫要先備妥（版本見 `CONTRIBUTING.md`）。連線取 `ONTOLOGY_DSN`、`ConnectionStrings__Platform`，未設定時預設 `localhost:5432`、帳號／密碼 `envcite`／`envcite`（同 `appsettings.Development.json`）；建法見 `CONTRIBUTING.md`。測試不經此路——Testcontainers 每次自起一個庫。

## 目錄配置與擴充判準

下列判準是**新增服務時照著做的**，不是現在就要建的目錄——**空目錄不先建**。

| 內容 | 位置 | 何時才建 |
|------|------|----------|
| 可獨立部署的服務 | `services/<name>/` | 新增服務時 |
| 契約 | `proto/envcite/<service>/v1/` | 該服務有對外 API 時 |
| 跨服務共用程式碼 | `libs/<lang>/<name>/` | 服務內的程式碼**出現第二個消費端時**才抽出，不是「將來可能要用」；本身有獨立依賴與測試週期的函式庫（如 `envcite-segmenter`）可一開始就建在這裡 |
| 服務映像 | `services/<name>/Dockerfile` | 該服務要部署時 |
| 部署清單 | 部署庫，**不在本倉庫** | 本倉庫只產出映像（CI 在 `main` 推送 `:<commit-sha>`），上線由部署庫自動完成。程式碼與清單要一起改時**清單先行**——新映像會自動上線，順序反了就是新映像配舊清單；上線流程與跨庫規則見容器層 `core-env-saas/AGENTS.md` |

- **契約維持單一 buf 模組**（`buf.yaml` 的模組根是 `proto/`，import 路徑為 `envcite/<service>/v1/...`）。拆成每領域一個模組會改變每一條 import 路徑，除非某領域需要獨立發佈到 schema registry，否則不拆。
- **CI 的擴充點是「新增一個服務要改幾個地方」**：要改的是 job、`rules:changes` 錨點與 `proto` 觸發路徑；要部署的服務再加映像建置 job。三到四個服務內維持單檔，再多才改用子管線（`trigger: include:`），不要提早拆。
- **標籤用 `<service>/vX.Y.Z`**，不用倉庫層單一版本號。

## Contract

- `proto/envcite/**` 是 gRPC API 的**單一真值**，各服務都由此產生程式碼，**不得各自複製一份**。
- API 文件：欄位語意寫在 `.proto` 註解；跨服務的組合與呼叫端接法寫在 `proto/README.md`（buf 模組文件）。改契約時兩者一起改。
- 破壞性變更要開新套件版本（`v2`）；`v1` 必須通過 `buf breaking`（WIRE_JSON：守線路與 JSON 相容）。刪除欄位時以 `reserved` 保留其號碼與名稱。
- 改 proto 的 MR 必須同時更新兩側產生碼——這是單一倉庫換來的性質，一件事一個 MR。
- 服務間的 gRPC 認證由叢集的服務網格負責（見 `proto/README.md`），服務本身不驗證呼叫端。

## Conventions

- 人讀文件 → `README.md`（每個目錄一份，總覽在根；本機開發環境在 `CONTRIBUTING.md`）；agent 作業 → 本檔與 skills。
- **知識文件的組織**（概念節點、任務入口、front-matter）以容器層為準，見 `core-env-saas/AGENTS.md` 的「知識文件」；本檔不重述。
- **文件只寫不會過時的**：規範、不變式與指向真值的指標。版本、清單、預設值、端點與事件名稱、日期、「目前／尚未」這類現況，以程式碼與測試為準，不寫進文件；決策論據寫在 MR 說明，不寫進 `AGENTS.md`（作業規範寫進論據必然過期）。
- **可觀測性各服務同一約定**：只認標準 `OTEL_*` 鍵，沒設 `OTEL_EXPORTER_OTLP_ENDPOINT` 就不匯出；指標標籤只用有限集合，租戶與請求內容不進指標。RED 指標的兩側對照見 `proto/README.md`。
- **配置檔與腳本的註解**：繁體中文，只寫「為什麼」與前提，不寫程式本身已說明的「做什麼」，不寫日期與事件經過；不用裝飾分隔線；JSON 沒有註解，用 `description` 欄位。
- 新增或搬動目錄、服務時，同一次提交更新根 `README.md` 的目錄與本檔的索引表，以及各 `AGENTS.md` 的索引。
- 本檔保持簡短；長流程 → `.agents/skills/<name>/SKILL.md`。各服務的 `AGENTS.md` 只寫該服務獨有的，倉庫層規範以本檔為準、不重複。

## Layering

| 層 | 路徑 | 載入時機 |
|----|------|----------|
| Baseline | 本檔；各服務巢狀 `AGENTS.md`（該樹之內以它為準） | 一律 |
| Skills | `.agents/skills/<name>/SKILL.md`（[規格](https://agentskills.io/specification)） | 需要時 |
| 共用 skills | 由 skills CLI 安裝，來源與版本釘在 `skills-lock.json` | 同上；`DISABLE_TELEMETRY=1 npx skills update -p -y` 更新 |
| Claude 橋接 | `CLAUDE.md` → `@AGENTS.md`；`.claude/skills/<name>` → **逐 skill** symlink 至 `../../.agents/skills/<name>`（官方文件述明可 symlink 的是 `<skill-name>` 項目本身，不是整個 `skills` 目錄） | 僅 Claude Code |
| Gemini 橋接 | `.gemini/settings.json` → `context.fileName: ["AGENTS.md"]` | 僅 Gemini CLI（其預設為 `GEMINI.md`） |
| Cursor rules | `.cursor/rules/*.mdc`（有路徑限定的指引時才建） | 僅 Cursor |

- 共用 agent 設定（`.agents/`、`.gemini/`、`skills-lock.json`）只放倉庫根，服務層不重複。
- 倉庫專屬 skill 寫在 `.agents/skills/`；`.claude/skills/` 內只放 symlink，**不要**另立一棵樹。
- 列在 `skills-lock.json` 的 skill **不要手改**，改在 lock 檔所列的來源 repo，再用 `npx skills update` 更新。
- **只服務單一服務的 skill 可放 `services/<name>/.claude/skills/`**：Claude Code 對單一倉庫多服務支援巢狀探索（自起始目錄往上至倉庫根，巢狀子目錄則於實際讀寫該處檔案時動態載入），不必為了共用而把兩個堆疊的內容硬併成一份。
- 基準只有本檔：不新增 `.cursorrules`、`GEMINI.md` 或其他平行基準檔；`CLAUDE.md` 只匯入本檔，另加的只有 Claude 專屬的說明。

## Safety

- 不提交機密或真實 `.env*` 值。
- 不在 `main` 上改動或 commit：先開分支，走 MR（`.githooks/pre-commit` 擋下 main 上的 commit；`wt` 建樹時啟用，既有 clone 執行 `git config core.hooksPath .githooks`）。
- 動手前先問：force-push、改寫歷史、正式環境部署、破壞性資料庫操作、對共用資料庫套用遷移。
- 分支收尾（落地或交接）依容器層 `core-env-saas/AGENTS.md`。
- 產生物與各服務專屬的禁改路徑、資料來源標示，見各巢狀 `AGENTS.md`（`envcite-proto` 的產生碼在 `libs/python/AGENTS.md`）。

## Done when

- 受影響服務的 lint／typecheck／test 與 `buf lint` 通過——由 `.gitlab-ci.yml` 依路徑分流強制。
- Commands 與 Safety 清單在工具鏈變動後仍然正確。
