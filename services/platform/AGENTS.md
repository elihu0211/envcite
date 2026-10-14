# AGENTS.md

倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `services/platform/`。

## 索引

| 文件 | 內容 |
|------|------|
| [`../../README.md`](../../README.md) | 倉庫總覽與目錄 |
| [`../../AGENTS.md`](../../AGENTS.md) | 倉庫層規範 |
| [`README.md`](README.md) | 本服務：介面、請求流程、啟動、開發指令 |
| [`../../proto/README.md`](../../proto/README.md) | gRPC 契約與服務如何組合 |
| `openapi/openapi_v2.json` | 對外 REST 契約（建置產生） |
| [`../ontology/`](../ontology/README.md)、[`../consult/`](../consult/README.md) | 本服務呼叫的上游，各有 README 與 `AGENTS.md` |

## Project

- **Name:** platform（`services/platform`；倉庫為 `envcite`）
- **Role:** 多租戶控制平面：租戶、API key、限流、計量、對外 REST API；經 gRPC 呼叫 `services/ontology`（共享、不分租戶）與 `services/consult`（接地、租戶隔離）。
- **Stack:** C# · .NET（SDK 版本在 `global.json`）· ASP.NET Core · PostgreSQL · xUnit
- **Auth surfaces:** 租戶 API key（`/v2`）；共用營運者金鑰（`/admin`）；營運者帳號加強制 TOTP（營運者後台 `/ui`）；客戶入口帳號（`/portal`，以設定開關）。
- **Plans:** 方案指派是營運者寫入的資料；接上付款供應商後，付款是真值，由其 webhook 寫入 entitlements。

## Architecture

- Modular monolith：一個 solution、一個可部署單位。模組是專案，只參考 `SharedKernel` 與 `Data`，彼此不直接參考；跨模組契約放 `SharedKernel`。
- **兩個 DbContext，不再增加**：`TenancyDbContext`（Finbuckle 的 EFCore 租戶儲存必須繼承 `EFCoreStoreDbContext`，無法合併）與 `PlatformDbContext`（所有租戶資料，schema `platform`）。新的租戶實體進 `PlatformDbContext`；模組不另立 context——每多一個就多一套遷移、bundle 與 Job 步驟，且失去跨模組交易。
- Data Protection 金鑰存在 `TenancyDbContext`，不在 `PlatformDbContext`：它不屬於任何租戶，且金鑰環在主機啟動時載入、早於租戶解析。沒有共用金鑰，一個副本簽的 cookie 會被另一個副本拒絕。產生 OpenAPI 文件時不註冊（沒有資料庫）。
- Blazor Server 電路是有狀態的 SignalR 連線：多副本需要入口的 session affinity（部署倉庫）或外部 backplane。
- 多租戶是執行期隔離（租戶解析加 `TenantId` 查詢過濾），不是每租戶一份程式碼或倉庫。
- **中介層順序是承重的**：`UseAuthentication` → `UseMultiTenant` → `UseAuthorization` → `UseRateLimiter`。
- `MultiTenantDbContext` 在建構時擷取租戶：不得在租戶解析之前解析它；租戶請求之外要針對特定租戶用 `MultiTenantDbContext.Create<TContext, TenantInfo>(tenant, services)`。

## Layout

- `src/EnvCite.Platform.Api`：ASP.NET Core 主機（composition root）。
- `src/EnvCite.Platform.Modules.*`：功能模組。
- `src/EnvCite.Platform.Web`：營運者後台與客戶入口的 Razor 元件函式庫（Blazor；頁面在 `/ui`、`/portal`），由 `Api` 掛上；不在 `/v2` 的限流與計量群組內。
- `src/EnvCite.Platform.Data`：`PlatformDbContext`、實體與遷移。
- `src/EnvCite.Platform.SharedKernel`：跨模組基元（認證名稱、計量介面、內部 gRPC 用戶端註冊、設計時連線）。
- `tests/EnvCite.Platform.Tests`：xUnit 加 `WebApplicationFactory` 整合測試；Testcontainers PostgreSQL，上游以行程內 h2c gRPC fake 取代。

