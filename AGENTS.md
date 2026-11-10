# AGENTS.md

## Project

服務、目錄與各自的 README、AGENTS 見根 [`README.md`](README.md) 的目錄表；本機開發環境見 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

各服務另有巢狀 `AGENTS.md`，**該樹之內以它為準**。

## 單一倉庫，獨立部署

每個 `services/*` 是各自獨立部署的服務；各用原生工具（`dotnet`、`uv`），CI 分流靠 GitLab 的 `rules:changes`，不引入 Bazel／Pants／Nx。

## Commands

各服務的指令見其 `AGENTS.md`，從**該服務目錄**執行。跨服務的從**倉庫根**執行。

版本與建置設定只在倉庫根一份，各服務共用：

- .NET（含 `libs/dotnet/`）：`global.json`、`Directory.Packages.props`、`Directory.Build.props`、`.editorconfig`
- Python：uv workspace 的 `pyproject.toml`、`uv.lock`；成員只列套件名稱；加依賴用 `uv add --raw <pkg>`；`uv.lock` 只經 `uv add`／`remove`／`lock` 改
- 有相容性理由的下界（如 `envcite-proto` 的 protobuf 執行期）把理由寫在該下界旁邊
- 收不進上述檔案的工具與映像版本，同一次提交改完 [`CONTRIBUTING.md`](CONTRIBUTING.md)「升級版本」表上的每一處

```bash
# 契約
DISABLE_TELEMETRY=1 npx @bufbuild/buf@<BUF_VERSION> lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@<BUF_VERSION> breaking --against '.git#branch=main'
```

`<BUF_VERSION>` 取 `.gitlab-ci.yml` 的 `BUF_VERSION`。

### 平行 worktree

