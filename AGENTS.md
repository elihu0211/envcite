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
| `scripts/` | 跨服務工具鏈（文件、契約產生） | — |

各服務另有巢狀 `AGENTS.md`，**該樹之內以它為準**。

## 為什麼是單一倉庫（不是單體）

合併倉庫**不改變部署邊界**：每個 `services/*` 仍是各自獨立部署的服務。變的只有原始碼位置、契約共享方式與 CI 分流。

改為單一倉庫的理由是契約：proto 先前由 git submodule 共享，為了一個 1.4 KB 的檔案把整棵 Python 樹 vendor 進 C# 倉庫，且契約變更不是原子的——改 proto 要先合一個 MR、再更新 submodule SHA 開第二個 MR，中間有一段兩邊對不上的視窗，而 `buf breaking` 只驗得到自己那一側、看不到消費端跟上沒有。現在 proto 在根、兩個服務同時受同一次變更檢驗。

**不要引入 Bazel／Pants／Nx。** 各服務用自己的原生工具（`dotnet`、`uv`），分流靠 GitLab CI 的 `rules:changes`；那些多語言建置系統是規模到了才需要的，現在導入會用一個更大的問題換掉 submodule。

## Commands

從**倉庫根**執行：

```bash
# ontology（Python）
cd services/ontology && uv sync
cd services/ontology && uv run python -m pytest
cd services/ontology && uv run ruff check . && uv run ruff format .
cd services/ontology && uv run python -m mypy src tests
cd services/ontology && ./scripts/generate-proto.sh      # 改完 proto 後重新產生

# platform（C#）
cd services/platform && dotnet build
cd services/platform && dotnet test
cd services/platform && dotnet format

# 契約
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 breaking --against '.git#branch=main'

# 文件
./scripts/docs check      # 來源層守門
./scripts/docs render     # 產出 PDF 並驗產物層
```

## 文件庫在另一個倉庫

技術決策及其論據記於 `env-law-docs`（本倉庫的同層目錄，非子目錄）：`.qmd` → Quarto → xelatex，十卷結構，十一項守門。它與本倉庫的分開理由與服務合併的理由不衝突——合併是為了**契約原子性**，而文件不共用 `proto/`，沒有那個問題要解。

改動設計時，決策的論據寫進該庫，不要寫進本倉庫的 `AGENTS.md`：後者是作業規範，寫進論據必然過期。

## Contract

- `proto/env_law/**` 是 gRPC API 的**單一真值**，兩個服務都由此產生程式碼，**不得各自複製一份**。
- 破壞性變更要開新套件版本（`v2`）；`v1` 必須通過 `buf breaking`。
- 本體資料不分租戶；`x-tenant-id` / `x-request-id` 僅供記錄與稽核。
- 改 proto 的 MR 必須同時更新兩側產生碼——這是單一倉庫換來的性質，別再用兩個 MR 做一件事。

## Conventions

- 小而聚焦的 diff；有既有程式碼時對齊其風格。
- 人讀文件 → `README.md`；決策論據 → 同層的 `env-law-docs`；agent 作業 → 本檔與 skills。
- 不要新增 `.cursorrules`、`GEMINI.md` 或其他平行基準檔；`CLAUDE.md` 維持純匯入橋接。
- 共用 agent 設定（`.agents/`、`.gemini/`、`skills-lock.json`）只放倉庫根，服務層不重複。
- `.claude/skills/<name>` 是**逐 skill** 的 symlink，指向 `../../.agents/skills/<name>`——官方文件述明可 symlink 的是 `<skill-name>` 項目本身，不是整個 `skills` 目錄。
- **只服務單一服務的 skill 可放 `services/<name>/.claude/skills/`**：Claude Code 對單一倉庫多服務支援巢狀探索（自起始目錄往上至倉庫根，巢狀子目錄則於實際讀寫該處檔案時動態載入），不必為了共用而把兩個堆疊的內容硬併成一份。

## Safety

- 不提交機密或真實 `.env*` 值。
- 動手前先問：force-push、改寫歷史、正式環境部署、破壞性資料庫操作。
- 不要手改產生物：`services/ontology/uv.lock`（用 `uv add/remove/lock`）、`.venv/`、`services/ontology/src/env_law/ontology/v1/*_pb2*`（跑 `generate-proto.sh`）。
- 樣本資料（`services/ontology/src/env_law/data/laws.ttl`）是占位 fixture，不是權威法條；真實資料須引用全國法規資料庫（政府資料開放授權條款－第1版，需標示出處）。

## Done when

- 受影響服務的 lint／typecheck／test 與 `buf lint` 通過——由 `.gitlab-ci.yml` 依路徑分流強制。
- 改了文件則 `./scripts/docs check` 通過。
- Commands 與 Safety 清單在工具鏈變動後仍然正確。
