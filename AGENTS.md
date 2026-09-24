# AGENTS.md

Vendor-neutral instructions for coding agents (Cursor, Codex, Claude Code, Copilot, Gemini CLI, …).

This file is canonical. `CLAUDE.md` only imports it (`@AGENTS.md`) per [Claude Code docs](https://code.claude.com/docs/en/memory) so sessions that can't read `AGENTS.md` directly still get it. Never duplicate this content into vendor-specific files.

## Project

- **Name:** env-law — 環境法規 SaaS，單一倉庫、多個獨立部署的服務
- **Remote:** `git@gitlab.com:thuaisse-lab/env-law.git`

| 路徑 | 內容 | 技術棧 |
|------|------|--------|
| `proto/` | gRPC 契約，**唯一真值** | protobuf · buf |
| `services/platform/` | 多租戶控制平面：tenants、API keys、配額、計量、對外 REST | C# · .NET 10 · ASP.NET Core · PostgreSQL |
| `services/ontology/` | 法規範本體與檢索（shared, non-tenant） | Python 3.14 · uv · gRPC · PostgreSQL |
| `services/consult/` | 接地問答：以本體事實為據作答（租戶隔離） | Python 3.14 · uv · gRPC（streaming） |
| `libs/python/` | 兩個以上服務共用的 Python 套件 | 路徑相依，不發佈 |
| `deploy/` | Kubernetes 清單（CloudNativePG、服務、遷移 Job） | kustomize · kubeconform |

各服務另有巢狀 `AGENTS.md`，**該樹之內以它為準**。

## 單一倉庫，不是單體

合併倉庫**不改變部署邊界**：每個 `services/*` 仍是各自獨立部署的服務；`proto/` 放同一倉庫，是為了契約變更的原子性。**不要引入 Bazel／Pants／Nx**——各服務用自己的原生工具（`dotnet`、`uv`），CI 分流靠 GitLab 的 `rules:changes`。論據見 `env-law-docs` 的 05-01。

## Commands

各服務的指令見其 `AGENTS.md`，從**該服務目錄**執行。跨服務的從**倉庫根**執行：

```bash
# 契約
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 breaking --against '.git#branch=main'
```

文件工具鏈（`./scripts/docs check`／`render`）在 `env-law-docs`，從該庫根執行。

## 目錄配置與擴充判準

下列判準是**新增服務時照著做的**，不是現在就要建的目錄——**空目錄不先建**，與文件庫「規劃中的卷次不建空殼」同一條理由。

| 內容 | 位置 | 何時才建 |
|------|------|----------|
| 可獨立部署的服務 | `services/<name>/` | 新增服務時 |
| 契約 | `proto/env_law/<service>/v1/` | 該服務有對外 API 時 |
| 跨服務共用程式碼 | `libs/<lang>/<name>/` | **出現第二個消費端時**，不是「將來可能要用」（已發生一次：`env-law-grpc` 於 consult 成為認證攔截器的第二個消費端時抽出） |
| 服務映像 | `services/<name>/Dockerfile` | 該服務要部署時 |
| 跨服務編排 | `deploy/` | 有第二個部署單元要一起編排時（已發生：資料庫與本體服務）。**只放能跑的東西**——沒有 adapter 的服務、尚未存在的匯入器都不放，缺席是狀態不是遺漏 |

- **契約維持單一 buf 模組**（`buf.yaml` 的模組根是 `proto/`，import 路徑為 `env_law/<service>/v1/...`）。拆成每領域一個模組會改變每一條 import 路徑，除非某領域需要獨立發佈到 schema registry，否則不拆。
- **CI 的擴充點是「新增一個服務要改幾個地方」**：現為三處（job、`rules:changes` 錨點、`proto` 觸發路徑）。三到四個服務內維持單檔，再多才改用子管線（`trigger: include:`），不要提早拆。
- **標籤用 `<service>/vX.Y.Z`**，不用倉庫層單一版本號。

論據見 `env-law-docs` 的 05-02。

## 文件庫在另一個倉庫

技術決策及其論據記於 `env-law-docs`（本倉庫的同層目錄，非子目錄）：`.qmd` → Quarto → xelatex。結構、守門與為什麼與程式碼分倉，見該庫自己的 `AGENTS.md`。

改動設計時，決策的論據寫進該庫，不要寫進本倉庫的 `AGENTS.md`：後者是作業規範，寫進論據必然過期。

## Contract

- `proto/env_law/**` 是 gRPC API 的**單一真值**，各服務都由此產生程式碼，**不得各自複製一份**。
- 破壞性變更要開新套件版本（`v2`）；`v1` 必須通過 `buf breaking`。
- 改 proto 的 MR 必須同時更新兩側產生碼——這是單一倉庫換來的性質，別再用兩個 MR 做一件事。

## Conventions

- 小而聚焦的 diff；有既有程式碼時對齊其風格。
- 人讀文件 → `README.md`；決策論據 → 同層的 `env-law-docs`；agent 作業 → 本檔與 skills。
- 本檔保持簡短；長流程 → `.agents/skills/<name>/SKILL.md`。各服務的 `AGENTS.md` 只寫該服務獨有的，倉庫層規範以本檔為準、不重複。

## Layering

| 層 | 路徑 | 載入時機 |
|----|------|----------|
| Baseline | 本檔；各服務巢狀 `AGENTS.md`（該樹之內以它為準） | 一律 |
| Skills | `.agents/skills/<name>/SKILL.md`（[規格](https://agentskills.io/specification)） | 需要時 |
| 共用 skills | 自 [`elihu0211/agent-config`](https://github.com/elihu0211/agent-config) 安裝，釘在 `skills-lock.json` | 同上；`DISABLE_TELEMETRY=1 npx skills update -p -y` 更新 |
| Claude 橋接 | `CLAUDE.md` → `@AGENTS.md`；`.claude/skills/<name>` → **逐 skill** symlink 至 `../../.agents/skills/<name>`（官方文件述明可 symlink 的是 `<skill-name>` 項目本身，不是整個 `skills` 目錄） | 僅 Claude Code |
| Gemini 橋接 | `.gemini/settings.json` → `context.fileName: ["AGENTS.md"]` | 僅 Gemini CLI（其預設為 `GEMINI.md`） |
| Cursor rules | `.cursor/rules/*.mdc`（目前沒有） | 僅 Cursor，路徑限定指引 |

- 共用 agent 設定（`.agents/`、`.gemini/`、`skills-lock.json`）只放倉庫根，服務層不重複。
- 倉庫專屬 skill 寫在 `.agents/skills/`；`.claude/skills/` 內只放 symlink，**不要**另立一棵樹。
- 列在 `skills-lock.json` 的 skill **不要手改**，改在 `agent-config` 再更新。
- **只服務單一服務的 skill 可放 `services/<name>/.claude/skills/`**：Claude Code 對單一倉庫多服務支援巢狀探索（自起始目錄往上至倉庫根，巢狀子目錄則於實際讀寫該處檔案時動態載入），不必為了共用而把兩個堆疊的內容硬併成一份。
- 不要新增 `.cursorrules`、`GEMINI.md` 或其他平行基準檔；`CLAUDE.md` 維持純匯入橋接。

## Safety

- 不提交機密或真實 `.env*` 值。
- 動手前先問：force-push、改寫歷史、正式環境部署、破壞性資料庫操作。
- 產生物與各服務專屬的禁改路徑、資料來源標示，見各服務 `AGENTS.md` 的 Safety。

## Done when

- 受影響服務的 lint／typecheck／test 與 `buf lint` 通過——由 `.gitlab-ci.yml` 依路徑分流強制。
- Commands 與 Safety 清單在工具鏈變動後仍然正確。
