---
type: reference
applies-to: all
owns: [operator-accounts]
---

# envcite-platform

多租戶控制平面：租戶、API key、限流、計量，以及對外 REST API（`/v2`）。法規與接地請求經 gRPC 轉給 `services/ontology` 與 `services/consult`。回[倉庫總覽](../../README.md)。

- 介面：`/v2` 租戶 REST API（API key）；`/admin` 營運者 API；`/ui` 營運者後台；`/portal` 客戶入口（以設定開關）。契約 [`openapi/openapi_v2.json`](openapi/openapi_v2.json)（`dotnet build` 產生、隨程式碼提交）；上游 gRPC 契約與外部呼叫端的接法見 [`proto/README.md`](../../proto/README.md)。
- 外部用戶端只經本服務進入系統；上游 [`ontology`](../ontology/README.md)、[`consult`](../consult/README.md)（gRPC，啟動時必須設定位址）。
- 資料：PostgreSQL，schema `platform`；租戶隔離是執行期機制（租戶解析加查詢過濾）。

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

## 入口

`src/EnvCite.Platform.Api`（長駐）：ASP.NET Core 主機，REST、後台與入口頁；Development 啟動時自動遷移。設定的鍵名、必填與預設值以各設定群組的選項類別與 `appsettings.json` 為準，規則見 [`AGENTS.md`](AGENTS.md) 的 Configuration。開發指令見 [`AGENTS.md`](AGENTS.md)。

## 營運者帳號

營運者後台（`/ui`）的帳號不開放自助註冊；除本機 Development 的種子帳號外，一律以營運者金鑰經 `/admin` 建立。

**營運者金鑰**：任意字串，呼叫 `/admin` 時放在 `X-Admin-Key`。服務只持有它的雜湊：

| 設定鍵 | 格式 |
|--------|------|
| `Platform__AdminKeySha256` | 金鑰 UTF-8 位元組的 SHA-256，64 個十六進位字元（大小寫皆可）；空字串即停用 `/admin`，格式不符則啟動失敗 |

**前提**：該環境已設定上述雜湊，且 `/ui` 已開啟。

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
| `POST /admin/operators/{operatorId}/password-reset` | 重設密碼；回應含新的一次性密碼，之後須再走首次登入 |
| `POST /admin/operators/{operatorId}/two-factor-reset` | 清除驗證器；該營運者下次登入重新綁定 |
| `POST /admin/operators/{operatorId}/disable`、`/enable` | 停用／啟用；下一個請求生效 |

不變式（授權、順序為何固定）見 [`AGENTS.md`](AGENTS.md) 的「營運者帳號」。