## Commands

從**本目錄**執行（`EnvCite.Platform.slnx` 所在處）。

```bash
dotnet restore && dotnet tool restore  # 工具：dotnet-ef（dotnet-tools.json）
# 依賴由提交的 packages.lock.json 鎖定（CI 用 --locked-mode restore）。
# 改了 Directory.Packages.props 或 csproj 之後：dotnet restore --force-evaluate，並提交鎖檔。
dotnet build                         # 警告即錯誤（Directory.Build.props）；同時產生 openapi_v2.json
dotnet test                          # 需要 Docker（Testcontainers PostgreSQL）
dotnet format                        # 套用 .editorconfig
dotnet format --verify-no-changes    # CI 檢查
dotnet run --project src/EnvCite.Platform.Api   # Development：連本機 PostgreSQL，自動遷移

# 遷移：每個 context 所在專案自己就是啟動專案（design-time factory，不是應用程式主機）
dotnet ef migrations add <Name> -p src/EnvCite.Platform.Data -s src/EnvCite.Platform.Data
dotnet ef migrations add <Name> -p src/EnvCite.Platform.Modules.Tenancy -s src/EnvCite.Platform.Modules.Tenancy

docker build -f Dockerfile ../..   # 映像（建置內容為倉庫根）；CI 的 `platform-image` 在 main 推送 :<sha>
```

- 套件版本只在 `Directory.Packages.props`（Central Package Management）；共用建置設定在 `Directory.Build.props`，不在 `.csproj` 重複。格式規則在 `.editorconfig`。
- 正式環境的遷移用 EF Core migration bundle，建進映像，由部署的遷移 Job 依序執行（tenancy 先、platform 後）。每個 context 的 `IDesignTimeDbContextFactory` 與應用程式共用 `UseNpgsql` 設定，所以 bundle 不必執行 `Program.cs`，也指向同一張遷移歷史表。

## Contract

- **對外 REST 契約是 `openapi/openapi_v2.json`**，每次 `dotnet build` 產生，與程式碼變更一起提交；過期（`dotnet` job）或破壞目標分支版本（`openapi` job，oasdiff）CI 都會失敗。新的 `/v2` 端點要有 `.WithName`／`.WithTags`／`.WithSummary`／`.WithDescription`，以及它會回的每個狀態的 `.Produces…`。不相容的變更進新版本群組（`/v3`），不進 `/v2`。
- **對外用語獨立於內部 gRPC**（端點在兩者間對映），用法律詞彙；新的公開欄位先取法律用語，內部 proto 不必跟著（例：`pointInTime` 對 gRPC 的 `as_of`）。
- `.proto` 一律來自倉庫根 `proto/`（Grpc.Tools 於各模組 `.csproj` 編譯），**不得複製**。
- 內部 gRPC 用戶端只經 `SharedKernel.InternalGrpcClient.AddInternalGrpcClient` 註冊，新增上游也走這裡，不複製一份。連線選項與 Python 伺服器成套（`envcite_grpc.connection`）：用戶端 keepalive 間隔必須大於伺服器允許的最短 ping 間隔；只重試 `UNAVAILABLE`（內部方法都是唯讀、確定性的一元呼叫），`DEADLINE_EXCEEDED` 與 `RESOURCE_EXHAUSTED` 不重試。改一邊要看另一邊。
- 錯誤對映：gRPC `InvalidArgument` → 400，`NotFound` → 404，`Unavailable`／`DeadlineExceeded` → 503。
- 計量只記成功的呼叫（consult 的拒答是成功的結果，要計）；用量不得猜測（端點沒回報用量就不記）。

## Configuration

