---
type: reference
applies-to: all
owns: [operator-accounts]
---

# envcite-platform

多租戶控制平面：租戶、API key、限流、計量，以及對外 REST API（`/v2`）。法規與接地請求經 gRPC 轉給 `services/ontology` 與 `services/consult`。回[倉庫總覽](../../README.md)。

| 項目 | 內容 |
|------|------|
| 介面 | `/v2` 租戶 REST API（API key）；`/admin` 營運者 API；`/ui` 營運者後台；`/portal` 客戶入口（以設定開關） |
| 技術棧 | C# · .NET（SDK 版本見 `global.json`）· ASP.NET Core · PostgreSQL |
| 呼叫端 | 外部用戶端，只經本服務進入系統 |
| 上游 | [`services/ontology`](../ontology/README.md)、[`services/consult`](../consult/README.md)（gRPC） |
| 資料 | PostgreSQL，schema `platform`；租戶隔離是執行期機制（租戶解析加查詢過濾） |
| 契約 | [`openapi/openapi_v2.json`](openapi/openapi_v2.json)，每次 `dotnet build` 產生，隨程式碼一起提交；上游 gRPC 契約見 [`proto/README.md`](../../proto/README.md) |
| 開發指引 | [本目錄 `AGENTS.md`](AGENTS.md)；倉庫層規範見根 [`AGENTS.md`](../../AGENTS.md) |
| 部署 | 部署庫的 `base/platform/` |

外部呼叫端的接法見 [`proto/README.md`](../../proto/README.md) 與 [`openapi/openapi_v2.json`](openapi/openapi_v2.json)。

## 一次 `/v2` 請求

```mermaid
%%{init: {"flowchart": {"curve": "basis"}}}%%
flowchart LR
    req(["用戶端<br/>Bearer API key"]) --> auth{"驗證 API key<br/>解析租戶"}
    auth -->|失敗| e401(["401"])
    auth --> plan{"租戶有方案？"}
    plan -->|否| e402(["402"])
    plan --> rate{"限流"}
    rate -->|超過| e429(["429"])
    rate --> rpc["gRPC 呼叫<br/>ontology · consult"]
    rpc -->|成功| meter["計量一筆"]
    rpc -->|不可用 · 逾時| e503(["503<br/>不計量"])
    meter --> res(["回應"])

    classDef step fill:#eef2ff,stroke:#6366f1,color:#1e1b4b
    classDef gate fill:#fffbeb,stroke:#f59e0b,color:#78350f
    classDef ext fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 3,color:#334155
    classDef stop fill:#fef2f2,stroke:#ef4444,color:#7f1d1d
    class rpc,meter step
    class auth,plan,rate gate
    class req,res ext
    class e401,e402,e429,e503 stop
```

管理端點（自己是誰、用量、API key）不經方案檢查；細節見 [`AGENTS.md`](AGENTS.md) 的 Plans & access。

## 先決條件

| 項目 | 說明 |
|------|------|
| .NET SDK | 版本見 `global.json` |
| PostgreSQL | 版本與建法見根 [`CONTRIBUTING.md`](../../CONTRIBUTING.md) |
| Docker | 測試的 Testcontainers 用 |
| [ontology](../ontology/README.md)、[consult](../consult/README.md) | 啟動時必須設定位址；實際呼叫時才需要在線 |

```bash
dotnet restore && dotnet tool restore
```

## 入口

| 入口 | 型態 | 做什麼 |
|------|------|--------|
| `src/EnvCite.Platform.Api` | 長駐 | ASP.NET Core 主機：REST、後台與入口頁；Development 啟動時自動遷移 |

**設定**：環境變數用 `__` 分層。鍵名、必填與預設值以各設定群組的選項類別與 `appsettings.json` 為準，規則見 [`AGENTS.md`](AGENTS.md) 的 Configuration；選項類別在啟動時驗證，缺值或錯值會讓主機啟動失敗。機密（如營運者金鑰雜湊）只經環境變數，不提交。

### 在本機跑起來

從**本目錄**執行：

```bash
dotnet run --project src/EnvCite.Platform.Api   # Development：連本機 PostgreSQL，自動遷移
```

平行 worktree 用 `wt platform`，port 由分支推導，見 [`CONTRIBUTING.md`](../../CONTRIBUTING.md)。Development 的預設值在 `appsettings.Development.json`。

## 營運者帳號

營運者後台（`/ui`）的帳號不開放自助註冊；除本機 Development 的種子帳號（見 [`CONTRIBUTING.md`](../../CONTRIBUTING.md)）外，一律以營運者金鑰經 `/admin` 建立。

**前提**：該環境已設定 `Platform__AdminKeySha256`（未設定時 `/admin` 停用），且 `/ui` 已開啟。所有 `/admin` 請求帶 `X-Admin-Key`。

**建立**：`POST /admin/operators`，JSON 本文欄位：

| 欄位 | 說明 |
|------|------|
| `email` | 登入帳號 |
| `displayName` | 顯示名稱 |

回應含該帳號資料與**一次性初始密碼**；密碼只回這一次（資料庫只存雜湊，也不進日誌或稽核）。

**規則**：首次登入（`/ui/login`，帳號為 email）先改密碼，再綁定 TOTP 驗證器；順序固定，TOTP 對每位營運者強制。

**端點**：`{operatorId}` 取自列表。

| 端點 | 用途 |
|------|------|
| `GET /admin/operators` | 列出營運者與狀態 |
| `POST /admin/operators` | 建立營運者 |
| `POST /admin/operators/{operatorId}/password-reset` | 重設密碼；回應含新的一次性密碼，之後須再走首次登入 |
| `POST /admin/operators/{operatorId}/two-factor-reset` | 清除驗證器；該營運者下次登入重新綁定 |
| `POST /admin/operators/{operatorId}/disable`、`/enable` | 停用／啟用；下一個請求生效 |

不變式（授權、順序為何固定）見 [`AGENTS.md`](AGENTS.md) 的「營運者帳號」。

## 開發

```bash
dotnet build                         # 警告即錯誤；同時重新產生 openapi_v2.json
dotnet test                          # 需要 Docker
dotnet format --verify-no-changes    # CI 的格式檢查；去掉旗標即套用
docker build -f Dockerfile ../..     # 映像；建置內容為倉庫根
```

- 套件版本集中在 `Directory.Packages.props`，以 `packages.lock.json` 鎖定；改了之後執行 `dotnet restore --force-evaluate` 並提交鎖檔。
- 遷移指令與模組配置見 [`AGENTS.md`](AGENTS.md)。
- 可觀測性：設了 `OTEL_EXPORTER_OTLP_ENDPOINT` 才匯出追蹤與指標。