每條分支一棵 [Worktrunk](https://worktrunk.dev) worktree，環境由 `.config/wt.toml` 建立；本機伺服器用 `wt platform`、`wt ontology`、`wt consult` 起，port 由分支推導。資料庫（各分支共用）、連線設定與本機種子帳號見 `CONTRIBUTING.md`。

## 目錄配置與擴充判準

目錄在下表的條件命中時才建立。

| 內容 | 位置 | 何時才建 |
|------|------|----------|
| 可獨立部署的服務 | `services/<name>/` | 新增服務時 |
| 契約 | `proto/envcite/<service>/v1/` | 該服務有對外 API 時 |
| 跨服務共用程式碼 | `libs/<lang>/<name>/` | 服務內的程式碼**出現第二個消費端時**才抽出，不是「將來可能要用」；本身有獨立依賴與測試週期的函式庫（如 `envcite-segmenter`）可一開始就建在這裡 |
| 服務映像 | `services/<name>/Dockerfile` | 該服務要部署時 |
| 部署清單 | 部署庫，**不在本倉庫** | 部署規則（含清單與程式碼一起改時的順序）見容器層 `core-env-saas/AGENTS.md` |

- **契約維持單一 buf 模組**（模組根是 `proto/`，import 路徑為 `envcite/<service>/v1/...`），除非某領域需要獨立發佈到 schema registry。
- **新增服務時 CI 要改**：job、`rules:changes` 錨點與 `proto` 觸發路徑；要部署的服務再加映像建置 job。`.gitlab-ci.yml` 維持單檔，超過三到四個服務才改用子管線。
- **標籤用 `<service>/vX.Y.Z`**，不用倉庫層單一版本號。

## Contract

- `proto/envcite/**` 是 gRPC API 的**單一真值**。各服務的程式碼都從這份契約產生，契約只此一份。
- API 文件：欄位語意寫在 `.proto` 註解；跨服務的組合與呼叫端接法寫在 `proto/README.md`（buf 模組文件）。改契約時兩者一起改。
- 現行套件必須通過 `buf breaking`（WIRE_JSON：守線路與 JSON 相容）；刪除欄位時以 `reserved` 保留其號碼與名稱。破壞性變更開新套件版本，消費者遷完後整個刪除舊版套件（含產生碼、健康名稱與部署探針），不並存、不留相容層；WIRE_JSON 不檢查整個套件的刪除，不必為此放寬 `buf breaking`。
- 改 proto 的 MR 必須同時更新兩側產生碼，一件事一個 MR。
- 服務本身不驗證 gRPC 呼叫端；只准指定服務呼叫由部署環境執行（見 `proto/README.md`）。

## Conventions

- 人讀文件 → `README.md`（每個目錄一份，總覽在根；本機開發環境在 `CONTRIBUTING.md`）；agent 作業 → 本檔與 skills。
- **知識文件的組織**（概念節點、任務入口、front-matter）以容器層為準，見 `core-env-saas/AGENTS.md` 的「知識文件」；本檔不重述。文件 front-matter 與相對連結由 `ci/check-docs.py` 在 CI 檢查，本機從倉庫根 `python3 ci/check-docs.py`。
- **文件只寫不會過時的**：規範、不變式、契約（端點、欄位、設定鍵、格式；寫在擁有該概念的 README）與指向真值的指標。版本、清單、預設值、事件名稱、日期、「目前／尚未」這類現況，以程式碼與測試為準，不寫進文件；決策論據寫在 MR 說明，不寫進 `AGENTS.md`（作業規範寫進論據必然過期）。
- **可觀測性各服務同一約定**：只認標準 `OTEL_*` 鍵，沒設 `OTEL_EXPORTER_OTLP_ENDPOINT` 就不匯出；指標標籤只用有限集合，租戶與請求內容不進指標。RED 指標的兩側對照見 `proto/README.md`。
- **CI 的邏輯在 `ci/` 腳本，`.gitlab-ci.yml` 只決定何時跑、傳什麼參數**：腳本不讀 `CI_*` 等平台變數（`ci/test-platform-neutral.sh` 擋），平台資訊由 job 轉成參數，所以同一支腳本在本機與 CI 以同一指令執行，換 CI 平台只重寫觸發層。新增會被 job 呼叫的腳本，同時補 `ci/test-*.sh` 的離線測試。
- **配置檔與腳本的註解**：繁體中文，只寫「為什麼」與前提，不寫程式本身已說明的「做什麼」，不寫日期與事件經過；不用裝飾分隔線；JSON 沒有註解，用 `description` 欄位。
- 新增或搬動目錄、服務時，同一次提交更新根 `README.md` 的目錄表與各 `AGENTS.md` 的索引。
- 本檔保持簡短；長流程 → `.agents/skills/<name>/SKILL.md`。各服務的 `AGENTS.md` 只寫該服務獨有的，倉庫層規範以本檔為準、不重複。

## Layering

| 層 | 路徑 | 載入時機 |
|----|------|----------|
| Baseline | 本檔；各服務巢狀 `AGENTS.md`（該樹之內以它為準） | 一律 |
| Skills | `.agents/skills/<name>/SKILL.md`（[規格](https://agentskills.io/specification)） | 需要時 |
| 共用 skills | 由 skills CLI 安裝，來源與版本釘在 `skills-lock.json` | 同上；`DISABLE_TELEMETRY=1 npx skills update -p -y` 更新 |
| Claude 橋接 | `CLAUDE.md` → `@AGENTS.md`；`.claude/skills/<name>` → **逐 skill** symlink 至 `../../.agents/skills/<name>`（不 symlink 整個 `skills` 目錄） | 僅 Claude Code |
| Gemini 橋接 | `.gemini/settings.json` → `context.fileName: ["AGENTS.md"]` | 僅 Gemini CLI（其預設為 `GEMINI.md`） |
| Cursor rules | `.cursor/rules/*.mdc`（有路徑限定的指引時才建） | 僅 Cursor |

- 共用 agent 設定（`.agents/`、`.gemini/`、`skills-lock.json`）只放倉庫根，服務層不重複。
- 倉庫專屬 skill 寫在 `.agents/skills/`；`.claude/skills/` 內只放 symlink，**不要**另立一棵樹。
- 列在 `skills-lock.json` 的 skill **不要手改**，改在 lock 檔所列的來源 repo，再用 `npx skills update` 更新。
- 只服務單一服務的 skill 放 `services/<name>/.claude/skills/`（Claude Code 會巢狀探索）。
- 基準只有本檔：不新增 `.cursorrules`、`GEMINI.md` 或其他平行基準檔；`CLAUDE.md` 只匯入本檔，另加的只有 Claude 專屬的說明。

## Safety

- 不提交機密或真實 `.env*` 值。
- 不在 `main` 上改動或 commit：先開分支，走 MR（擋下的 hook 見 `CONTRIBUTING.md`「從開發到上線」）。
- 動手前先問：force-push、改寫歷史、正式環境部署、破壞性資料庫操作、對共用資料庫套用遷移。
- 分支收尾（落地或交接）依容器層 `core-env-saas/AGENTS.md`。
- 產生物與各服務專屬的禁改路徑、資料來源標示，見各巢狀 `AGENTS.md`（`envcite-proto` 的產生碼見其 README）。

## Done when

受影響服務的 lint／typecheck／test 與 `buf lint` 通過——由 `.gitlab-ci.yml` 依路徑分流強制。