- 設定只有一條讀法：每個設定群組是一個 `IPlatformOptions` 類別，自己指名區段，以 `AddPlatformOptions<T>()` 註冊（綁定加 `ValidateOnStart`）；使用端注入 `IOptions<T>`。除了 `Observability.cs` 裡那個 `OTEL_EXPORTER_OTLP_ENDPOINT` 開關，沒有別處碰 `IConfiguration`。錯值或缺值在啟動就讓主機停下並指名鍵（k8s 裡是帶清楚訊息的 CrashLoopBackOff），不是第一個請求才失敗。
- 新增群組：實作 `IPlatformOptions`、註冊、加 `IValidateOptions<T>`（屬性夠用時 `[OptionsValidator]`，跨欄位規則手寫）、在 `ConfigurationTests` 加一個案例。
- **鍵名是與部署倉庫的契約**，改名是破壞性變更。k8s 以環境變數供給（`Section__Key`）。機密（營運者金鑰雜湊、模型端點金鑰）只經環境變數，不進檔案、不提交；Api 專案沒有 `UserSecretsId`。
- 可選的功能面（營運者後台、客戶入口、有依據的回答、對話紀錄）**預設關閉**，新增的同類功能也一樣；Development 為了本機可用而開啟。關閉時對應路徑回 404，且不簽發任何 cookie。
- 正式環境不設 `DefaultPlan`（服務是全付費，沒方案的租戶在資料端點得 402）；Development 與測試設定它，讓剛建立的租戶能用。
- Development 的種子資料（`SeedDevelopmentData`）只在 Development 生效，且只在對應表是空的時才建；一次性密碼只經 `Console.WriteLine` 印到終端機，不進 `ILogger`，沒有預設密碼。
- 在 TLS 終止的 proxy 後面要設 `ASPNETCORE_FORWARDEDHEADERS_ENABLED=true`，否則依來源位址限流的規則會把所有人看成同一個位址（且限流狀態是每個副本各一份）。

## Observability

- OpenTelemetry 追蹤、指標與日誌；設定全是標準 `OTEL_*` 鍵，**沒設 `OTEL_EXPORTER_OTLP_ENDPOINT` 就什麼都不匯出**。
- 已認證的租戶只標在請求追蹤的 `tenant.id`，**不進指標**（基數無界）。
- 探針：`/health/live` 不檢查任何東西（依賴故障不該重啟 pod）；`/health/ready` 檢查資料庫（`TenancyDbContext`，它早於租戶解析存在），故障回 503。探針請求不追蹤。

## Auth & tenancy

- **租戶 API**：`Authorization: Bearer <key>` → 以前綴查詢、SHA-256 加定時間比對 → `tenant` 宣告 → Finbuckle 解析租戶 → 租戶實體（`IsMultiTenant()`）自動過濾。
- **營運者 API**（`/admin`）：`X-Admin-Key` 對 `Platform:AdminKeySha256`。
- **cookie 永遠不認證 `/v2` 或 `/admin`**；每個 `/ui` 與 `/portal` 回應都是 `Cache-Control: no-store`。預設認證方案維持 ApiKey。
- 每個租戶範圍的查詢都必須以 `TenantId` 過濾；不經審查不得停用查詢過濾。只能**按名稱**繞過租戶過濾（`IgnoreQueryFilters([TenantToken])`），且只在審查過的跨租戶查詢（API key 認證、對話紀錄的保存期限清理）。
- **一次性密碼**：隨機產生、只顯示一次、只存雜湊；不得進任何日誌或稽核。
- 登入失敗**永遠不透露**帳號是否存在、是否鎖定或停用（一個訊息；不存在的帳號仍付一次雜湊的成本）；失敗的表單**不回填**密碼或驗證碼；稽核與日誌**不記輸入的內容**（人會把密碼貼進 email 欄；稽核表只增不改），只有形如 email 的輸入才記。
- 密碼規則：最小長度、無組成規則（NIST SP 800-63B）；連續失敗鎖定帳號。營運者與客戶兩邊規則相同。

### 營運者帳號（`/ui`）

