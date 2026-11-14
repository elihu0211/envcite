<!--
type: reference
applies-to: all
owns: [operator-accounts, mail-invitation, reasoning-rest, mcp-endpoint, corpus-scope, brand-name, service-logging, tenant-access-urls, appearance-setting, tenant-directory-sync, portal-google-signin, conversation-history]
-->

# envcite-platform

多租戶控制平面：租戶、API key、限流、計量，以及對外 REST API（`/v2`）。法規與接地請求經 gRPC 轉給 `services/ontology` 與 `services/consult`。回[倉庫總覽](../../README.md)。

- 介面：`/v2` 租戶 REST API（API key）；`/ui` 營運者後台；`/portal` 客戶入口（以設定開關）。契約 [`openapi/openapi_v2.json`](openapi/openapi_v2.json)（`dotnet build` 產生、隨程式碼提交）；上游 gRPC 契約與外部呼叫端的接法見 [`proto/README.md`](../../proto/README.md)。
- 外部用戶端只經本服務進入系統；上游 [`ontology`](../ontology/README.md)、[`consult`](../consult/README.md)（gRPC，啟動時必須設定位址）。
- 資料：PostgreSQL，schema `platform`；租戶隔離是執行期機制（租戶解析加查詢過濾）。

## 概念

| 組 | 節 | 內容 |
|----|----|------|
| 對外 REST API | [語料範圍](#語料範圍) | 只涵蓋中央法規；拒答原因的解讀 |
| 對外 REST API | [一次 `/v2` 請求](#一次-v2-請求) | 驗證、方案、限流、計量，以及失敗時的狀態碼 |
| 對外 REST API | [推論](#推論post-v2reasoningevaluate) | `POST /v2/reasoning/evaluate` 的欄位對映 |
| 入口與介面 | [入口](#入口) | 主機，以及設定鍵的真值在哪 |
| 入口與介面 | [租戶頁的存取網址](#租戶頁的存取網址) | 複製登入網址與 API Base URL |
| 入口與介面 | [個人設定與外觀](#個人設定與外觀) | 系統／亮色／暗色，兩個面共用一個元件 |
| 入口與介面 | [對話歷史與接續](#對話歷史與接續) | 側欄的對話、釘選、重新命名、封存、分叉、刪除、搜尋，以及接續時帶進模型的前文 |
| 帳號與存取 | [營運者帳號](#營運者帳號) | 欄位、命令列、首次登入規則 |
| 帳號與存取 | [密碼規則](#密碼規則) | 營運者與入口成員共用的密碼檢查 |
| 帳號與存取 | [邀請與信箱驗證](#邀請與信箱驗證) | 邀請連結、暫時密碼、重寄 |
| 帳號與存取 | [目錄同步](#目錄同步platformdirectory) | 身分來源把成員推進租戶：設定鍵、規則、群組、SCIM 端點 |
| 設定與運維 | [日誌](#日誌) | 日誌等級、EF Core SQL、如何看 SQL |
| 設定與運維 | [系統名稱](#系統名稱platformbrand) | 名稱的唯一來源、哪些地方跟著變 |
| 設定與運維 | [寄信](#寄信platformmail) | 設定鍵、加密與帳密、信件內容、測試與排查 |

## 對外 REST API

### 語料範圍

`/v2` 的法規、搜尋、接地與推論只涵蓋**中央法規**；**地方自治法規（自治條例、自治規則等）不在範圍內**。這是產品範圍，不是語料的缺口：

- **問句明白在問自治法規**：`POST /v2/ground` 的問句含「自治條例」「自治法規」「自治規則」「地方加嚴」之一（詞表真值在 consult 的 `LocalMarker.cs`）且沒有達標的條文時，`refusal.reason` 為 `local_law_uncovered`；呼叫端要告知使用者語料只涵蓋中央法規、請另查該地方政府的法規。有達標的條文時照常回證據，但不可當成地方法規的答案。
- **其餘情形無法與「查無」區分**：問句沒有上述字樣（例如只點名縣市）時，`POST /v2/search` 無結果、`POST /v2/ground` 的 `no_authority`／`out_of_scope`、`GET /v2/laws/…` 的 404，都不代表地方法規沒有規定；呼叫端不得暗示「地方沒有相關規定」。
- **識別碼格式不代表語料範圍**：`jurisdiction`、`idScheme` 的合法值含縣市管轄與地方方案（見 [`ontology.proto`](../../proto/envcite/ontology/v2/ontology.proto) 的 `LawIdentifier`），那是契約預留；帶縣市管轄的識別碼與任何不在語料的識別碼一樣查無。
- **`out_of_scope` 不是語料範圍外**：指檢索到的條文都未達相關度門檻；拒答語意見 [`proto/README.md`](../../proto/README.md) 的〈呼叫端的語言模型〉。

對外規格 [`openapi/openapi_v2.json`](openapi/openapi_v2.json) 的 `info.description` 帶同一句範圍聲明（來源 `PlatformOpenApi.cs`）。

### 一次 `/v2` 請求

```mermaid
---
config:
  flowchart:
    curve: basis
---
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

### 推論（`POST /v2/reasoning/evaluate`）

案件事實放請求本文，回應是 `atDecision`（裁處時之法律）與 `earlier`（其間不同的法律組）。欄位與型別以 [`openapi/openapi_v2.json`](openapi/openapi_v2.json) 為準；推論語意（何時附條件、何時不列出）見 [`proto/README.md`](../../proto/README.md) 的「標註與推論」。REST 與 gRPC 的對映規則：

- 欄位名是 gRPC 欄位的 camelCase；列舉是去掉前綴的 PascalCase 名稱（`DELEGATION_GAP_NO_CITATION` → `NoCitation`），不是數字。
- 請求裡省略的是「未知」：`administrativePenalty` 的是／否、金額與日期欄位省略即不送出，不當作否或 0。金額、時數、工資與所得利益是十進位文字。
- 回應裡沒有的是 `null`，不是空字串或 0：額度缺（`minAmount`、`maxAmount`）、授權環節的引文與對到的法規（`citedLawName`、`citedPath`、`identifier`、`uri`）。清單永遠存在，沒有就是空的。
- 授權環節的 `gap` 為 `null` 表示這一環成立（gRPC 的零值）。

| REST 欄位 | 說明 |
|-----------|------|
| `invalidated[]`：`statementId`、`uri`、`rulingNo`、`invalidFrom`、`partial` | 違憲失效而不參與推論的陳述；`verdicts` 少了某則先看這裡 |
| `delegationBases[]`：`statementId`、`rooted`、`links[]`（`commandIdentifier`、`citedLawName`、`citedPath`、`identifier`、`uri`、`gap`；兩個 identifier 是 `{jurisdiction, idScheme, localId}` 物件） | 命令陳述往上到法律的授權鏈；`rooted` 為假時相關罰則附條件 |
| `general.reduced[]`：`penaltyStatementId`、`mitigation`、`share`（`Half`／`Third`）、`minAmount`、`maxAmount` | 行政罰法第 18 條第 3 項減輕後的範圍 |
| `general.raisedCaps[]`：`penaltyStatementId`、`maxAmount` | 第 18 條第 2 項依所得利益加重的上限 |
| `general.criminalDeductions[]`：`penaltyStatementId`、`amount` | 第 26 條第 3、4 項的扣抵額 |
| `general.unpunished[].excuse` 的 `CriminalPriority` | 第 26 條第 1 項刑事優先，罰鍰不列出 |
| `problems[].kind` 的 `InvalidatedDelegation`（`conditionId`） | 授權的限值所在條文已違憲失效，要件為未知 |
| 請求 `administrativePenalty`：`criminalOffense`、`courtConfiscated`、`criminalClearedOn`、`criminalPayment`、`communityServiceHours`、`minimumHourlyWage`、`gainedBenefit` | 第 18、26 條所依的事實 |

### AI 代理的入口（MCP，`/v2/mcp`）

同一批資料能力的第二個入口，給會自己呼叫工具的 AI 代理（Claude Code、Cursor、VS Code 等）。協定是 [MCP](https://modelcontextprotocol.io) 的 Streamable HTTP，**無狀態**：每個請求獨立，沒有 session 放在某個副本的記憶體裡，多副本之間不需要連線黏著。工具是 REST 資料端點的包裝，共用同一份實作，所以輸入檢查、錯誤與計量與 REST 相同。

- **身分與額度與 REST 同一道門**：`Authorization: Bearer <API key>`；沒有 key 是 401，沒有方案是 402；每租戶限流與 `/v2` 其餘端點共用同一個額度，握手（`initialize`）、列舉與每次工具呼叫各算一個請求。不實作 OAuth 授權伺服器，所以只有能設定標頭的用戶端（開發者工具）接得上。
- **預設關閉**（`Platform:Mcp:Enabled`），沒開就不掛；不在 [`openapi/openapi_v2.json`](openapi/openapi_v2.json)，因為它是另一個協定。
- **`Origin` 檢查**（MCP 規範要求，防 DNS rebinding）：請求帶了 `Origin` 而不在 `Platform:Mcp:AllowedOrigins` 就回 403，而且在驗證之前；CLI 與桌面用戶端不送 `Origin`，不受影響。清單要填成 `https://host`，不含路徑與結尾斜線，啟動時驗證。
- **工具**全部唯讀、確定性，宣告 `readOnlyHint`、`idempotentHint` 與輸出結構；成功才計量，單位同 REST。查詢文字與案件事實不進任何日誌。

| 工具 | 對應 REST | 計量 |
|------|-----------|------|
| `search_articles`（`query`、`pointInTime`、`limit`） | `POST /v2/search` | `ontology.search` |
| `ground_question`（`question`、`pointInTime`） | `POST /v2/ground` | `consult.ground`（拒答也計一次） |
| `get_law`（`jurisdiction`、`idScheme`、`localId`、`pointInTime`） | `GET /v2/laws/{…}` | `ontology.get_law` |
| `list_articles`（同上） | `GET /v2/laws/{…}/articles` | `ontology.list_articles` |
| `evaluate_reasoning`（`request`：同 `POST /v2/reasoning/evaluate` 的本文） | `POST /v2/reasoning/evaluate` | `reasoning.evaluate` |

- **資源**：`envcite://laws/{jurisdiction}/{idScheme}/{localId}`，一部法規現行的標題、基本資料與各條清單（JSON）；讀一次等於 `get_law` 加 `list_articles`，各計量一次。**提示**：`grounded_answer`（`question`、`pointInTime`），把「先 `ground_question`、只根據法源作答、以代號標明出處、證據不足就明說」交給用戶端的模型。
- **錯誤**：預期的失敗（輸入錯誤、找不到、內部服務暫時不可用）是工具錯誤（`isError`），訊息是英文的穩定說明，不含內部細節；拒答與空結果是結果，不是錯誤。
- **接上用戶端**：Claude Code 為 `claude mcp add --transport http envcite https://<主機>/v2/mcp --header "Authorization: Bearer <API key>"`；其他用戶端在 MCP 伺服器設定裡填網址與同一個標頭。

## 入口與介面

### 入口

`src/EnvCite.Platform.Api`（長駐）：ASP.NET Core 主機，REST、後台與入口頁；Development 啟動時自動遷移。設定的鍵名、必填與預設值以各設定群組的選項類別與 `appsettings.json` 為準，規則與開發指令見 [`AGENTS.md`](AGENTS.md) 的 Configuration 與 Commands。

### 租戶頁的存取網址

租戶頁（`/ui/tenants/{租戶}`）頁首下方身分列的右側是「存取網址」：兩顆複製按鈕，複製完整網址、不在畫面上列出；不屬於任何分頁，有沒有成員或 API key 都顯示。

| 按鈕 | 複製的網址 |
|------|-----------|
| 複製登入網址 | `https://<主機>/portal/<租戶識別碼>/login`（識別碼經百分比編碼） |
| 複製 API Base URL | `https://<主機>/v2` |

主機取自目前請求的來源（scheme 與主機；Gateway 之後由 forwarded headers 還原），沒有對應的設定鍵：後台、入口與 API 同一個主機。`Platform:Mail:BaseUrl` 只管信裡的連結，不是通用的對外網址。複製元件的用法見 [`AGENTS.md`](AGENTS.md)「設計系統與營運者後台」。

### 個人設定與外觀

登入後的「個人設定」放使用者自己的偏好：外觀可選**系統**（預設，跟隨裝置）、**亮色**、**暗色**。營運者後台與客戶入口各有一頁，共用 `Shared/AppearanceSetting` 元件：

| 面 | 頁面 | 設定外觀的端點 |
|----|------|----------------|
| 營運者後台 | `GET /ui/settings` | `POST /ui/appearance` |
| 客戶入口 | `GET /portal/{租戶}/settings` | `POST /portal/{租戶}/appearance` |

- **預設**：沒有偏好時 `<html>` 不帶 `data-theme`，外觀只靠 `prefers-color-scheme`。
- **偏好存放**：這個瀏覽器的 cookie（`ep-appearance`，值 `light` 或 `dark`；名稱、有效期與屬性在 `AppearanceOptions.Cookie`），路徑 `/`，所以營運者後台、客戶入口與登入前的頁面看到同一個偏好。不跨裝置、不分帳號、不進資料庫。選回「系統」是刪掉 cookie；cookie 不存在、被清掉或值不合法一律視為「系統」。
- **端點**：表單欄位 `appearance`（`system`、`light`、`dark`）與 `returnUrl`；需要 antiforgery，不需要登入。值不合法回 400、不寫 cookie。一般表單送出導回 `returnUrl`（只准留在該面之下）；`Accept: application/json` 的請求（個人設定頁的腳本）回 204。
- **匿名頁面**（登入、啟用、重設、已登出、無權限）不提供切換，但依 cookie 輸出偏好。
- 設計規則見 [`AGENTS.md`](AGENTS.md)「設計系統與營運者後台」。

### 對話歷史與接續

客戶入口的問答可以存成對話，之後從側欄點回去**接續**：還原先前的問答，再問時前文一併帶進模型。一律存在、沒有開關；問題文字是客戶案情，所以只有作者看得到自己的對話（同租戶的擁有者也不行），並依 `Platform:ConversationHistory:RetentionDays` 自動刪除。

| 動作 | 入口 | 說明 |
|------|------|------|
| 開啟並接續 | `GET /portal/{租戶}/c/{id}` | 工作區；不是自己的、不存在、已過期、沒開啟，都是同一個「找不到」畫面 |
| 列表與搜尋 | `GET /portal/{租戶}/history?q=` | 只比對標題（不分大小寫，`%` 與 `_` 當文字），不比對內容 |
| 釘選／取消 | `POST /portal/{租戶}/c/{id}/pin` | 表單欄位 `pinned`（`true`、`false`） |
| 重新命名 | `POST /portal/{租戶}/c/{id}/rename` | 表單欄位 `title`；去頭尾空白、換行與控制字元合成空白、截到 80 字，空白不接受 |
| 封存／取消 | `POST /portal/{租戶}/c/{id}/archive` | 表單欄位 `archived`（`true`、`false`）；封存的不在側欄與預設列表，只在已封存檢視 |
| 已封存檢視 | `GET /portal/{租戶}/history?view=archived` | 封存得晚的在前；可用 `q` 搜尋標題；`view` 是其他值就是預設列表 |
| 分叉 | `POST /portal/{租戶}/c/{id}/fork` | 表單欄位 `title`（同重新命名的正規化，空白不複製）；整串複製成新的一串並前往它 |
| 刪除 | `POST /portal/{租戶}/c/{id}/delete` | 連同訊息一起刪，稽核只記 Id；介面先確認 |

- **端點**：需要入口登入與 antiforgery；做完導回送出表單的那一頁（Referer，只准留在該租戶入口之下），刪除或封存的正是正在看的那串就回首頁；分叉成功則前往新的那一串。不是自己的、不存在的結果相同（只是導回），不洩漏它存不存在。選單在側欄與列表頁共用，是原生 popover 加普通表單，沒有 JavaScript 也能用。
- **排序**：釘選的在側欄最上面（釘得晚的在前，筆數有上限），其餘依最後更新、依台北時間的日曆日分組。釘選、重新命名與封存都不算新的活動：不改最後更新時間，所以不改排序、也不延長保存；超過保留天數的對話連同釘選、封存的一起刪除（`Platform:ConversationHistory:RetentionDays`）。
- **封存**：封存時一併取消釘選，取消封存不會把釘選帶回來；封存的對話網址仍可開啟，在裡面接著問是新的活動，會自動取消封存。
- **分叉**：整串複製（訊息、引用、問的是哪一天的條文與順序），之後兩串各自發展；新的一串不帶釘選與封存，最後更新時間是現在，保存期限從現在起算。複製的是自己的對話，不經過任何人，所以不另記稽核。
- **存什麼**：問題（連同問的是哪一天的條文）、回答正文，以及引用（代號、法規名、條文網址）與結果種類（回答、拒答、只列條文）；不存條文本文，全文隨時可由網址再查。
- **前文**：接續時，前面有結果的回合以對話的形式（使用者的問題、助理的回答）帶進模型。回答裡的代號換成法規名——代號只在那一輪的證據集裡有意義；拒答的回合只留問題。前文**不是證據**：回答仍只能引用這一輪證據集的代號，核驗不變（見 [`AGENTS.md`](AGENTS.md)「有依據的回答與對話紀錄」）。有前文的提問會先把問題改寫成不看前文也看得懂的一句，用它取證據；改寫失敗就用原句。帶幾輪、每則多長由 `Platform:Answer:HistoryTurns` 與 `Platform:Answer:HistoryChars` 決定，鍵的範圍與預設值以 `AnswerOptions` 為準。
- **計量**：一次提問仍是一次作答；改寫用掉的 token 與作答一起記在同樣兩個 token 操作上，不另算次數。

## 帳號與存取

### 營運者帳號

營運者後台（`/ui`）的帳號不開放自助註冊。除本機 Development 的種子帳號外，第一位營運者以命令列建立，之後的營運者在後台 `/ui/operators` 新增、停用、重設。本機順序見 [`CONTRIBUTING.md`「本機第一個帳號」](../../CONTRIBUTING.md#本機第一個帳號)，staging 的步驟見[`索引`](../../CONTRIBUTING.md#索引)。

**前提**：`/ui` 已開啟。

**登入**：以 email 登入（`/ui/login`）。TOTP 對每位營運者強制，順序一律是「先有只有本人知道的密碼，再綁定驗證器」：

- 經[邀請](#邀請與信箱驗證)建立：密碼自己設，登入後直接綁定。
- 用暫時密碼建立：首次登入先改密碼再綁定。

**後台**：`/ui/operators` 列出營運者與狀態（含「待啟用」「信箱未驗證」），可新增（啟用寄信時是邀請）、重寄邀請或驗證信、重設密碼、重設驗證器、停用與啟用；停用在下一個請求生效。營運者不能對自己的那一列操作。

**命令列**：在服務自己的環境裡執行（資料庫連線與設定取自該環境），不啟動 HTTP 伺服器，跑完就結束。以服務的進入點執行，例如 `dotnet EnvCite.Platform.Api.dll operator create a@example.com`。

- 一次性初始密碼只印在標準輸出，不進日誌或稽核；稽核的操作者記為 `system`。
- 主控台除了命令自己的訊息，只剩 Warning 以上的日誌（SQL 預設不印，見[日誌](#日誌)）。

| 指令 | 用途 |
|------|------|
| `operator create <email> [顯示名稱]` | 建立營運者。已啟用寄信時寄邀請信（不產生密碼，也不印連結）；否則印出一次性初始密碼 |
| `operator resend-invite <email>` | 重寄邀請信（先前的連結失效）；已有密碼但信箱未驗證的則寄驗證信 |
| `operator reset-password <email>` | 重設密碼，印出新的一次性密碼（寄信失敗或收不到信時的退路） |
| `operator reset-two-factor <email>` | 清除驗證器；對方下次登入重新綁定 |
| `mail test <email>` | 用目前的 `Platform:Mail` 設定寄一封測試信，確認中繼接受；見[測試與排查](#測試與排查) |
| `mail preview <輸出目錄>` | 把信件版型（邀請、驗證、測試信）寫成 `.html` 與 `.txt`，連結是占位值；**不寄信、不連資料庫**，不需要啟用 `Platform:Mail`；見[預覽](#信件內容) |

結束碼：`0` 成功、`1` 操作失敗（帳號已存在、找不到營運者、帳號已建立但邀請信沒寄出，或測試信沒寄成）、`2` 用法錯誤。

授權與順序為何固定等不變式見 [`AGENTS.md`](AGENTS.md) 的「營運者帳號」。

### 密碼規則

營運者（`/ui`）與客戶入口的成員適用同一套，在設定與修改密碼時檢查（邀請啟用、改密碼；營運者經手的一次性密碼也經同一個驗證器）。依 NIST SP 800-63B：**長度比組成重要**，只要求夠長、不太容易被猜到；不強制大小寫、數字或符號，不要求定期換密碼，任何字元（含空白與中文）都可以。

| 規則 | 說明 | 失敗代碼（`OutcomeCodes`） |
|------|------|--------------------------|
| 最短與最長長度 | 數字在 `PasswordPolicy`（`SharedKernel`）；以 UTF-16 字元數計，與瀏覽器的 `length` 同一種算法 | `password.too_short`、`password.too_long` |
| 不含自己的 email | 完整 email 與名稱部分（名稱太短時不比對）；不分大小寫 | `password.contains_account_info` |
| 不太容易被猜到 | 強度估算（[zxcvbn](https://github.com/dropbox/zxcvbn)，分數 0 到 4）達 `PasswordPolicy` 的最低分數：擋常見密碼、鍵盤順序、連續或重複字元、「大寫加數字加驚嘆號」這類套路；帳號資料也算已知的猜測素材 | `password.too_guessable` |
| 與目前的密碼不同 | 只在修改時 | `password.same_as_current` |

失敗的訊息依代碼取使用者的語言並說明原因；連結仍有效、可以重打，密碼不寫回表單。連續失敗鎖定帳號（見 [`AGENTS.md`](AGENTS.md)）。

**提示**：設定與修改密碼的頁面（啟用、改密碼；後台與入口）共用 `NewPasswordField` 模板，數字取自 `PasswordPolicy`。列出長度、不含 email、避開常見與連續重複字元三條，並說明不必混用大小寫或符號；有 JavaScript 時長度即時回饋，其餘由伺服器檢查並說明原因。

### 邀請與信箱驗證

登入名稱是 email（不分大小寫；營運者全域唯一，入口成員在租戶內唯一）。`EmailConfirmed` 表示信箱歸屬已由連結證明。

建立帳號有兩條路，由 `Platform:Mail:Enabled` 決定：

| | 啟用寄信（邀請） | 沒啟用寄信（暫時密碼） |
|---|---|---|
| 建立後的帳號 | 沒有密碼，信箱未驗證 | 有一次性初始密碼，信箱未驗證 |
| 交給對方的 | 一封信，內有單次、限時的連結；**信裡沒有密碼** | 操作者親手交付的一次性密碼 |
| 對方第一次要做 | 開啟連結，設定自己的密碼；信箱同時驗證 | 用該密碼登入，立刻改掉 |
| 營運者之後 | 登入，綁定 TOTP 驗證器 | 改密碼，再綁定 TOTP 驗證器 |
| 信箱驗證 | 開啟連結即完成 | 另寄驗證信（開啟連結只確認信箱） |

```mermaid
sequenceDiagram
    participant Op as 操作者
    participant P as platform
    participant R as SMTP 中繼
    participant U as 被邀請者
    Op->>P: 新增營運者或成員（email）
    P->>P: 建立沒有密碼的帳號；權杖由 Identity 即時產生，不存
    P->>R: 邀請信（連結，不含密碼）
    R-->>U: 投遞
    U->>P: GET /activate?userId&code（只檢視，不用掉連結）
    U->>P: POST 新密碼
    P->>P: 設定密碼、標記信箱已驗證；連結隨安全戳記失效
    U->>P: 登入（營運者再綁定驗證器）
```

連結的網址為 `<BaseUrl>/ui/activate?userId=<帳號 Id>&code=<權杖>`（營運者）或 `<BaseUrl>/portal/<租戶>/activate?userId=…&code=…`（成員）。規則：

- 只是開啟網頁不會用掉連結（信件掃描器會預先開啟），送出表單才會；設好密碼後連結立即失效。
- 連結無效、過期、已用過、租戶不符、帳號被停用或不存在，一律是同一個結果（`activation.link_invalid`），不透露帳號是否存在。
- 密碼不符規則時連結仍有效，可以重打。啟用不簽發 session：使用者接著照常登入。
- 清單上「待啟用」或「信箱未驗證」的帳號可重寄：還沒有密碼的重發邀請，先前的連結隨之失效；已有密碼的寄驗證連結，開啟後只確認信箱。重設密碼（暫時密碼）任何時候都能用，寄信失敗或收不到信時用它。
- 登入不要求信箱已驗證；信箱驗證前不得作為復原管道。

### Google 登入（`Platform:Portal:Google`）

客戶入口的另一種登入方式：成員用自己的 Google 帳號（Gmail 或 Google Workspace）登入，不必另記密碼。可選功能，**預設關閉**；關閉時登入頁沒有按鈕、相關路徑一律 404。環境變數寫成 `ENVCITE_Platform__Portal__Google__<鍵>`；`ClientSecret` 只經環境變數。啟用卻缺 `ClientId` 或 `ClientSecret`，啟動就停下並指名鍵。

- **全站一個 Google 用戶端**，不是每租戶一組。Google Cloud 端：OAuth 用戶端類型「網頁應用程式」；已授權的重新導向 URI 是**對外來源加 `/signin-google`**（每個環境各登記一個，Google 要求完全相同）；同意畫面發布為正式，範圍只要 `openid`、`email`、`profile`（基本範圍不必經 Google 審查）。
- **流程**：登入頁的按鈕 POST 到 `/portal/{租戶}/login/google`（antiforgery），導去 Google（授權碼加 PKCE，每次請使用者選帳號）；Google 回到全站固定的 `/signin-google`，再導到 `/portal/{租戶}/login/google/callback` 完成登入。租戶隨受保護的 state 與暫存 cookie 帶著，回呼時與路徑上的租戶不符就拒絕。登入後去 `returnUrl`（只准留在該租戶入口之下），沒有則首頁。
- **只登入既有成員，不建立帳號**。首次以 Google 已驗證的 email 對到這個租戶裡已存在、**已啟用**（設過自己的密碼、沒有待改的初始密碼）的成員，並連結該 Google 帳號的 `sub`；之後以 `sub` 為準，email 變了也是同一位成員。還在邀請中的人先用信裡的連結啟用。停用的成員登不進。同一個 Google 帳號可以是不同租戶的成員（各連結一次）。
- **失敗只有一句話**（登入頁帶 `google=failed`）：email 未驗證、找不到成員、尚未啟用、已停用、取消授權、流程不屬於這個租戶，結果相同；稽核（`portal.sign_in_failed`，`via=google`）只留原因與成員 Id，不留 Google 給的 email。
- **兩步驟驗證與鎖定照舊**：Google 不告知對方有沒有做過 2FA，所以成員啟用了兩步驟驗證仍要走第二步；被要求註冊的角色照常被導去註冊。成功與連結各留稽核（`portal.signed_in` 帶 `via=google`、`portal.google_linked`）。登出只結束本站的 session，不登出 Google。

### 目錄同步（`Platform:Directory`）

客戶的身分來源（IdP）把成員推進自己的租戶。可選功能，**預設關閉**：部署者開了功能面，租戶的擁有者還要自己開連線才生效；關閉時一律「不存在」（`directory.not_enabled`，結果是 NotFound），既有成員不受影響。環境變數寫成 `ENVCITE_Platform__Directory__<鍵>`；錯值在啟動就停下並指名鍵。

| 鍵 | 說明 |
|----|------|
| `Enabled` | 功能面總開關 |
| `MaxPageSize` | 列出成員時一頁最多幾筆（1–1000） |
| `RequestsPerMinute` | 每個 token 每分鐘最多幾次請求（1–10000） |
| `StaleAfter` | 多久沒收到身分來源的請求，介面警示連線可能斷了（至少一分鐘） |

租戶自己的設定是資料，不是設定鍵：是否開啟、推送沒指定角色時的預設角色、是否接管同 email 的既有成員。

#### 規則

服務層的行為，端點與介面依此呈現。

| 項目 | 規則 |
|------|------|
| 比對 | 以身分來源給的 `ExternalId`（租戶內唯一）為首要鍵，email 次之；email 不能由同步變更 |
| 重複建立 | 同 `ExternalId` 或同 email 是衝突，不建第二筆；重送與逾時重試因此安全 |
| 新成員 | 走邀請信（沒有密碼，開連結自己設定），所以需要寄信啟用；停用狀態的成員不需要信，重新啟用時才寄 |
| 停用 | `active=false` 與刪除都是停用：帳號與稽核保留，所有 session 在下一個請求失效；重送得到同一個結果 |
| 角色 | 同步最高只能授予管理員；擁有者只由人指派。最後一位啟用中的擁有者不能被同步停用或降級。成員的角色來自群組對映，沒有就是租戶的預設角色（見[群組](#群組)） |
| 既有成員 | 同 email 的手動成員預設不動；租戶開啟「接管」才連結（只連結，角色與停用維持原樣），擁有者永遠不接管 |
| 受管成員 | 由同步建立或接管的成員，其角色與停用在介面與管理動作上鎖住（`directory.member_managed`）；擁有者或營運者可「解除管理」，之後回到人手上 |
| 管理 | 設定與 token 只有租戶的擁有者能管：token 能讓身分來源建立管理員，管理員不能建立管理員，所以也不能持有它。營運者只能看狀態、緊急關閉連線、解除成員的管理 |
| token | 格式 `scim_{前綴}_{secret}`，只存雜湊、只在簽發時顯示一次；與 API key（`sk_live_`）互不通用 |
| 稽核 | 「誰」是 token（`directory` 類型，記 token 的 Id）；稽核裡沒有 token、連結或身分來源送來的屬性。變更與稽核同一個交易，稽核寫不進去就沒有變更 |

#### 端點（`/scim/v2`，SCIM 2.0）

協定是 RFC 7643／7644，本節只記本服務的選擇。不在 `/v2` 之下：不計量、不走方案與逐租戶限流，也不進 `openapi_v2.json`（規格是 RFC 本身，能力由 `/ServiceProviderConfig` 自述）。部署者沒開功能面就不掛路徑。

| 項目 | 規則 |
|------|------|
| 認證 | `Authorization: Bearer scim_…`；租戶只來自 token。失敗一律同一個 401（不透露 token 是不存在、已撤銷、已過期或密碼錯），帶 `WWW-Authenticate: Bearer realm="scim"`。API key 不能用在這裡，這種 token 也不能用在 `/v2` |
| 內容型別 | 回應是 `application/scim+json`；請求的 `application/json` 與 `application/scim+json` 都收，本文上限 256 KB |
| `GET /ServiceProviderConfig`、`/ResourceTypes`、`/Schemas`（及 `/{id}`） | 自述：支援 PATCH 與過濾；不支援 Bulk、排序、ETag、改密碼 |
| `GET /Users` | 過濾只支援 `userName eq "…"` 與 `externalId eq "…"`（屬性與運算子不分大小寫），其他回 400 `invalidFilter`；`startIndex` 從 1、`count` 以 `MaxPageSize` 為上限，`count=0` 只回總數；包含手動建立的成員 |
| `POST /Users` | 201 加 `Location`；重複回 409 `uniqueness`。`userName` 是 email（不是 email 時退用 `emails` 的主要位址）；`roles` 不經 SCIM 指定，角色用租戶設定的預設 |
| `GET`／`PUT`／`PATCH`／`DELETE /Users/{id}` | PUT 是取代（沒送 `displayName` 視為清掉、沒送 `active` 視為啟用）；PATCH 接受 `add`／`replace`／`remove`、有或沒有 path（Entra、Okta 的寫法），`active` 可為布林或 `"True"`／`"False"`，不認得的屬性略過；DELETE 是停用，204，重送仍 204；`userName` 不能變更（400 `mutability`） |
| `GET`／`POST /Groups`、`GET`／`PUT`／`PATCH`／`DELETE /Groups/{id}` | 見[群組](#群組)。過濾只支援 `displayName eq "…"`（不分大小寫）與 `externalId eq "…"`；POST 201 加 `Location`，名稱重複回 409 `uniqueness`；DELETE 204，重送回 404 |
| 其他路徑 | Bulk 等回 SCIM 格式的 404（已驗證後才回，沒有 token 一律 401） |
| 錯誤 | RFC 7644 §3.12 的格式（`schemas`、`status`、`scimType`、`detail`）加本倉庫的穩定 `code`（`OutcomeCodes`）；`detail` 永遠是英文。連線關閉：404 `directory.not_enabled`；新成員需要寄信但沒啟用：503 `mail.not_enabled`；超過額度：429 加 `Retry-After` |
| 限流 | 每把 token 每分鐘 `RequestsPerMinute` 次（授權之後才算，沒有有效 token 的請求不耗用額度）；狀態在各副本的記憶體裡 |
| 指標 | `envcite.platform.directory.requests`，標籤只有 `operation` 與 `outcome`；租戶與內容不進指標 |

#### 群組

身分來源推送群組與成員（SCIM Groups），**客戶的擁有者**在入口決定每個群組給什麼角色。群組本身不改任何人的角色，要有對映才算。

| 項目 | 規則 |
|------|------|
| 成員 | 必須是這個租戶裡由目錄同步管理的成員，否則整個請求回 400 `invalidValue`（`directory.group_member_invalid`），不留半個群組；別的租戶的 Id 算不存在 |
| 重算 | 受管成員的角色 = 他所屬、有對映的群組裡**最高**的角色（管理員 > 開發者 > 帳務 > 唯讀）；一個有對映的群組都沒有，就是租戶的預設角色。群組的成員變動、群組被刪、或擁有者改了對映時，對受影響的成員重算，改了角色的人各有一筆稽核（`member.attribute_synced`，`via` 是 `group`），舊 session 失效 |
| 例外 | 擁有者與已解除管理的成員不受影響；租戶的預設角色改了不會回頭重算既有成員（預設角色只用於新建立的成員，以及不再屬於任何有對映群組的成員） |
| 對映 | 只能是同步可授予的角色（最高到管理員）或「不對映」；只有擁有者能設，群組由身分來源推送、擁有者不能在這裡建立或刪除 |
| 唯一性 | 群組名稱在租戶內唯一（不分大小寫、去前後空白），外部識別碼也是；別的租戶可以用同樣的名稱 |
| PATCH | `members` 的 `add`／`remove`／`replace`，`remove` 可用 `members[value eq "id"]`（Entra）或把 Id 放在 `value`（Okta），不在群組裡的移除略過所以重送安全；`displayName`、`externalId` 可改；沒有變化什麼都不寫 |
| 稽核 | `directory.group_created`／`group_updated`／`group_deleted`（名稱與人數，不記成員清單）、`directory.group_role_mapped`（誰設的、前後的角色、影響幾位） |

#### 介面

| 位置 | 內容 | 誰 |
|------|------|----|
| 客戶入口 `/portal/{租戶}/directory` | 連線狀態（是否運作、最後收到請求、同步次數、被拒絕數）、SCIM Base URL、token 清單與簽發撤銷（新 token 只顯示一次，之後只看得到前綴）、設定（接受與否、預設角色、接管既有成員）、群組表與每個群組的角色對映 | 只有擁有者；導覽列也只列給擁有者 |
| 客戶入口的成員頁 | 受管成員有標記，角色與停用的控制項收起，改列「解除管理」 | 解除管理只有擁有者 |
| 營運者後台的租戶頁 | 同一張唯讀狀態卡加「關閉目錄同步」（緊急關閉，冪等，需確認）；成員表同樣標記受管成員並可「解除管理」 | 營運者 |

部署者沒開功能面（`Platform:Directory:Enabled`）時：導覽列與營運者頁不列，入口頁只說明這個環境沒有開啟。連線指示：沒開是「已關閉」，開了但從沒收到請求是「等待身分來源連線」，超過 `StaleAfter` 沒收到是「久未收到請求」。

## 設定與運維

### 日誌

- **等級**：標準的 `Logging:LogLevel`，真值在 [`appsettings.json`](src/EnvCite.Platform.Api/appsettings.json)。
- **EF Core 的 SQL**：每條 SQL 是類別 `Microsoft.EntityFrameworkCore.Database.Command` 的 Information，預設把這個類別調到 Warning，[命令列](#營運者帳號)的結果與 `kubectl logs` 才不被 SQL 蓋掉；失敗的指令（Error）仍會記。只調這一個類別：遷移進度（`Microsoft.EntityFrameworkCore.Migrations`）、連線錯誤等其他 EF 日誌照一般等級。SQL 的參數值 EF 預設遮蔽為 `?`，本服務沒有開 `EnableSensitiveDataLogging`。
- **看 SQL**：用環境變數把該類別調回 Information，不改檔案提交：

```bash
env 'ENVCITE_Logging__LogLevel__Microsoft.EntityFrameworkCore.Database.Command=Information' <啟動指令>
```

類別名稱裡的點照原樣寫。shell 的 `export` 不接受含點的名稱，所以用 `env`；k8s 的環境變數名稱允許點。

### 系統名稱（`Platform:Brand`）

系統名稱只有一個來源：`Platform:Brand:Name`（環境變數 `ENVCITE_Platform__Brand__Name`）。沒設就是預設名稱（`BrandOptions.DefaultName`）；不可空白、不含換行等控制字元、最多 25 個字（寄件者顯示名稱在手機信件清單上會被截斷）。錯值在啟動就停下並指名鍵。

| 地方 | 跟著設定變 |
|------|-----------|
| 後台與入口的標頭、頁面標題（`<title>`）、復原碼的列印標題 | 是。預設名稱在英文資源檔有對照；改了名稱，兩種語言都原樣顯示新名稱，不留英文舊名 |
| 邀請與驗證信、命令列 `mail test` 的主旨與內文（純文字與 HTML 兩版，HTML 的標頭、標題與頁尾也是） | 是 |
| 寄件者顯示名稱 | `Platform:Mail:FromName` 有設以它為準；沒設（或空白）用系統名稱 |
| OpenAPI 描述（`/openapi/v2.json`，僅 Development 提供） | 是；提交的 [`openapi/openapi_v2.json`](openapi/openapi_v2.json) 是預設名稱下產生的，建置環境不要設這個鍵 |
| 標誌 SVG（`src/EnvCite.Platform.Web/wwwroot/brand/*.svg`）與內嵌的品牌圖形 | **否**。圖檔不讀設定，名稱在圖的 `<title>`；改名時另外換圖 |
| 文件、其他服務（consult、ontology）的文字與提示詞 | 否 |

介面一律經 `BrandText.Name` 取名稱，不在元件裡寫死；帶名稱的標題用格式鍵（`L["登入 · {0}", Brand.Name]`），英文資源檔的鍵同樣是 `{0}`。

### 寄信（`Platform:Mail`）

可選功能，預設關閉。環境變數寫成 `ENVCITE_Platform__Mail__<鍵>`；開啟時缺漏或錯值在啟動就停下並指名鍵。寄信用 MailKit。

#### 設定鍵

| 鍵 | 開啟時必填 | 說明 |
|----|-----------|------|
| `Enabled` | | 開關；沒開時建立帳號走暫時密碼 |
| `Host`、`Port` | `Host` | SMTP 中繼的主機與埠 |
| `Security` | | 加密模式：`None`（沒設時的預設）、`StartTls`、`StartTlsWhenAvailable`、`SslOnConnect`、`Auto`；見[加密與帳密](#加密與帳密) |
| `Username`、`Password` | | 登入中繼的帳密，**要嘛都設、要嘛都不設**（只設一個，啟動就停下並指名鍵）。`Password` 是機密，只經環境變數（k8s Secret）；見[加密與帳密](#加密與帳密) |
| `From`、`FromName`、`ReplyTo` | `From`（`Username` 是 email 時可省） | `From` 是寄件者；沒設而 `Username` 是 email 時用 `Username`（要求登入的中繼要求寄件者等於認證的帳號，其他中繼通常只收自己網域的寄件者）。`FromName` 是顯示名稱，沒設用[系統名稱](#系統名稱platformbrand)。`ReplyTo` 選填，是 email，加到信的 `Reply-To` 標頭，不影響寄件者 |
| `BaseUrl` | 是 | 信裡連結的來源：`https://主機`，不含路徑（只有 localhost 可用 `http`）。寫死在設定，不取自請求的 Host 標頭 |
| `LinkLifetime` | | 連結的有效期限（1 小時至 14 天），也是 Identity 資料保護權杖的期限 |
| `TimeoutSeconds` | | 連線到回應的總時限；逾時算寄信失敗 |
| `Template:*` | | 信件版型的可調值，全部有預設值，與 `Enabled` 無關、一律驗證；見[信件內容](#信件內容) |

沒設 `Security`、`Username` 與 `Password` 時，連線是**不加密、免驗證**的 SMTP，只適用於來源位址被中繼信任的內網；因此信裡只有連結（短效、單次），沒有密碼。

#### 加密與帳密

`Security` 一對一對應 MailKit 的 `SecureSocketOptions`：

| 值 | 行為 |
|----|------|
| `None`（預設） | 不使用 SSL／TLS |
| `StartTls` | 連線後以 STARTTLS 升級；中繼不支援就失敗 |
| `StartTlsWhenAvailable` | 中繼公告 STARTTLS 才升級，否則不加密地繼續 |
| `SslOnConnect` | 連上就是 SSL／TLS（通常是 465 埠） |
| `Auto` | 交給 MailKit 依埠號決定（465 為 `SslOnConnect`，其餘為 `StartTlsWhenAvailable`）；不保證加密 |

- **明文風險**：`None` 從不加密；`StartTlsWhenAvailable` 在中繼沒有公告 STARTTLS 時（包括公告被中間人拿掉）照樣明文送出，連帳密也是；`Auto` 在 465 以外的埠與它相同。RFC 8314 建議連上就加密，能選就選 `StartTls` 或 `SslOnConnect`。
- **預設是 `None`**：對應不加密、以來源位址信任的內網中繼（25 埠）。設了帳密卻用 `None` 或 `StartTlsWhenAvailable`，啟動記一則警告，`mail test` 也印警告。
- **帳密驗證**：兩者都設才驗證，由 MailKit 的 `AuthenticateAsync` 依中繼公告的機制協商（本服務不自己寫 SASL 或 STARTTLS）；沒設就**不送 AUTH**，即使中繼公告了它。`None` 加帳密能用（SASL PLAIN），代價是明文。
- **憑證一律驗證**：沒有略過驗證的設定。中繼的憑證由內部 CA 簽發時，把該 CA 放進**容器信任的憑證存放區**（.NET 在 Linux 讀系統憑證目錄，也認 `SSL_CERT_FILE`、`SSL_CERT_DIR`），不在程式裡放行。
- **寄件者必須等於認證的帳號**：Exchange 等中繼對不同的寄件者回 `550 5.7.60 Client does not have permissions to send as this sender`。`From` 與（email 形式的）`Username` 不同時，啟動記警告但不停下，因為有些中繼允許 Send As。
- **帳號系統**：Exchange 與 Google Workspace 是不同的帳號系統，用中繼所屬系統的帳號，否則回 `535`。
- **密碼錯誤的代價**：MailKit 對中繼公告的每種機制各試一次，一次寄信可能記下多次失敗登入；帳號若有連續失敗鎖定，改密碼後要同步更新 Secret 並重啟。
- **密碼的來源與保護**：k8s 以 `secretKeyRef` 注入環境變數 `ENVCITE_Platform__Mail__Password`（對應設定鍵 `Platform:Mail:Password`），改密碼就是更新 Secret 並重啟 pod。`Password` 不進日誌、稽核、例外訊息、`mail test` 輸出、選項的 `ToString()` 與序列化，也不在任何診斷端點；`Username` 同樣不進日誌與輸出。`mail test` 只印「使用帳密驗證：是／否」，不印帳號。

#### 寄出結果

結果是 `Sent`（中繼接受了信，不等於已投遞）、`Failed`、`Disabled`（未啟用）或 `NotRequested`（沒有收件地址）。寄不出去不使建立或重寄失敗，帳號照樣在，畫面與命令列照實顯示。

稽核只記結果（`mail`），不記連結；日誌只記例外型別、SMTP 狀態碼與增強狀態碼（`Mail delivery failed: SmtpCommandException 550 5.7.60`；沒有的以 `-` 表示），不含位址、帳密與內文。

#### 信件內容

邀請信、驗證信與 `mail test` 是同一個版型，寄成 `multipart/alternative`：純文字在前、HTML 在後（客戶端顯示它看得懂的最後一個）。兩版事實一致：同一條連結、同樣的期限與提示、同樣的租戶識別碼；`mail test` 沒有連結也沒有密碼。一律帶 `Auto-Submitted: auto-generated`。

- **分層**：呼叫端組資料（收件者、連結、有效期限、租戶、「邀請或驗證」）→ `IMailComposer` 組成信，不寄信、不連網路，預覽與測試用同一個服務 → `IMailSender` 只管傳輸。全部經 `AddPlatformMail()` 註冊。
- **文案**：在資源檔（`SharedKernel/MailTexts.resx` 繁體中文、`MailTexts.en.resx` 英文，鍵是領域鍵、帶參數用 `{0}`）。語言由 `Platform:Mail:Template:Culture` 決定（`zh-TW` 或 `en`），不隨操作者的介面語言——收信的人還沒登入過。主旨是 `{系統名稱}：邀請您啟用帳號`、`{系統名稱}：請驗證您的信箱`、`{系統名稱}：寄信測試`（英文有對照），系統名稱見[系統名稱](#系統名稱platformbrand)。
- **HTML 與版面**：Razor 元件（`SharedKernel/MailViews/`）經 `HtmlRenderer` 渲染，插入的值由 Razor 自動 HTML 編碼；純文字版由同一組資料另外產生。版面是單欄、有最大寬度的表格加內嵌樣式（傳統 Outlook 以 Word 引擎渲染，按鈕改用 VML 圓角矩形）；淺色與深色兩組配色，深色只在支援 `prefers-color-scheme` 的客戶端套用。
- **版型選項**（`Platform:Mail:Template`，`MailTemplateOptions`）：語言、寬度、內距、字型與淺色／深色兩組色票；鍵、預設值與合法範圍以 `MailTemplateOptions`、`MailPalette` 與驗證器為準。顏色只收 `#rrggbb`，每組色票的文字對比度至少 4.5:1；不合法就在啟動停下並指名鍵。
- **安全限制**：信裡唯一的外部網址是啟用連結，沒有遠端圖片、追蹤像素、點擊追蹤轉址、腳本或外部樣式——連結等同一次登入的鑰匙，不能經第三方，也不能被預先載入。
- **預覽**：`mail preview <輸出目錄>` 用同一個 `IMailComposer` 與選項寫出五種信（邀請與驗證各有「營運者」與「有租戶」兩種、加測試信）的 `.html` 與 `.txt`；連結是保留網域 `platform.example` 上的占位值，權杖是假值。瀏覽器預覽不等於真實客戶端，傳統 Outlook 與各家深色模式要以實際寄出的信檢查（見 [`CONTRIBUTING.md`「本機收信與寄信測試」](../../CONTRIBUTING.md#本機收信與寄信測試)）。

#### 測試與排查

在服務自己的環境裡執行 `mail test <email>`：

- 印出實際使用的中繼、加密模式、「使用帳密驗證：是／否」、寄件者、回覆位址（有設時）與連結來源；不印帳號與密碼，加密不足時多一行警告。說明文字在 `MailTexts` 資源檔（鍵以 `MailTest.` 開頭），語言同信件。
- 寄一封沒有連結、沒有密碼的測試信（與邀請信同一個版型），以結束碼說明結果；失敗時一次列出常見原因（含 `535` 與 `550 5.7.60`），對應下表。
- 來源位址是否被中繼信任只能在服務實際執行的環境驗證；從別的機器測通不代表服務能寄。

| 現象 | 常見原因 |
|------|----------|
| `寄信未啟用` | `Enabled` 不是 `true` |
| `Failed`，日誌是 `SocketException`、`IOException` 或逾時 | 連不上 `Host:Port`：DNS、防火牆、埠錯誤 |
| `Failed`，日誌帶 `550`、`554` 之類的 SMTP 狀態碼 | 來源位址不在中繼的信任名單，或 `From`、收件者的網域被中繼拒絕 |
| `Failed`，日誌是 `AuthenticationException`、狀態碼 `535` | 帳號或密碼錯誤。Exchange 與 Google Workspace 是不同的帳號系統：要用中繼所屬系統的帳號；改過密碼要同步更新 Secret（見[密碼錯誤的代價](#加密與帳密)） |
| `Failed`，日誌帶 `550 5.7.60` | 寄件者不等於認證的帳號：讓 `From` 等於 `Username`，或不設 `From` 改用 `Username` |
| `Failed`，日誌是 `SmtpServiceNotAuthenticatedException` | 中繼要求先登入：設 `Username` 與 `Password`（這類中繼通常也要求加密，一併設 `Security`） |
| `Failed`，日誌是 `SslHandshakeException` | 憑證不被信任（內部 CA 沒放進容器信任）、主機名稱與憑證不符，或 `Security` 與埠不合（例如 `SslOnConnect` 連到 25 埠） |
| `Failed`，日誌是 `NotSupportedException` | `Security` 是 `StartTls`，但中繼沒有公告 STARTTLS |
| `Failed`，日誌是 `TimeoutException` | 連得上埠但沒有回應：常見於對 465 埠用 `None`（該埠一連上就要 TLS） |
| `Failed`，日誌是 `FormatException` | 收件地址格式不合法 |
| `Sent`，但沒收到 | 中繼接受了信但沒投遞：查垃圾信匣與中繼的郵件追蹤 |

本機與正式環境的操作順序見 [`CONTRIBUTING.md`「本機收信與寄信測試」](../../CONTRIBUTING.md#本機收信與寄信測試)。