- 有**自己的使用者儲存**（營運者不屬於任何租戶，不是租戶實體，無租戶過濾）；Identity 的金鑰型別選 `Guid`，是為了不與成員表的 `IdentityUser…<string>` 型別混成同一個繼承階層；儲存手動註冊。
- **不開放自助註冊**：帳號由 `/ui` 的營運者頁或營運者金鑰建立。
- **首次登入：強制改密碼，再綁驗證器**，順序固定（驗證因子不會設在金鑰持有者知道的密碼後面）。**TOTP 對每位營運者強制**，沒有角色豁免、不能關。
- **授權每個請求都重查資料庫**：帳號存在、未停用、security stamp 仍有效；是否還要改密碼、是否已綁驗證器，由**資料庫**決定，不看 cookie 宣告。所以停用、重設密碼、重設驗證器在**下一個請求**生效；重新啟用也旋轉 stamp，舊 cookie 不復活。已開著的互動式電路不是 HTTP 請求，由 `RevalidatingServerAuthenticationStateProvider` 模式定期重驗。
- 登入、第二步、改密碼與安全設定頁是靜態 SSR 的 POST 表單（`[ExcludeFromInteractiveRouting]`、antiforgery），受依來源位址的限流政策約束。
- **登入的稽核是 fail closed**：事件先於 session cookie 寫入，寫入失敗就沒有 session。
- 營運者不能對自己的那一列操作。

### 客戶入口（`/portal/{tenant}/…`）

- 租戶在路徑裡，於登入前由委派策略解析（路徑優先，API key 宣告其次）。頁面是**靜態 SSR**（無電路）：每個請求自行解析租戶並認證，表單是附 antiforgery 的普通 POST。
- 使用者是租戶實體（`MultiTenantIdentityDbContext`）：同一個 email 在兩個租戶是兩個帳號；角色是欄位，不是 Identity 角色。
- session cookie 的授權只讀宣告；**cookie 的租戶必須等於請求路徑解析出的租戶**（此檢查被使用者表的租戶過濾端到端遮蔽，所以有獨立的單元測試，每個檢查都做過突變測試）。每個入口請求檢查 security stamp，所以改密碼或重設會立即結束成員的其他 session。
- 三條建置時踩過的規則：(1) 入口 cookie 由 `UsePlatformPortalAuthentication` 認證，必須在 `UseMultiTenant` **之後**，否則 scoped 的 `PlatformDbContext` 在租戶已知前就被解析，整個請求都帶著沒有租戶的 context；(2) 租戶 context 是 `AsyncLocal`，`MemberAdmin` 在呼叫它的方法內綁定，不在 async 輔助方法裡（輔助方法返回時值會被丟棄）；(3) 營運者建立的成員要先改一次性密碼才能用，之後才綁驗證器。
- 依來源位址的限流刻意寬鬆（機關或顧問公司常共用一個對外位址），它擋的是自動化猜測，不是正常使用。
- 這是**示範等級**的入口：沒有外部或 SSO 登入、沒有 email、沒有 CAPTCHA，不是給真實客戶用的；要改變這點，先補齊這些。

### 入口的 MFA（TOTP，RFC 6238）

- 登入分兩步：密碼成功只簽發短期、不續期、授權不接受的待驗證 cookie，第二頁把驗證碼（或復原碼）換成真正的 session。
- 必須保持且有測試的規則：(1) **密碼與驗證碼共用一個失敗計數器**，且密碼正確**不**重設它（`PortalAccount` 自己做鎖定記帳，不用 `PasswordSignInAsync`）；(2) **重放防護**：只接受比 `LastTotpStep` 新的時間步，原子更新；容許 ±1 步，時間取自 `TimeProvider`；(3) 金鑰由我們產生並存放（`SetAuthenticationTokenAsync`），**不用** `ResetAuthenticatorKeyAsync`——它會旋轉 stamp，結束開啟該頁的那個 session；(4) 待驗證 ticket 綁定簽發它的租戶；(5) 沒有金鑰、驗證碼、復原碼或 `otpauth` URI 進稽核或日誌（測試同時捕捉兩者）。
- QR 碼在伺服器端繪製，金鑰不交給第三方 QR 服務。復原碼只在那一次回應顯示。
- 擁有者與管理員強制 MFA（`Platform:Portal:RequireMfaForAdmins`）；被要求的角色不能自己關閉，營運者重設會把他送回綁定流程，而不是留下無保護的帳號。順序同樣是先改一次性密碼、再綁定。

### 成員管理（客戶自助與營運者）

- 營運者路徑與客戶自助路徑共用 `MemberCore`（驗證、一次性密碼、稽核），不會漂移。
- 規則（每條都有測試）：**階層**——擁有者管理所有人；管理員只管理較低的角色，且只能授予那些角色（能管理的就是能授予的，沒有人被升到管理者之上）；**沒有人對自己操作**（停用、改角色、重設）；**操作者的角色讀資料庫，不讀 cookie 宣告**；別的租戶的成員就是找不到。
- 停用的成員無法登入（與密碼錯誤相同的泛用失敗）、所有開著的 session 立即失效（stamp 旋轉**且** session 檢查看旗標本身）、不能完成停用前已開始的第二步；帳號與稽核紀錄保留。啟用與停用都旋轉 stamp，舊 cookie 不復活。

## Plans & access

- **方案指派是資料，不是設定**：`TenantEntitlements`（租戶 id → 方案名稱；不是 Finbuckle 租戶實體）。方案**定義**（限額）在 `Platform:Plans`——產品決策，很少變；**誰在哪個方案**隨每位客戶變動，由營運者在執行期設定，立即生效並寫入稽核，不必為每位客戶重新部署。查找順序：資料庫指派 → 設定的 `TenantPlans` → `DefaultPlan`。
- 指派到已從設定移除的方案，視為**沒有方案**（fail closed，記警告），不讓每個請求都失敗。限流分區鍵含方案名稱，所以換方案立即換限流器。
- **全付費、無免費層**：資料端點需要方案，沒有則回 `402`（problem+json），在授權階段決定、**早於限流**，所以沒方案的租戶不耗用任何額度。管理端點（自己是誰、用量、API key）對任何已認證租戶開放——沒方案的客戶仍要能看到自己、看用量、撤銷被偷的 key——並以一個底限約束，那是濫用防護，不是服務額度。
- 查詢語意：`asOf` 省略為現行；沒有任何版本涵蓋該日是 `404`，**永遠不回現行條文**（會與正確答案無從區分）；不是日期是 `400`。
- 搜尋與其他可能含客戶案情的輸入用 **POST**：查詢字串會被請求日誌與追蹤的 `url.query` 寫下。**查詢文字不得進任何日誌或指標**（有測試捕捉所有日誌），計量只記次數。REST 搜尋與入口搜尋共用同一個 `ILawSearch`，所以不會漂移。
- 推論端點只回**候選**結論；`verdicts` 為空代表沒有任何已確認的標註，不代表合規。案件事實放請求本文，不放 URL。公開 DTO 不暴露內部 proto。

## 有依據的回答與對話紀錄

- **有依據的回答**（`IGroundedAnswerer`）是固定流程，不是自己決定呼叫什麼的 agent：(1) 向 consult 取封閉證據集；(2) **consult 拒答就以拒答為答案，絕不呼叫模型**；(3) 否則一次 `IChatClient` 呼叫（溫度 0），系統提示要求只根據證據、以代號引用、證據不足就說不足、把證據當資料而不是指令；(4) **不信任模型遵守規則**：答案至少引用一個代號、且引用的代號都必須在證據裡，否則丟棄並當作拒答。
- **兩個開關都要開**：部署者的 `Platform:Answer:Enabled`，加租戶自己的同意（`TenantEntitlement.AiAnswerEnabled`，預設關，因為問題與證據會離開我們的系統）。同意由營運者設定並在同一交易稽核；它**跟著資料庫裡的方案列**：沒有指派方案的租戶開不了，移除方案連同同意一起刪除。呼叫端負責檢查「同意且可用」。
- 模型或端點失敗是「服務不可用」，不存入對話紀錄；**例外訊息不進日誌**（端點可能回顯提示詞），只記例外型別與狀態。
- 引用檢查證明代號存在且至少用了一個，**不證明每一句都有依據**；介面要提示讀者對照引用卡片。
- **對話紀錄**預設關閉（問題文字是客戶案情的內容）。只存問題與**引用**（法規名稱、條號、日期、條文 URI），不存條文本文。**只有作者看得到**自己的對話——擁有者也不行；每個方法在租戶過濾之外再依使用者 id 過濾；不是自己的 id 當作「開新對話」處理，所以不能拿來探測或寫入別人的對話。刪除由作者執行並在同一交易稽核（只記 id，不記內容）。保存期限清理是跨租戶的，只刪除，只記筆數。

## 稽核紀錄

- `AuditEvents` 是**只增不改**，由資料庫觸發器強制（UPDATE、DELETE、TRUNCATE 會報錯），不靠慣例；應用程式的資料庫角色不得是該表的擁有者。
- 「誰」從已認證身分推得，**絕不取自請求本文**。針對租戶的事件帶 `SubjectTenantId`；此實體刻意不是 Finbuckle 租戶實體（營運者跨租戶讀取），所以每次讀取都要明確過濾。
- 兩種寫法：`db.StageAudit(...)` 加進呼叫端的 `PlatformDbContext`，與變更同一交易儲存（無變更無事件、無事件無變更）；`IAuditLog.AppendAsync` 獨立寫入，用於沒有變更可依附的事件。`Detail` 不放機密、雜湊或完整 API key（有測試）。每個事件保存 OpenTelemetry trace id。

## 設計系統與營運者後台

- 外觀只有一個來源：`Web/Design/DesignTokens.cs`（顏色、字級、間距、圓角、陰影、動態）。要改顏色或尺寸只改那裡；`platform.css` 只引用變數，**不得寫死**顏色、字重、時長、緩動、縮放或觸控尺寸（測試會失敗）。`DesignTokenTests` 驗證每組文字配色符合 WCAG AA 對比。
- 產品決策：**只有淺色**，不做深色主題（測試擋 `prefers-color-scheme`）；不用漸層；入口**沒有側邊欄**；視口斷點只有兩個，寫在 `DesignTokens.Breakpoints`，其他視口寬度會讓測試失敗；表格卡片用 container query，**不得**對卡片加 `overflow: hidden`。禁止停用縮放。
- 營運者頁面**不呼叫 `/admin` HTTP API**，改讀 `IAuditQuery`、`ITenantDirectory` 這類在 `SharedKernel` 的契約（與 `/admin` 端點用同一份實作）；每次呼叫開自己的 scope，因為 Blazor Server 電路的壽命遠長於一個請求，不能持有過期的 `DbContext`。
- 新發出的 API key 或一次性密碼只在不可由 Esc 或點背景關閉的對話框顯示一次，UI 不再重繪它；不可逆的動作（撤銷、移除方案）要確認。
- 時間以臺北時間（固定 UTC+8）顯示，ISO 時間放 `title`；長 id 縮短時留**尾端**（UUIDv7 的開頭是時間戳）。
- `Api.csproj` 設 `RequiresAspNetWebAssets`，因為 Razor 元件在類別庫裡、主機自己沒有 `.razor`；少了它 SDK 不會提供 `_framework/blazor.web.js`，電路不會連線，而伺服器渲染的 HTML 看不出任何問題。`DesignPageTests` 會抓取頁面引用的每個腳本以守住這點。

## Conventions

- 格式規則：`.editorconfig`。
- 新增 `/v2` 端點、公開欄位或可選功能面時，同一次提交更新 OpenAPI、測試，以及（行為改變時）本檔的對應不變式。

## Safety

- 不提交機密、連線字串、計費金鑰或真實的 `.env*`／本機 appsettings 值。
- 產生物不手改：`src/*/Migrations/`（用 `dotnet ef`；`.editorconfig` 標為 `generated_code`；唯一例外是 `AuditEventsAppendOnly`，EF 無法表達觸發器，其 `Sql` 呼叫是手寫的）、`obj/` 下的 gRPC 程式碼（來自倉庫根 proto）、`openapi/openapi_v2.json`（來自 `dotnet build`）。
- 對共用資料庫套用遷移前先問（總清單見根 `AGENTS.md`）。

## Done when

- `dotnet build` 與 `dotnet test` 通過，`dotnet format --verify-no-changes` 乾淨——由 `.gitlab-ci.yml` 強制（Testcontainers 用 Docker-in-Docker）。
