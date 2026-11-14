# AGENTS.md

倉庫層規範見根 `AGENTS.md`；本檔只管 `services/platform/`。

## Project

- **Role:** 多租戶控制平面：租戶、API key、限流、計量、對外 REST API；經 gRPC 呼叫 `services/ontology`（共享、不分租戶）與 `services/consult`（接地、租戶隔離）。
- **Auth surfaces:** 租戶 API key（`/v2`）；營運者帳號加強制 TOTP（營運者後台 `/ui`）；客戶入口帳號（`/portal`，以設定開關）；目錄同步 token（`/scim/v2`，以設定開關）。

## Architecture

- Modular monolith：一個 solution、一個可部署單位。模組是專案，只參考 `SharedKernel` 與 `Data`，彼此不直接參考；跨模組契約放 `SharedKernel`。模組是編譯期的：每個模組以 `AddXModule()` 註冊，不做執行期載入組件的外掛。唯一的例外是 `Modules.Consult` 參考 `Modules.Ontology`：`consult.proto` 匯入 `ontology.proto`，產生的型別在後者。
- **依賴注入的規矩由 `ArchitectureTests` 掃原始碼與專案檔守住**，違反的人在 CI 看得到是哪個檔案：(a) 上一條的專案參考；`SharedKernel` 不參考任何平台專案，`Web` 只參考 `SharedKernel`（不經模組、不碰資料層）；(b) **不用服務定位器**：除了組合根（`Api` 專案、`WebModule.cs`）與 `IServiceProvider` 自己的實作，沒有任何型別注入 `IServiceProvider` 自己解析；要惰性取得就注入 `Lazy<T>`，由模組的 `AddXModule()` 註冊；(c) **元件與 `Web` 不用 `IHttpContextAccessor`**：互動式元件沒有可靠的 `HttpContext`，身分走 `AuthenticationState`、租戶由路由參數帶入；(d) **我們自己的型別不註冊可處置（`IDisposable`／`IAsyncDisposable`）的 transient**：容器會抓著它直到 circuit 或應用程式結束（記憶體洩漏）。測試主機在 Development 環境，`ValidateScopes` 與 `ValidateOnBuild` 開著，singleton 抓 scoped 的錯在啟動就爆。
- **入口側欄由各功能自己貢獻**：實作 `IPortalNavContributor`（`Components/Layout/Navigation/`，註冊用 `AddPortalNavContributor<T>()`，scoped）宣告項目、群組與 `Order`（服務群組 10–29、組織群組 30–59），外殼 `PortalLayout` 只經 `PortalNavigation.Collect` 排序，不認識任何功能。新功能不改外殼。項目文字經 `L`（基底 `PortalNavContributorBase` 提供），資源檔的測試才掃得到；顯示與否不是授權，頁面與服務層另有檢查。
- **兩個 DbContext，不再增加**：`TenancyDbContext`（Finbuckle 的 EFCore 租戶儲存必須繼承 `EFCoreStoreDbContext`，無法合併）與 `PlatformDbContext`（所有租戶資料，schema `platform`）。新的租戶實體進 `PlatformDbContext`；模組不另立 context——每多一個就多一套遷移、bundle 與 Job 步驟，且失去跨模組交易。
- Data Protection 金鑰存在 `TenancyDbContext`，不在 `PlatformDbContext`：它不屬於任何租戶，且金鑰環在主機啟動時載入、早於租戶解析。沒有共用金鑰，一個副本簽的 cookie 會被另一個副本拒絕。產生 OpenAPI 文件時不註冊（沒有資料庫）。金鑰預設以明文 XML 存放；`Platform:DataProtection:CertificatePath` 與 `CertificatePassword` 是成對的可選設定（只設一個啟動即失敗，密碼只經環境變數），設了則**新**金鑰以該憑證加密，既有明文金鑰照常可讀、不改寫；所有副本須用同一張憑證。
- Blazor Server 電路是有狀態的 SignalR 連線：多副本需要入口的 session affinity（部署庫）或外部 backplane。
- 多租戶是執行期隔離（租戶解析加 `TenantId` 查詢過濾），不是每租戶一份程式碼或倉庫。
- **中介層順序是承重的**：`UseAuthentication` → `UseMultiTenant` → `UseAuthorization` → `UseRateLimiter`。
- `MultiTenantDbContext` 在建構時擷取租戶：不得在租戶解析之前解析它。
- **沒有 ambient tenant 的地方（營運者的跨租戶操作、互動式 circuit）一律經 `TenantScopes.InAsync`**（`Data`）：它開一個 DI scope、把租戶設成目標租戶、在委派裡執行、用完處置——一個操作一個 `DbContext`。委派裡用 `scope.Get<T>()` 取服務、`scope.CreateDb()` 取明確繫結的 `PlatformDbContext`（稽核要與變更同一個 `SaveChanges` 時用它）。不要自己 `CreateAsyncScope` 加綁定，也不要把 `TenantScope` 傳出委派，或在別的 async 輔助方法裡另外綁定：租戶 context 是 `AsyncLocal`，方法一返回值就丟掉。`TenantScopesTests` 守住隔離（只看得到目標租戶、同時進行的兩個租戶不互相滲透、scope 用完即處置）。

## Layout

- `src/EnvCite.Platform.Web` 是 Razor 元件函式庫（營運者後台 `/ui`、客戶入口 `/portal`），由 `Api` 掛上；不在 `/v2` 的限流與計量群組內。
- 測試：xUnit 加 `WebApplicationFactory` 整合測試；Testcontainers PostgreSQL，上游以行程內 h2c gRPC fake 取代。
- **互動式的部分用真的瀏覽器測**（`WorkspaceE2ETests`：xUnit 以 `WebApplicationFactory` 的 Kestrel 模式起伺服器、建租戶與成員，`tests/e2e/workspace.mjs` 用 playwright 驅動瀏覽器）：circuit 的身分來自 SignalR 連線、連線沒有租戶路徑，這類錯誤（曾經把入口成員當匿名、整頁被導去後台登入）只有真的瀏覽器加真的伺服器驗得到，TestServer 與服務層測試都看不到。第一次執行自動 `npm ci` 並下載 playwright 指定版本的 Chromium；沒有網路用 `ENVCITE_SKIP_E2E=1` 跳過，有現成的 Chromium 用 `ENVCITE_CHROMIUM_PATH` 指過去。CI 不設這兩個，所以一定會跑。入口頁面在 circuit 裡被授權擋下（別的組織、還有必須先完成的步驟）時，用入口的版面與說明（`Routes.razor` 的 `DefaultLayout` 依網址挑），不給客戶看後台的外殼。
- 寄信的測試對行程內的 `SmtpServer`（NuGet 套件，包成 `TestSmtpServer`）進行真的 TCP 連線，不寄任何真實郵件、不連外，也不自己手寫 SMTP 伺服器。

## Commands

從**本目錄**執行（`EnvCite.Platform.slnx` 所在處）。

```bash
dotnet restore && dotnet tool restore  # 改了 Directory.Packages.props 或 csproj 之後加 --force-evaluate 並提交 packages.lock.json（CI 用 --locked-mode）
dotnet build                         # 警告即錯誤；同時產生 openapi_v2.json
dotnet test                          # 需要 Docker
dotnet format --verify-no-changes    # CI 檢查；去掉旗標即套用
dotnet run --project src/EnvCite.Platform.Api   # Development：連本機 PostgreSQL，自動遷移；worktree 內用 `wt platform`

# 遷移：每個 context 所在專案自己就是啟動專案（design-time factory，不是應用程式主機）
dotnet ef migrations add <Name> -p src/EnvCite.Platform.Data -s src/EnvCite.Platform.Data
dotnet ef migrations add <Name> -p src/EnvCite.Platform.Modules.Tenancy -s src/EnvCite.Platform.Modules.Tenancy

docker build -f Dockerfile ../..   # 映像（建置內容為倉庫根）
```

- 共用建置設定在 `Directory.Build.props`，不在 `.csproj` 重複。
- 正式環境的遷移用 EF Core migration bundle，建進映像，由部署的遷移 Job 依序執行（tenancy 先、platform 後）。每個 context 的 `IDesignTimeDbContextFactory` 與應用程式共用 `UseNpgsql` 設定，所以 bundle 不必執行 `Program.cs`，也指向同一張遷移歷史表。

## Contract

- **對外 REST 契約是 `openapi/openapi_v2.json`**，每次 `dotnet build` 產生，與程式碼變更一起提交；規格過期或破壞目標分支版本，CI 都會失敗。新的 `/v2` 端點要有 `.WithName`／`.WithTags`／`.WithSummary`／`.WithDescription`，以及它會回的每個狀態的 `.Produces…`。不相容的變更進新版本群組（`/v3`），不進 `/v2`；決定直接破壞 `/v2` 時，pull request 加 `api-breaking` 標籤（CI 的 oasdiff 只列不擋，僅限該 pull request），破壞了什麼與理由寫在 pull request 說明。
- **營運操作的失敗帶穩定代碼**（`AdminOutcome.Code`，常數在 `SharedKernel/OutcomeCodes.cs`）：呼叫端與介面依代碼判斷，不依賴文字。`Message` 與 API 的 `detail` 永遠是英文（來自 `OutcomeCodes.English`），服務層不放中文；API 的驗證與一般錯誤回應多一個 `code` 欄位。介面依代碼取使用者的語言（`Labels` 資源檔的 `Outcome.{代碼}`，`OutcomeText.Localized()`）。新增代碼要同時加英文、中文、英文資源檔，測試守住三者一致；Identity 的錯誤經 `IdentityOutcomes` 轉成代碼，不直接傳出。
- **對外用語獨立於內部 gRPC**（端點在兩者間對映），用法律詞彙；新的公開欄位先取法律用語，內部 proto 不必跟著（例：`pointInTime` 對 gRPC 的 `as_of`）。
- `.proto` 一律來自倉庫根 `proto/`（Grpc.Tools 於各模組 `.csproj` 編譯），**不得複製**。
- 內部 gRPC 用戶端只經 `SharedKernel.PlatformGrpcClient.AddPlatformGrpcClient` 註冊（它在 `libs/dotnet` 的 `AddInternalGrpcClient` 之上加租戶與請求的 metadata），新增上游也走這裡，不複製一份。連線選項與 Python 伺服器成套（`envcite_grpc.connection`；不等式見 `envcite-grpc` 的 README），改一邊要看另一邊。只重試 `UNAVAILABLE`（內部方法都是唯讀、確定性的一元呼叫），`DEADLINE_EXCEEDED` 與 `RESOURCE_EXHAUSTED` 不重試。
- 錯誤對映：gRPC `InvalidArgument` → 400，`NotFound` → 404，`Unavailable`／`DeadlineExceeded`／`ResourceExhausted` → 503。
- **未捕捉的例外**（非 Development）由最外層的 `UseExceptionHandler()` 收下，對接受 JSON 的呼叫端回 500 problem+json：`code` 為 `internal.error`、`detail` 固定英文、帶 `traceId`，**例外的訊息與堆疊不出現在回應**（只在日誌）；只接受 HTML 的呼叫端維持空本文的 500。Development 保留框架的開發者例外頁。
- 計量只記成功的呼叫（consult 的拒答是成功的結果，要計）；用量不得猜測（端點沒回報用量就不記）。
- **`Idempotency-Key`（`POST /v2/ground`）**：鍵是（租戶、操作、key）；請求內容只存雜湊，不存原文（問題文字是客戶案情）；只存成功的回應，錯誤不存。回應與用量**在同一次儲存**（`IIdempotencyStore.CompleteAsync`），並行的重複請求只有一個存得進去、另一個的用量隨之回滾，所以重送不重複計量。同 key 不同內容回 422。新的可重送端點照此接上，不另寫一套。

## Configuration

- 設定只有一條讀法：每個設定群組是一個 `ISectionOptions` 類別（`libs/dotnet`），自己指名區段，以 `AddSectionOptions<T>()` 註冊（綁定加 `ValidateOnStart`）；使用端注入 `IOptions<T>`。除了 `libs/dotnet` 的 `Telemetry.cs` 裡那個 `OTEL_EXPORTER_OTLP_ENDPOINT` 開關，與 `AddPlatformDataProtection` 載入金鑰環憑證（框架只接受註冊時就備妥的憑證物件；仍有 `IValidateOptions` 校驗），沒有別處碰 `IConfiguration`；`mail preview` 在主機建立之前把同一份設定交給一個小容器、經 `AddPlatformMail()` 繫結，不另寫讀法。錯值或缺值在啟動就讓主機停下並指名鍵（k8s 裡是帶清楚訊息的 CrashLoopBackOff），不是第一個請求才失敗。
- 新增群組：實作 `ISectionOptions`、註冊、加 `IValidateOptions<T>`（屬性夠用時 `[OptionsValidator]`，跨欄位規則手寫）、在 `ConfigurationTests` 加一個案例。
- **鍵名是與部署庫的契約**，改名是破壞性變更。k8s 以環境變數供給（`ENVCITE_Section__Key`，前綴由 `ServiceEnvironment` 套用；不帶前綴的環境變數不進設定，`OTEL_*` 除外）。機密（模型端點金鑰、`Platform:Mail:Password`）只經環境變數，不進檔案、不提交；Api 專案沒有 `UserSecretsId`。
- 可選的功能面（營運者後台、客戶入口、有依據的回答、對話紀錄、寄信、MCP）**預設關閉**，新增的同類功能也一樣；Development 為了本機可用而開啟。關閉時對應路徑回 404，且不簽發任何 cookie。
- **系統名稱只有一個來源**：`Platform:Brand:Name`（`BrandOptions`；不可空白、25 字內、無控制字元）。介面經 `BrandText.Name` 取，信件主旨與內文（純文字與 HTML 兩版）、`mail test`、OpenAPI 描述讀 `IOptions<BrandOptions>`；`Platform:Mail:FromName` 沒設時預設用它。不在元件、信件或描述裡寫死名稱；帶名稱的文字用格式鍵（`L["登入 · {0}", Brand.Name]`），預設名稱（`BrandOptions.DefaultName`）的英文對照是 `Ui.en.resx` 的同名一鍵，自訂名稱不查資源檔、兩種語言原樣顯示。標誌 SVG 不讀設定，改名時另外換圖（見 README〈系統名稱〉）。`BrandTests` 守住預設值下輸出不變、改名後各處一起變。
- 部署環境不設 `DefaultPlan`（服務是全付費，沒方案的租戶在資料端點得 402）；Development 與測試設定它，讓剛建立的租戶能用。
- Development 的種子資料（`SeedDevelopmentData`）只在 Development 生效，且只在對應表是空的時才建；一次性密碼只經 `Console.WriteLine` 印到終端機，不進 `ILogger`，沒有預設密碼。
- **預設日誌不得蓋掉命令列的輸出**：命令列（`OperatorCli`）在服務自己的主機裡跑，結果印在標準輸出，與主控台日誌同一個畫面；`kubectl logs` 也是同一條流。所以 EF Core 的 SQL 類別（`Microsoft.EntityFrameworkCore.Database.Command`）在 `appsettings.json` 預設為 Warning。只調這一個類別、不調整個 `Microsoft.EntityFrameworkCore`：遷移進度、其他警告與錯誤仍要看得到。`appsettings.Development.json` 不把它調回去；要看 SQL 的人用環境變數（README〈日誌〉）。新增每次命令都會印的 Information 日誌之前，先看命令列輸出是否仍乾淨；`ConfigurationTests` 與 `OperatorCliTests` 守住 SQL 這一條。
- 在 TLS 終止的 proxy 後面要設 `ASPNETCORE_FORWARDEDHEADERS_ENABLED=true`，否則依來源位址限流的規則會把所有人看成同一個位址（且限流狀態是每個副本各一份）。

## Observability

- OpenTelemetry 追蹤、指標與日誌，依根 `AGENTS.md` 的約定。已認證的租戶只標在請求追蹤的 `tenant.id`，不進指標（基數無界）。
- **RED 指標**：HTTP 用 ASP.NET Core 標準的伺服器請求指標（探針以 `DisableHttpMetrics()` 排除，否則稀釋 rate 與延遲）；對 ontology／consult 的 gRPC 用戶端由 `libs/dotnet` 的 `GrpcClientMetrics` 量，隨 `AddPlatformGrpcClient` 掛上，新增上游不必另接。HttpClient 的指標看不出 gRPC 失敗（失敗仍是 HTTP 200），所以不能只靠它。標籤與伺服器端對齊（見 `proto/README.md`）。
- 探針：`/health/live` 不檢查任何東西（依賴故障不該重啟 pod）；`/health/ready` 檢查資料庫（`TenancyDbContext`，它早於租戶解析存在），故障回 503。探針請求不追蹤。
- **模型呼叫的遙測**：`IChatClient` 一律經 `AnswerTelemetry.Wrap` 接上 OpenTelemetry（追蹤與指標共用固定的 `AnswerTelemetry.SourceName`，`Observability` 登記它）。`EnableSensitiveData` **明確關閉**：框架的預設會讀 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`，環境變數一開提示詞就進了追蹤；問題文字是客戶案情，只留模型、token 數、延遲與結束原因。`AnswerTelemetryTests` 守住（含環境變數打開時）。換掉模型用戶端的註冊時要沿用這個包裝。
- **入口作答的結果計數**：`envcite.platform.answer.outcomes`（`AnswerMetrics`，與模型遙測共用 `AnswerTelemetry.SourceName` 這個 `Meter`）在每次作答得出結果時加一，回答與拒答都計，所以能算比率；標籤只有 `outcome`（`answered`、`insufficient_evidence`、`unverified_citation`、`empty_answer`，以及 consult 拒答的 `no_authority`、`out_of_scope`、`local_law_uncovered`、`missing_input`）與 `bare_topic`（原句是否被判為主題詞），值域固定；租戶、問句與命中的詞不進指標。沒有結果的呼叫（模型或 consult 暫時不可用、輸入錯誤）不計。它與 `consult` 的 `envcite.consult.ground.outcomes` 不同層：差額是模型這一步與引用核驗的損耗。`AskServiceTests` 守住標籤與值。

## Auth & tenancy

- **租戶 API**：`Authorization: Bearer <key>` → 以前綴查詢、SHA-256 加定時間比對 → `tenant` 宣告 → Finbuckle 解析租戶 → 租戶實體（`IsMultiTenant()`）自動過濾。
- **沒有營運者 HTTP API**：營運者的建立與救援走命令列（`OperatorCli`），日常管理走 `/ui`；不再有常駐的共用金鑰。
- **cookie 永遠不認證 `/v2`**；每個 `/ui` 與 `/portal` 回應都是 `Cache-Control: no-store`。預設認證方案維持 ApiKey。
- 每個租戶範圍的查詢都必須以 `TenantId` 過濾；不經審查不得停用查詢過濾。只能**按名稱**繞過租戶過濾（`IgnoreQueryFilters([TenantToken])`），且只在審查過的跨租戶查詢（API key 與目錄同步 token 的認證、對話紀錄與冪等紀錄的保存期限清理）。
- **一次性密碼**：隨機產生、只顯示一次、只存雜湊；不得進任何日誌或稽核，也不得寄信（信裡只放連結，見「邀請與信箱驗證」）。
- 登入失敗**永遠不透露**帳號是否存在、是否鎖定或停用（一個訊息；不存在的帳號仍付一次雜湊的成本）；失敗的表單**不回填**密碼或驗證碼；稽核與日誌**不記輸入的內容**（人會把密碼貼進 email 欄；稽核表只增不改），只有形如 email 的輸入才記。
- **密碼規則的唯一來源是 `PasswordPolicy`**（`SharedKernel`；規則表見 README「密碼規則」）：夠長、不含自己的 email、強度（zxcvbn）夠，**不強制組成、不定期換**（NIST SP 800-63B）；連續失敗鎖定帳號。營運者與客戶兩邊規則相同，`MembersModule.AddPasswordPolicy<TUser>()` 讓兩個 Identity 註冊用同一份驗證器（`PasswordPolicyValidator`，在內建長度檢查之後執行；一次性密碼也要過）。驗證器的錯誤只帶穩定代碼，經 `IdentityOutcomes` 轉成結果代碼；密碼與 email 不進日誌或錯誤描述。要調嚴或放寬，改 `PasswordPolicy` 的常數；提示與驗證同源，不要在頁面或文案裡另寫數字。
- **元件一律站在共用的基底與模板上，不各自寫死**（`ComponentTemplateTests` 掃原始碼守住）：
  - **基底**（`Components/Bases/`）：`PlatformComponentBase`（本地化 `L` 與系統名稱 `Brand`，由 `_Imports.razor` 的 `@inherits` 套給每個元件，不再 `@inject`）、`NavigableComponentBase`（共用的 `Navigation`）、`PlatformLayoutBase`（版面，含 `L`、`Brand` 與 `Navigation`；`PortalLayoutBase` 再集中供給入口雙版面 `Ask`、`HistoryAvailability`、`Time`、`NavContributors` 與 `CollectNavItems`）、`PageBase`（頁面的 `Error`、載入狀態 `Loading` 與包住查詢的 `LoadingAsync`、結果代碼轉文字的 `Describe`）、`StaticPageBase`／`StaticPageBase<TService>`（靜態渲染頁面的 `HttpContext`、`User` 與單服務頁面 `Service`）、`FormPageBase<TForm>`（`Input`）、`PortalPageBase`／`PortalPageBase<TService>`／`PortalFormPageBase<TForm>`（路徑上的 `Tenant` 與單服務入口頁 `Service`）、`AdminPageBase`（互動式後台頁：操作者、對話框、`Notice`、`Busy`、`RunAsync`、不可逆動作前的 `ConfirmAsync`）、`FormDialogBase`／`SecretDialogBase`（對話框：`CompleteAsync` 送出流程、Enter 送出、鎖住只顯示一次的機密、複製）。全面禁止在 `.razor` 使用 `@inject`（`ComponentTemplateTests` 守住）：單服務薄頁／共用流程頁繼承泛型基底 `StaticPageBase<TService>`／`PortalPageBase<TService>`，其餘具依賴的元件一律以 `.razor.cs` 主建構子 DI（Primary Constructor）注入。
  - **版面模板**（`Components/Shared/`）：`DocumentTitle`（分頁標題「名稱 · 系統名稱」）、`PageHeading`（h1）、`FormAlert`（訊息，錯誤是 `role=alert`）、`PostForm`（POST 表單）、`FormField`（標籤與說明）、`SubmitButton`、`DialogError`、`DialogSubmitActions`、`NewPasswordField`、`PasswordField`、`AskComposer`（問答輸入框與範例）；區塊與資料的模板 `SectionCard`（卡片與標題）、`StatGrid`／`StatTile`（摘要磚，吃 `StatItem` 清單）、`DataTable<TItem>`（欄位以 `ColumnDef<TItem>` 清單宣告，`Plain` 是入口的純表格外觀）、`LoadedView`（失敗、載入中、查無、有資料四態）、`SelectField`（表單的下拉欄位）、`OptionSelect`（表格列裡的原生下拉）、`CheckboxField`（勾選欄位）、`SecretReveal`（只顯示一次的機密與複製鈕，靜態頁用）、`SourceCards`（一次回答的法源卡片：有答案時是出處，找到法源但不足以回答時照樣列出）；`PageTabs`／`TabPanel`（詳細頁依主題分頁：分頁是 `?tab=` 連結，面板都在文件裡、沒選到的 `hidden`，`TabItem` 在 `Presentation/`）；`StatItem`、`ColumnDef`、`SelectOption` 在 `Presentation/`。這些標記只在模板裡寫一次，頁面與對話框不自己寫 `ep-alert`、`ep-field-row`、`ep-label`、`ep-form`、`<PageTitle>`、`ep-card`、`ep-stats`、`ep-plain-table`、`ep-secret-row`、`ep-tabs`、`ep-tab-panel`、`ep-composer`、`<select>`、`<InputCheckbox>`，也不自己呼叫 `ShowMessageBoxAsync`。新頁面要的版面缺了模板，先補成共用元件再用。
  - **營運者與客戶入口共用的流程**只寫一份，元件放 `Components/Shared/`：登入 `LoginFlow`、第二步驗證 `TwoFactorSignInFlow`、兩步驟驗證設定 `TwoFactorSetupFlow`、改密碼 `ChangePasswordFlow`、啟用連結 `ActivationFlow`。兩邊的帳號服務都實作 `IAccountLifecycle`（含 `SignInAsync`，結果是同一個 `SignInStatus`），兩步驟驗證服務都實作 `ITwoFactor`；流程元件只依賴這兩個介面與 `SignInRoutes`（兩邊的去處與 `SafeReturnUrl`，防 open redirect 的規則只有這一份）。頁面只剩外框（標題、卡片）、路由與參數，沒有自己的程式碼；入口多出來的部分（`IPortalTwoFactor` 的角色要求與關閉）由流程元件依服務型別決定，不在頁面分支。服務層同樣：兩步驟驗證的註冊、啟用、復原碼與敏感動作前的再確認寫在 `TwoFactorBase`，`OperatorTwoFactor`／`PortalTwoFactor` 只補「怎麼驗證一組碼」「啟用後的 session」「稽核寫什麼」；登入的第二步機制不同（營運者是自製的暫存憑證加重放防護，入口用 Identity 原生流程），不為了合併而改行為。
  - 表單的內容類別（`LoginForm`、`CodeForm`、`DisableForm`、`PasswordForm`、`ActivateForm`）在 `Bases/Forms.cs` 兩邊共用；欄位名稱就是 POST 的欄位名稱，改名會讓送出對不上。
- **設定或修改密碼的欄位一律用 `Shared/NewPasswordField` 模板**（它包 `PasswordField`，加一份三條的規則清單，數字讀 `PasswordPolicy`）：頁面不自己寫 `minlength`、「至少 N 個字元」或清單。長度回饋是漸進增強（`forms.js`，文字與最短長度由伺服器寫在 `data-*`），沒有 JavaScript 時說明與 `minlength`／`maxlength` 仍在；螢幕閱讀器只在長度剛夠時被通知，不逐字念；只有長度能即時檢查，其餘由伺服器擋下，清單不假裝即時。`PasswordFieldTests` 掃原始碼守住。

### 營運者帳號（`/ui`）

- 有**自己的使用者儲存**（營運者不屬於任何租戶，不是租戶實體，無租戶過濾）；Identity 的金鑰型別選 `Guid`，是為了不與成員表的 `IdentityUser…<string>` 型別混成同一個繼承階層；儲存手動註冊。
- **不開放自助註冊**：帳號只由 `/ui` 的營運者頁或命令列建立，兩條路都需要已被信任的身分（已登入的營運者，或能在服務環境執行指令的系統管理者）。
- **先有只有本人知道的密碼，再綁驗證器**，順序固定（驗證因子不會設在系統管理者知道的密碼後面）：暫時密碼路徑，首次登入強制改密碼再綁；邀請路徑，密碼是本人自己設的，登入後直接綁。**TOTP 對每位營運者強制**，沒有角色豁免、不能關，邀請也不例外（啟用不簽發 session）。
- **授權每個請求都重查資料庫**：帳號存在、未停用、security stamp 仍有效；是否還要改密碼、是否已綁驗證器，由**資料庫**決定，不看 cookie 宣告。所以停用、重設密碼、重設驗證器在**下一個請求**生效；重新啟用也旋轉 stamp，舊 cookie 不復活。已開著的互動式電路不是 HTTP 請求，由 `PlatformAuthenticationStateProvider`（`RevalidatingServerAuthenticationStateProvider` 模式，每分鐘）重驗，兩種身分各驗各的：入口成員（有 `PortalUserId` claim）走 `IPortalSessions`，其餘已登入者一律當營運者驗——不認得的身分是失效，不是放行。
- **session 有絕對期限**（`Platform:Ui:SessionMaxAge`）：cookie 雖滑動續期，從登入起算超過上限一律拒絕並清掉 cookie。起點存在票的屬性（`OperatorSessionLimit.StartedAtKey`），不用 `IssuedUtc`（續期會重寫它）；沒有起點的票視為無效。時間走 `TimeProvider`。
- 登入、第二步、改密碼與安全設定頁是靜態 SSR 的 POST 表單（`[ExcludeFromInteractiveRouting]`、antiforgery），受依來源位址的限流政策約束。
- **登入的稽核是 fail closed**：事件先於 session cookie 寫入，寫入失敗就沒有 session。
### 客戶入口（`/portal/{tenant}/…`）

- 租戶在路徑裡，於登入前由委派策略解析（路徑優先，API key 宣告其次）。頁面是**靜態 SSR**（無電路）：每個請求自行解析租戶並認證，表單是附 antiforgery 的普通 POST；唯一的例外是**工作區**（問答首頁與對話頁，見〈問答〉），它是 Interactive Server（保留預先渲染）。
- 使用者是租戶實體（`MultiTenantIdentityDbContext`）：同一個 email 在兩個租戶是兩個帳號；角色是欄位，不是 Identity 角色。
- session cookie 的授權只讀宣告；**cookie 的租戶必須等於請求路徑解析出的租戶**（此檢查被使用者表的租戶過濾端到端遮蔽，所以有獨立的單元測試，每個檢查都做過突變測試）。每個入口請求檢查 security stamp，所以改密碼或重設會立即結束成員的其他 session。
- **「請求所屬的租戶」有兩個來源**（`PortalMemberHandler`）：HTTP 請求是租戶解析的結果（環境租戶，精確比對）；互動式 circuit 裡的頁面切換沒有環境租戶，由 `AuthorizeRouteView` 把路由資料當授權資源傳進來——`Routes.razor` 必須設 `Resource="routeData"`（測試掃原始碼守住；少了它，circuit 裡的入口頁一律被拒），租戶取自路由值 `Tenant`（不分大小寫：網址是使用者打的，識別碼只有小寫）。資源是路由資料時**只看路由值、不看環境租戶**，路由裡沒有租戶就拒絕。
- **circuit 的重驗與 cookie 驗證是同一份檢查**（`PortalSessionValidator.IsValidAsync`：帳號在、沒停用、戳記沒變）；差別只在呼叫者。`PortalSessions` 沒有 HTTP 請求，依 principal 的租戶 claim 經 `TenantScopes.InByIdentifierAsync` 開租戶 scope 再檢查；沒有租戶 claim、租戶不存在、帳號在別的租戶一律無效。停用與重設都會換戳記，所以「停用旗標」那一層要用不動戳記的測試單獨證明（`PortalSessionsTests`）。
- **circuit 的連線（`/_blazor`）兩張 cookie 都認，進 circuit 時只留一個身分**：入口的工作區是互動式頁面，連線沒有路徑上的租戶，瀏覽器又可能同時帶著營運者與入口成員的 cookie，而連線的網址看不出這條 circuit 是為哪一頁開的。所以 `UsePlatformWebAuthentication`（營運者）與 `UsePlatformPortalAuthentication`（入口）都在 `/_blazor` 認證，把兩個 identity 併進同一個 principal（`CircuitIdentities.Combine`）；circuit 啟動時由 `PlatformAuthenticationStateProvider` 依**第一個網址**挑一個（`/portal` 底下只留入口成員，其餘只留營運者，`Narrow`），之後授權與重驗只看得見那一個。兩邊的 claim 型別大量重疊（`NameIdentifier`、`Email`、安全戳記、待改密碼與待註冊驗證器的旗標），所以兩個 identity 不能一起留在授權看得到的 principal 裡；挑不到要的那一邊就是匿名，不退而求其次。連線上的入口 cookie 沒有環境租戶，`PortalSessionValidator` 在沒有環境租戶時改用 cookie 自己的租戶 claim 檢查（與 circuit 的重驗同一條路），失效就收回 cookie。circuit 失效後的導向看路徑（`RedirectToLogin`）：入口的頁面回入口的登入頁。`CircuitConnectionTests` 與 `PlatformAuthenticationStateProviderTests` 守住這些。
- 三條規則：(1) 入口 cookie 由 `UsePlatformPortalAuthentication` 認證，必須在 `UseMultiTenant` **之後**，否則 scoped 的 `PlatformDbContext` 在租戶已知前就被解析，整個請求都帶著沒有租戶的 context；(2) 租戶 context 是 `AsyncLocal`，`MemberAdmin` 在呼叫它的方法內綁定，不在 async 輔助方法裡（輔助方法返回時值會被丟棄）；(3) 營運者建立的成員，暫時密碼路徑要先改一次性密碼才能用，之後才綁驗證器；邀請路徑的密碼是本人自己設的。
- 依來源位址的限流刻意寬鬆（機關或顧問公司常共用一個對外位址），它擋的是自動化猜測，不是正常使用。

- **Google 登入**（契約在 [`README.md`](README.md)「Google 登入」）：用框架的 Google handler 與 Identity 原生的外部登入（`SignInManager` 的 `ExternalLoginSignInAsync`、`AspNetUserLogins`），不自製 OAuth。不變式：(1) 全站一個回呼 `/signin-google`（在 `/portal` 之外：放進去會被當成租戶識別碼），租戶只靠受保護的驗證屬性（`PortalGoogle.TenantItem`）帶到第二段，第二段比對它與路徑上的租戶；(2) **不建立帳號**，只對應到已啟用的既有成員，身分以（提供者、`sub`）為準，email 只在第一次連結時用、且要 Google 說已驗證；(3) handler 一律註冊但關閉時給佔位的 ClientId（空值會讓每個請求丟例外），起點與第二段在關閉時回 404，固定回呼的失敗（`OnRemoteFailure`）沒有租戶就回 404；(4) 失敗只有一種結果，稽核只留原因與成員 Id；(5) 本地的兩步驟驗證與鎖定照舊，不信任 Google 的 MFA。
- **外部登入的鍵必須含租戶**：`AspNetUserLogins` 的鍵是（提供者、提供者給的 Id），不像 Identity 其他表由全域唯一的 `UserId`／`Id` 帶路，所以 `PlatformDbContext` 用 Finbuckle 原生的 `AdjustKey` 把 `TenantId` 併進主鍵（同一個 Google 帳號可以是兩個租戶的成員）；`PlatformDbContext` 因此在建構時呼叫 `EnforceMultiTenantOnTracking`。新增主鍵不含全域唯一值的租戶實體時照做。

### 入口的 MFA（TOTP，RFC 6238）

- 登入走 Identity 原生的兩步驟流程（待驗證 cookie 短效、不滑動、授權不認它）。**預設關閉，由使用者在安全頁自行開啟**；驗證與復原碼全用 Identity 原生，不自製 TOTP 驗證與重放防護（Otp.NET 僅營運者後台使用）。
- 必須保持且有測試的規則：(1) 密碼與驗證碼共用一個失敗計數器，密碼正確不歸零，要第二步也過才歸零（原生行為，測試守住）；(2) 金鑰以 `GenerateNewAuthenticatorKey` 產生、`SetAuthenticationTokenAsync` 存放，**不用** `ResetAuthenticatorKeyAsync`——設定頁在渲染中執行，它旋轉 stamp 後無法刷新 cookie，會結束該 session；(3) 待驗證 cookie 不能跨租戶使用；(4) 沒有金鑰、驗證碼、復原碼或 `otpauth` URI 進稽核或日誌（測試同時捕捉兩者）。
- QR 碼在伺服器端繪製，金鑰不交給第三方 QR 服務；復原碼只顯示一次。
- 要對擁有者與管理員強制 MFA 須明確開啟 `Platform:Portal:RequireMfaForAdmins`；被要求的角色不能自己關閉，營運者重設會把他送回綁定流程，而不是留下無保護的帳號。順序同樣是先改一次性密碼、再綁定。

### 邀請與信箱驗證

- 流程、表格與連結規則由 [`README.md`](README.md)「邀請與信箱驗證」擁有；以下只記不變式與其理由。
- **登入不要求信箱已驗證**（邀請功能之前建立的帳號全是未驗證，強制會鎖人）；未驗證的信箱不得作為復原管道（目前沒有以信箱復原的流程，新增時必須先檢查）。
- 建立帳號的呼叫端（命令列、後台、客戶入口）依 `IMailSender.Enabled` 在邀請與暫時密碼兩條路中選一條；服務層的 `InviteAsync` 在沒啟用時回 `mail.not_enabled`。邀請的帳號沒有密碼（`PasswordHash` 為 null），新增登入或復原流程時要處理這種帳號。
- 連結的權杖就是 Identity 的電子郵件確認權杖（資料保護、綁使用者 Id 與安全戳記、限時 `Platform:Mail:LinkLifetime`）：**不自製、不存**。用過、過期或戳記改變就失效。
- **GET 只檢視、不消耗，POST 才消耗**。頁面回 `Referrer-Policy: no-referrer`，套用登入的限流；連結已無效時頁面仍渲染一張無欄位的隱藏表單，連按兩次送出才不會掉到框架的錯誤頁。
- 設密碼用 `AddPasswordAsync`：成功會換安全戳記，連結就此失效，並發的第二次提交因為「已經有密碼」而失敗。啟用**不簽發 session**：營運者登入後仍須綁定驗證器，入口的兩步驟規則不變。
- 重寄：還沒有密碼的先換安全戳記（先前的連結失效，此時沒有 session 會被波及）；已有密碼的寄驗證連結，不換戳記。已啟用且已驗證則 `account.already_active`；停用的 `account.disabled`。
- 連結與權杖不進日誌、稽核與命令列輸出；寄信失敗不使建立或重寄失敗，結果以 `MailDelivery` 回報。
- `mail test` 失敗只指向日誌，不印例外訊息（可能回顯位址）；`Platform:Mail` 只在啟用時驗證，未啟用時其餘鍵被忽略。
- **寄信的設定與傳輸**（鍵的語意與排查在 README「寄信」，以下只記不變式）：
  - **設定分三個選項類別，各自驗證，鍵都在 `Platform:Mail`**（拆類別不改鍵）：`MailOptions`（啟用、`BaseUrl`、`LinkLifetime`，呼叫端讀）、`MailTransportOptions`（`Host`、`Port`、`Security`、`Username`、`Password`、`TimeoutSeconds`，只有傳輸層讀）、`MailIdentityOptions`（`From`、`FromName`、`ReplyTo`），版型仍是 `MailTemplateOptions`。三個驗證器都只在啟用時檢查。跨類別的規則放在註冊（`From` 的預設是 `PostConfigure`）與 `MailSettingsAdvisor`（警告），不放進寄信的方法。預設值只放在選項類別，與 README 鍵表一致；程式裡不寫死位址、帳號、埠。
  - **傳輸可替換**：`SmtpMailSender` 向 `ISmtpClientFactory` 要用戶端，**每封信一個新的**（`SmtpClient` 不是執行緒安全，不當單例共用）；EHLO 名稱由寄件者位址的網域決定。實作類別 `internal`，對外只有介面與選項，全部在 `AddPlatformMail()` 註冊，Program.cs 不接任何寄信的線。測試用 DI 換掉工廠或傳輸，不手動 new。
  - `Password` 是機密：只經環境變數（k8s 的 `secretKeyRef`），**只有傳輸層讀它**，其他程式只問 `HasCredentials`；**不得出現在日誌、稽核、例外訊息、`mail test` 輸出、選項的字串化與序列化、任何診斷端點**，`Username` 同樣不進日誌與輸出。驗證訊息只指名鍵、不帶值。新增任何會印出或記錄寄信設定的程式，要有測試捕捉日誌與輸出、斷言不含帳密。
  - `Username` 與 `Password` 要嘛都設、要嘛都不設；沒設就**不送 AUTH**（即使中繼公告了它），維持現有部署的行為。`Security` 沒設維持 `None`，不因預設改變而讓既有部署寄不出信。
  - `Security` 是 `None` 或 `StartTlsWhenAvailable` 而設了帳密：啟動記警告（`MailSettingsAdvisor`），`mail test` 同樣印警告；不停下，因為內網中繼可能就是這樣。
  - 協商交給 MailKit：**不自己寫 SASL 或 STARTTLS，不設 `ServerCertificateValidationCallback` 放行憑證**。內部 CA 簽的憑證，做法是把 CA 放進容器信任（README「加密與帳密」）。
  - 寄件者必須等於認證的帳號：`From` 沒設且 `Username` 是 email 時預設為 `Username`，兩者不同只警告，因為有些中繼允許 Send As。
  - `mail test` 的說明文字在 `MailTexts` 資源檔（`MailTest.` 鍵，經 `IMailTestReport`），不在命令列程式裡；失敗原因是 `MailTestReport` 裡的一張表（原因對應 SMTP 狀態碼，文字在 `MailTest.Cause.{名稱}`），新增原因只動表與兩份資源檔。日誌的狀態碼是數字（SMTP 狀態碼與增強狀態碼），不記例外訊息。
- **信件分三層，各自單一職責，全部走 DI**：呼叫端只組資料（`ActivationMailModel`，或 `IActivationMailer` 的參數）；`IMailComposer` 把資料加文案、系統名稱與版型組成 `ComposedMail`（主旨、文字版、HTML 版），不寄信；`IMailSender`（`SmtpMailSender`）只管傳輸，收 `MailContent` 寄成 `multipart/alternative`（文字在前）。沒有 static 的組信工具類；新增一種信是在 `IMailComposer` 加一個方法與一個 Razor 頁面，不在呼叫端拼字串。註冊只在 `AddPlatformMail()`。
- **文案在 `MailTexts` 資源檔，不在程式裡**：預設繁體中文、`.en.resx` 英文，鍵是領域鍵、帶參數用 `{0}`；兩個語言的鍵與參數個數一致，由 `MailComposerTests` 守住（缺鍵時組信丟例外，不會把鍵名寄出去）。信的語言取自 `MailTemplateOptions.Culture`，**不取自請求的語言**（操作者用英文介面，收信的人不一定）；組信只在同步取字串的區段暫時換文化，不跨 await。改一版的事實（連結、期限、提示、租戶）時另一版同改。
- **版型的長相是選項（`Platform:Mail:Template`），不是程式裡的常數**：色票、寬度、內距、字型都有預設、可由設定覆寫，值會進內嵌樣式與樣式表，所以由 `MailTemplateOptionsValidator` 驗證格式（顏色只收 `#rrggbb`、字型只收安全字元）與每組色票的文字對比度（至少 4.5:1），錯了在啟動停下並指名鍵。系統名稱只經注入的 `IOptions<BrandOptions>`。
- **HTML 由 Razor 元件加 `HtmlRenderer` 渲染**：參數是強型別的 `MailPage`，插入的值由 Razor 編碼。`MarkupString` 只用在條件註解、VML 與樣式表，內容只能是固定文字、整數與已驗證的顏色，**不得放使用者或設定來的自由文字**。給 `HtmlRenderer` 的 `HtmlEncoder` 放行 Unicode 全範圍（預設只放行基本拉丁字母，會把整封信變成 `&#x…;`），危險字元照樣編碼。
- **信裡唯一的外部網址是啟用連結**：不放遠端圖片、追蹤像素、點擊追蹤轉址、腳本或外部樣式。連結等同一次登入的鑰匙，經第三方或被預先載入都會洩漏或用掉它；`MailComposerTests` 守住（含 `<` `"` `&` `'` 的名稱與租戶、四種邀請／驗證與租戶組合）。信裡不放密碼；日誌與稽核不記連結與內文。
- **版面維持表格加內嵌樣式**（傳統 Outlook 以 Word 引擎渲染：不支援 flexbox、grid，`max-width` 要靠條件註解的後備表格），按鈕的 VML 後備與深色配色（`prefers-color-scheme`）跟著版型一起維護；預設色票的對比度另有測試以獨立的算法驗證。改版型後用 `mail preview` 看過，並請人以真實客戶端（傳統 Outlook、各家深色模式）收一封實際寄出的信。
- **`mail preview` 在建立主機之前處理**（`Program.cs`），所以不連資料庫、不需要啟用 `Platform:Mail`；它用 `AddPlatformMail()` 註冊的真實 `IMailComposer`（不解析 `IMailSender`），所以預覽與實際寄出的是同一份組信邏輯與選項。占位連結的網域必須是保留網域（`.example`），權杖是假值：預覽檔案不得含任何真實連結。

### 成員管理（客戶自助與營運者）

- 營運者路徑與客戶自助路徑共用 `MemberCore`。
- 規則（每條都有測試）：**階層**——擁有者管理所有人；管理員只管理較低的角色，且只能授予那些角色（能管理的就是能授予的，沒有人被升到管理者之上）；**沒有人對自己操作**（停用、改角色、重設）；**操作者的角色讀資料庫，不讀 cookie 宣告**；別的租戶的成員就是找不到。
- 停用的成員無法登入（與密碼錯誤相同的泛用失敗）、所有開著的 session 立即失效（stamp 旋轉**且** session 檢查看旗標本身）；帳號與稽核紀錄保留。啟用與停用都旋轉 stamp，舊 cookie 不復活。

### 目錄同步（身分來源推送成員）

- 契約與規則表由 [`README.md`](README.md)「目錄同步」擁有；以下只記不變式與其理由。程式在 `Modules.Members/DirectorySync/`，不另立模組：邀請連結、停用與階層規則都是 Members 的 internal，模組之間不互相參考。
- **租戶只來自 token**，不收路徑、查詢字串或本文裡的租戶。服務一律在「目前請求的租戶」內執行（`IDirectoryProvisioning` 是 scoped，營運者版 `IDirectoryAdmin` 自己開 scope 綁定目標租戶，做法同 `MemberAdmin`），別的租戶的成員與 token 就是找不到。
- **稽核的「誰」是 token**（`AuditActors.Directory`，`ActorId` 取自已認證身分的 `directory_token_id` claim，絕不取自本文）。`Detail` 不放 SCIM 本文、token、連結或身分來源送來的其他屬性（有測試）。
- **一個請求一個交易**：成員的變更、狀態計數與稽核事件一起成功或一起失敗，稽核寫不進去就沒有變更（fail closed），身分來源收到錯誤會重送，所以每個動作都冪等；沒有變化的重送什麼都不寫。邀請信在交易內寄、不使佈建失敗，結果以 `MailDelivery` 記進稽核。計數用 SQL 的原子遞增，不讀了再寫（身分來源會平行送很多請求）。
- **同步不擴大權限**：角色最高到管理員（`DirectoryRoles.Assignable`），擁有者只由人指派，也不接管擁有者；最後一位啟用中的擁有者不能被同步停用或降級；階層檢查之外，設定與 token 只有擁有者能管（管理員不能建立管理員，所以不能持有能這麼做的憑證）。
- **缺席不是離職**：身分來源久沒來只在介面警示，不自動停用任何人。
- **SCIM 入口（`/scim/v2`）自成一面**：token 是自己的 authentication scheme（`DirectoryToken`）。租戶解析早於授權，而它不是預設 scheme，所以 `TenancyModule` 的租戶策略對這個路徑明確驗證它；handler 把同一個請求的驗證結果放進 `HttpContext.Items`，租戶解析與授權只查一次資料庫。失敗一律同一個 401、不透露原因；功能面沒開時 handler 什麼都不認（`NoResult`），路徑也不掛。
- **限流依 token，在授權之後**（端點過濾器，`DirectoryRateLimiter`），不用全域的 `UseRateLimiter`：全域的 `OnRejected` 只有一個，由入口的限流占用，而且沒有有效 token 的請求不該耗用額度。指標標籤只有 `operation`、`outcome`，租戶與內容不進指標。
- 請求本文只在端點解析、用完即丟：日誌、指標與稽核都不記本文、token 或身分來源送來的屬性（`ScimEndpointTests` 捕捉所有日誌斷言）。PATCH 寬鬆接受各家寫法、略過不認得的屬性——一個多餘的屬性不該讓整個同步失敗；`userName`（email）不能變更。
- **受管成員的角色與停用鎖在人手之外**（`MemberAdmin`、`MemberSelfService` 都擋，回 `directory.member_managed`），否則下一輪同步就悄悄蓋掉人的修改；解除管理連 `ExternalId` 一起清，身分來源之後才接管得回去。
- **群組對映的角色重算只有一條路**（`DirectoryGroupRoles.RecomputeAsync`）：群組的成員變動、群組被刪、擁有者改對映都經它，而且一定在呼叫端已開的交易裡（成員的變更、狀態計數與稽核一起成功或一起失敗，稽核寫不進去角色也不變，有測試）。只動受管成員、不動擁有者；取最高而不是疊加，所以順序與重送都不影響結果。`SCIM` 不指定角色、群組也不能由身分來源對映：對映是客戶的擁有者的決定（同設定與 token 只有擁有者能管的理由——身分來源設錯一個群組不該直接變出一批管理員，而管理員的上限已在 `DirectoryRoles.Assignable`）。
- **介面只呈現，不另立規則**（規則表由 README「目錄同步」擁有）：`Shared/DirectoryStatusCard` 是營運者的租戶頁與客戶入口共用的唯讀狀態卡，由 `SectionCard`、`StatGrid`、`DataTable`、`StatusBadge`、`CopyButton` 組成；動作（簽發、撤銷、緊急關閉）由嵌它的頁面經 `Actions`、`TokenActions`、`ChildContent` 放進來，所以靜態入口（普通表單）與互動後台（按鈕）共用一張。顯示與否看 `IDirectoryAvailability`（SharedKernel，singleton 只讀設定，同 `IAnswerAvailability`），**存取控制在服務層**：頁面藏起來不是授權，非擁有者直接送出表單一樣被 `directory.owner_only` 擋下。
- **新簽發的 token 只在那個回應裡顯示一次**（`SecretReveal`，重新整理就沒了），不進稽核、日誌與狀態；狀態只給前綴。勾選欄位沒勾就不會送出，所以表單的預設與「沒送出」都是 false；表單的模型類別只能有一個無參數建構式（框架的表單對映遇到兩個建構式會丟「No converter registered」，且是在送出時才發現）。
- token 與 API key 是兩種憑證（`BearerSecret` 共用格式與雜湊，標記不同），互不通用，不靠 `ApiKey.Scopes` 區分：scope 是同一種憑證內的能力，擋不住拿錯種類的憑證去呼叫另一邊。

## Plans & access

- **方案指派是資料，不是設定**：`TenantEntitlements`（不是 Finbuckle 租戶實體）。方案**定義**（限額）在 `Platform:Plans`；**誰在哪個方案**由營運者在執行期設定，立即生效並寫入稽核。查找順序：資料庫指派 → 設定的 `TenantPlans` → `DefaultPlan`。
- 指派到已從設定移除的方案，視為**沒有方案**（fail closed，記警告），不讓每個請求都失敗。限流分區鍵含方案名稱，所以換方案立即換限流器。
- **全付費、無免費層**：資料端點需要方案，沒有則回 `402`（problem+json），在授權階段決定、**早於限流**，所以沒方案的租戶不耗用任何額度。管理端點（自己是誰、用量、API key）對任何已認證租戶開放——沒方案的客戶仍要能看到自己、看用量、撤銷被偷的 key——並以一個底限約束，那是濫用防護，不是服務額度。
- 查詢語意：`asOf` 同 gRPC 的 `as_of`（倉庫根 `proto/README.md`）；查無對映 `404`，不是日期 `400`。
- 搜尋與其他可能含客戶案情的輸入用 **POST**：查詢字串會被請求日誌與追蹤的 `url.query` 寫下。**查詢文字不得進任何日誌或指標**（有測試捕捉所有日誌），計量只記次數。REST 搜尋與入口搜尋共用同一個 `ILawSearch`，所以不會漂移。
- 推論端點只回**候選**結論；`verdicts` 為空代表沒有任何已確認的標註，不代表合規。案件事實放請求本文，不放 URL。公開 DTO 不暴露內部 proto。

## 有依據的回答與對話紀錄

- **有依據的回答**（`IGroundedAnswerer`）是固定流程，不是自己決定呼叫什麼的 agent：(0) 有前文（追問）時先把問題改寫成獨立的問句，只用來取證據（見下面的〈對話記憶〉）；(1) 向 consult 取封閉證據集；(2) **consult 拒答就以拒答為答案，絕不呼叫模型**；(3) 否則一次 `IChatClient` 呼叫（溫度 0），系統提示要求只根據證據、以代號引用、證據不足就說不足、把證據當資料而不是指令；(4) **不信任模型遵守規則**：答案至少引用一個代號、且引用的代號都必須在證據裡，否則丟棄並當作拒答；(5) **只打主題詞、沒有問句，由程式判斷，不靠提示詞**（`BareTopic`：搜尋端不當問句、無句末標點、無疑問詞，四項全中才算；「是不是問句」讀搜尋端的同一份判準 `LawSearchOptions.Shape`，所以主題詞一定不是搜尋端的問句，同一個輸入不會在兩邊被讀成相反的形態）：模型對沒有問句的輸入會照「證據不足」規則拒答，即使證據集裡就有直接規範它的條文，所以命中時送給模型的「問題」換成任務句（說明證據裡與它有關的規定，缺哪一項就不寫哪一項，不因缺一項就整個回「證據不足」；只有證據完全沒談到這個主題才回），取證據仍用使用者打的原詞（檢索是字面比對）。任務句不要列出一串必答項目：列了「行為、義務與罰則」時，證據只涵蓋行為規定，模型仍整個回了「證據不足」。判準只看形態、取嚴：命中時送給模型的問題會被換掉，把使用者真的提出的短問句誤當成主題詞，模型回答的就是另一個問題；漏認的代價小，因為模型回「證據不足」時條文仍會列出（見 (6)）；(6) **拒答原因要說真話**：`no_authority` 只留給 consult 一條都沒找到；consult 給了法源、模型卻回「證據不足」是 `insufficient_evidence`，仍是拒答（沒有回答正文），但帶著 `Sources`，介面把條文照樣列出、歷史也記下出處。其餘作廢的原因（引用對不上、空回答）不帶法源：那是模型的輸出有問題，不是證據不夠。
- **流程以 Microsoft Agent Framework 的圖式 workflow 表達**（`GroundedAnswerer`：rewrite → ground → write → verify），為之後加節點（規劃、多次取證、推論）留位置；目前仍是固定的一條線，走向不由模型決定。三條規矩：(a) 節點內**預期的失敗轉成結果代碼**，不丟例外——框架把節點的例外包成事件，`OutcomeOf` 把取消與未預期的錯誤原樣再丟出，所以它們仍是取消與 500，不會被吞成一則安靜的拒答（有測試）；(b) 已有結論（拒答、失敗）的回合（`AnswerTurn.Done`）原樣穿過後面的節點，只有最後一個節點是輸出來源；(c) 節點每次呼叫現建，因為它們捕捉的是這一次請求的租戶與 scoped 服務。節點之間傳的 `AnswerTurn` 帶問題文字，不實作會被印出的 `ToString`。只採用框架已穩定的部分；尚未穩定的轉接器與託管（AG-UI、DevUI、Durable）不引入，要用先確認它已穩定。 **等待時說明進行到哪一步**：`IGroundedAnswerer.AnswerAsync` 與 `IAskService.AskAsync` 收 `IProgress<AnswerProgress>`，每個節點開始時回報一次（改寫追問、取證據、寫並帶找到幾條、核對出處；沒有前文就沒有改寫這一步）；回報只有步驟與條數，不帶問題與證據，已有結論的回合（拒答、失敗）不回報後面的步驟。不串流模型的字：引用要整段核對過才能給人看，核對不過的回答會被捨棄，先送出去的字收不回來。
- **對話記憶（短期記憶）用 .NET 通用的型別，不另訂一套**：前文是 `IReadOnlyList<ChatMessage>`（User 是問題、Assistant 是回答，由舊到新），經 `IAskService.AskAsync` 與 `IGroundedAnswerer.AnswerAsync` 傳入；`IChatClient` 本來就無狀態，每次呼叫由我們帶訊息。五條規矩：(a) **前文不是證據**：系統提示明說只用來理解問題，`Verify` 不變（只認這一輪證據集的代號）；(b) **前一輪的代號一律剝掉**（`GroundedAnswerer.PriorOfAsync`）：代號只在那一輪的證據集裡有意義，留著模型會照抄，而這一輪的 `L1` 可能是別的條文，會騙過核驗；`ChatSession` 先把代號換成法規名，服務端再剝一次當保險；(c) **長度交給 MEAI 的 `IChatReducer`**（`MessageCountingChatReducer`，只留最近 `HistoryTurns` 輪），再加每則 `HistoryChars` 的截斷，不自己切；該類別在 MEAI 11 仍標評估用（`MEAI001`），只在 `ConsultModule` 的註冊那一行放行，介面本身是穩定的。**不用 `SummarizingChatReducer`**：摘要多一次模型呼叫，而且摘要會帶進沒有出處的說法，違反接地；(d) **追問先改寫再取證據**（`rewrite` 節點）：檢索是字面比對，「那罰款多少」單獨看找不到任何條文；改寫只用在取證據，作答與核驗用使用者的原句；**改寫不把前面回答裡的條號、金額與日期帶進問句**（只有使用者自己指「這一條」「第 N 條」才保留）：帶進去的條號會把字面檢索拉回上一題的條文，而追問常是在問別條（「那罰款多少」的上一題引用的是禁止規定，罰則在別條），這在真實模型上看過；沒有前文就原樣放行、不呼叫模型；改寫失敗不讓回合失敗，用原句繼續；改寫的 token 記在同樣兩個 token 操作上，不另算次數；(e) **前文的來源是 `ChatSession` 的回合**（連線記憶體），不是資料庫：歷史沒開啟也有記憶，從歷史載入的舊對話則由存下來的結構還原。不用 Agent Framework 的 `AgentSession`／`ChatHistoryProvider`：我們是固定流程不是 agent 迴圈，而它的 session 序列化會把整串訊息（含證據）存起來，與「不存條文本文」衝突；日後若改成 agent 迴圈，`ChatHistoryProvider` 的 Provide／Store 對應 `ChatSession` 的前文與 `RecordAsync`。
- **兩個開關都要開**：部署者的 `Platform:Answer:Enabled`，加租戶自己的同意（`TenantEntitlement.AiAnswerEnabled`，預設關，因為問題與證據會離開我們的系統）。同意由營運者設定並在同一交易稽核；它**跟著資料庫裡的方案列**：沒有指派方案的租戶開不了，移除方案連同同意一起刪除。呼叫端負責檢查「同意且可用」。
- 模型或端點失敗是「服務不可用」，不存入對話紀錄；**例外訊息不進日誌**（端點可能回顯提示詞），只記例外型別與狀態。
- 引用檢查證明代號存在且至少用了一個，**不證明每一句都有依據**；介面要提示讀者對照引用卡片。
- **頁面不直接碰回答、搜尋與歷史這些領域服務，一律經 `IAskService`**（`SharedKernel` 的契約，`Modules.Conversations` 實作，singleton）：方案與同意的檢查（`AskCapabilities`）、回答或搜尋（`AskAsync`）、記進歷史（`RecordAsync`）。每個操作經 `TenantScopes.InByIdentifierAsync` 在**呼叫者的租戶**裡執行，租戶只取自入口成員的 claim；沒有 `PortalUserId` claim 的身分（匿名、營運者、API key）一律當作沒有方案，不呼叫任何服務、不計量（fail closed）。靜態頁與互動式 circuit（沒有環境租戶）用同一份，頁面只剩呈現與表單。服務回的是結果（`AskResult`：模式、狀態、回答或條文）；使用者看到的文字與記進歷史的文字由 `ChatSession` 組，語言是提問當下的介面語言。`AskServiceTests` 直接在服務層證明租戶隔離、計量、同意與 fail closed。
- **對話紀錄**預設關閉（問題文字是客戶案情的內容）。只存問題（連同問的是哪一天）、回答正文、結果種類與**引用**（代號、法規名稱、條文 URI；`Citations` 是 jsonb 的擁有型別集合），不存條文本文。**只有作者看得到**自己的對話——擁有者也不行；每個方法在租戶過濾之外再依使用者 id 過濾；不是自己的 id 當作「開新對話」處理，所以不能拿來探測或寫入別人的對話。刪除由作者執行並在同一交易稽核（只記 id，不記內容）。保存期限清理是跨租戶的，只刪除，只記筆數。**釘選、重新命名與封存只動 `PinnedAt`／`Title`／`ArchivedAt`，不動 `UpdatedAt`**（單一 `ExecuteUpdate`）：它們不是新的活動，不改排序也不延長保存（封存的對話照樣超過保留天數就刪）；封存同時清掉釘選，在封存的對話接著問（`AppendTurnAsync`）是新的活動、自動取消封存，所以不會有「在接著用、卻看不見」的對話。**分叉是新的活動**：`ForkAsync` 只複製呼叫者自己的對話（訊息、引用、問的日期與 `CreatedAt` 原樣複製，讀回的先後不變），新的一串不釘選、不封存、`UpdatedAt` 是現在；不是自己的、不存在的、標題空白的一律回 null（導回，不洩漏它存不存在）；搜尋只比對標題（`ILike`，使用者打的 `%` `_` 先跳脫），不比對內容，內容是案情、不建索引。訊息的 `CreatedAt` 決定讀回的先後，資料庫只存到微秒，所以一問一答差一微秒以上，不是一個 tick。
- **歷史有沒有開啟與保留天數，讀 `IConversationHistoryAvailability`**（singleton，只讀設定），不讀 `IConversationHistory`：後者是 scoped（要資料庫與租戶），為了一個旗標去解析它，互動式 circuit 會把那個 `DbContext` 抓住直到斷線（同 `IAnswerAvailability`、`IDirectoryAvailability` 的做法）。**側欄分兩區，不是一條清單**：功能區（`ep-side-actions`：新對話與各功能入口，群組之間只有細線、沒有後台式的群組標題）固定在上；對話歷史區（`ep-recent`）在功能區之外、占剩下的高度，是凹進去的另一塊面板，區頭（標題與篩選）固定、只有清單（`ep-recent-scroll`）自己捲動。側欄本體不捲動，否則歷史一長就把功能入口捲走；窄欄只留圖示與搜尋鈕，不畫歷史區。側欄的對話經 `IAskService.RecentAsync`：只有本人的標題、時間與是否釘選，沒開啟時服務不開 scope、不查資料庫；釘選的自成一組排最上面，其餘依台北時間的日曆日分組（今天、昨天、過去 7 天、更早；`ConversationGroups`）。**每個項目的「⋯」選單（`Shared/ConversationMenu`，側欄與對話紀錄頁共用）是原生 popover 加附 antiforgery 的普通 POST 表單**，送到 `PortalEndpoints` 的 `/c/{id}/pin`、`/rename`、`/fork`、`/archive`、`/delete`（側欄只列沒封存的，封存的在對話紀錄頁的 `?view=archived`；端點直接用 `IConversationHistory`，它們是普通請求、有環境租戶，不受互動式元件的限制）：靜態頁與互動式工作區的側欄是同一個 `PortalShell`，所以不用 `@onclick`，也不引入 MudBlazor 的互動元件。做完導回 Referer（經 `SignInRoutes.SafeReturnUrl`，防 open redirect；分叉成功前往新的那一串）；刪除要先確認，封存可還原、不確認。
- **工作區是客戶入口唯一的互動式頁面**（`Components/Portal/Workspace/`）：`ChatPage` 在 `/portal/{tenant}`（新對話）與 `/portal/{tenant}/c/{id}`（開啟自己的舊對話），版面是 `WorkspaceLayout`；其餘入口頁維持靜態 SSR。沒有 JavaScript 看得到預先渲染的首屏與說明，但不能送出（`<noscript>` 有提示）。四條規矩：(a) **租戶只取自已驗證的身分**（登入 cookie 的 `TenantIdentifier` 與 `PortalUserId` claim），circuit 沒有環境租戶，也不信任何元件參數；`WorkspaceLayout` 因此不用 `IPortalAccount`，租戶名稱經 `ITenantDirectory.FindByIdentifierAsync`；(b) **一串對話的狀態與流程在 `ChatSession`**（scoped，一個 circuit 一份，純 C# 類別，`ChatSessionTests` 用假的 `IAskService` 測），頁面只呈現；不預期的例外在這裡轉成通用的失敗回合（只記例外型別），不會讓 circuit 斷線；(c) **跨元件通知用 `WorkspaceState`**（記錄完一題後側欄重讀最近對話），不用靜態事件；(d) **互動式的入口元件只認 `IAskService` 與 `AuthenticationState`**：`ArchitectureTests` 禁止它們出現帳號、驗證器、登入、歷史、模型、資料庫與 `TenantScopes` 的型別，也要求入口頁只有 `Workspace/` 底下的是互動式。「對話紀錄」清單（`History`）仍是靜態頁：標題搜尋（`?q=`）、連結到 `/c/{id}`，每一列有同一個 `ConversationMenu`。**首頁的輸入是多行的 `textarea[data-composer]`**（Enter 送出、Shift+Enter 換行、組字中不送出、自動長高都由 `forms.js` 處理，沒有它仍是一般表單）；**首頁沒有手動日期**，一律查現行，時點查詢只經 REST 的 `asOf` 與 MCP；首頁只留一行極簡提示，完整說明（輸入方式、紀錄、資料送到模型服務的揭露）收在原生 popover（`#ask-info`），揭露不得從首頁拿掉。

## MCP 入口

欄位、工具與接法的契約在 README「AI 代理的入口」；這裡只放不變式。

- **工具是 REST 資料端點的包裝，不另寫一份**：取得法規、列條文、推論經 `OntologyGateway`，取得法源經 `ConsultGateway`，搜尋經 `ILawSearch`；REST、MCP 與入口的回答共用它們，所以輸入檢查、錯誤與「查詢文字不進日誌」只有一個實作。新增資料端點時，先寫進閘道，再讓 REST 與 MCP 各自呈現。**閘道不計量重送**（`ConsultGateway` 不記 `consult.ground`：REST 的 `Idempotency-Key` 要與回應同一個交易記），由呼叫端記。
- **模組各自貢獻工具，主機組合**：工具類別在擁有該能力的模組（`Modules.Ontology/Mcp`、`Modules.Consult/Mcp`），以 `WithOntologyTools()`、`WithConsultTools()` 加進 `PlatformMcp.AddPlatformMcp()`；模組之間不互相參考，MCP 的設定、`Origin` 檢查與掛載在 `Api/Mcp`。
- **掛在 `/v2` 的資料群組裡**（`MapPlatformMcp`）：API key、方案（沒有方案 402）與每租戶限流繼承自群組，不另做一套；測試把它掛到群組外會失敗（`TheSameDoorAsRestNoKeyIs401AndNoPlanIs402`）。`Origin` 檢查在驗證之前（`UsePlatformMcpOriginCheck`）。
- **無狀態**（`HttpServerSessionMode.Stateless`）：不要引入需要記住 session 的功能（伺服器主動推送、訂閱），那會讓多副本需要連線黏著。
- **工具只讀**，宣告 `ReadOnly`／`Idempotent`／`OpenWorld = false` 與結構化輸出；預期的失敗以 `McpException` 回成工具錯誤（框架會把其他例外換成通用訊息，不洩漏內部細節）。工具的描述是給模型讀的：寫清楚拒答與空結果不是錯誤、證據以外的知識不能用。
- **不實作 OAuth 授權伺服器**：身分就是 API key。託管型用戶端（要求 OAuth 的連接器）接不上是已知的取捨；要支援，是另一個要有身分提供者的決定，不在這裡順手加。

## 稽核紀錄

- `AuditEvents` 是**只增不改**，由資料庫觸發器強制（UPDATE、DELETE、TRUNCATE 會報錯），不靠慣例。執行期以群組 `envcite_platform_app` 的登入身分連線，不是資料庫擁有者（擁有者能停用觸發器）；群組的權限由遷移授予，對該表只有 SELECT、INSERT。遷移以擁有者執行，預設權限才涵蓋之後新建的表。
- 「誰」從已認證身分推得，**絕不取自請求本文**。針對租戶的事件帶 `SubjectTenantId`；此實體刻意不是 Finbuckle 租戶實體（營運者跨租戶讀取），所以每次讀取都要明確過濾。
- 有變更可依附的事件用 `db.StageAudit(...)`，與變更同一交易儲存；沒有的用 `IAuditLog.AppendAsync`。`Detail` 不放機密、雜湊或完整 API key（有測試）。

## 設計系統與營運者後台

- 外觀只有一個來源：`Web/Design/DesignTokens.cs`。給代理看的視覺規格是同目錄的 `DESIGN.md`（企業配色、字級、字型）；改數值時兩份一起改，`DesignTokenTests` 核對它們相同。`platform.css` 只引用變數，**不得寫死**顏色、字重、時長、緩動、縮放或觸控尺寸；文字配色須符合 WCAG AA 對比，**淺色與深色兩套都要過**（深色是同一組變數換一份值，由 `TokenCss` 產生，元件不各寫一份）；視口斷點只寫在 `DesignTokens.Breakpoints`；表格卡片**不得**加 `overflow: hidden`；禁止停用縮放。**外觀預設跟隨瀏覽器／系統（`prefers-color-scheme`），個人設定可手動覆寫**（契約見 [`README.md`](README.md)「個人設定與外觀」）：
  - 沒有偏好時 `<html>` 不帶 `data-theme`、不存任何東西，行為與只靠 `prefers-color-scheme` 完全相同。
  - 偏好存在**伺服器端的 cookie**（`AppearanceOptions.Cookie`，以 `CookieBuilder` 描述，名稱與有效期不寫死在端點或元件裡），由 `App.razor` 在回應的第一格輸出 `data-theme`、鎖定 `<meta name="color-scheme">`、讓 `theme-color` 只留選的那一個；token 與原生控制項都跟著選擇。不存 localStorage、不用內嵌指令碼讀偏好、不進資料庫。
  - **切換只放在登入後的個人設定**：營運者與客戶入口各一頁，共用 `Shared/AppearanceSetting`，不複製。**匿名頁面（登入、啟用、重設、已登出）不提供切換**：還沒有身分，沒有「個人」設定可放，作業系統與瀏覽器的選擇就是唯一來源；但它們一樣依 cookie 輸出已存的偏好，兩邊才一致。
  - 選項是原生 radio（`role="radiogroup"`）。選了立即預覽：`forms.js` 換 `data-theme`、`color-scheme` 與 `theme-color`（用 View Transition 淡入淡出），並用 `fetch` 送出同一張表單；沒有 JavaScript 時是普通的 POST 表單。
  - 舊的 `ThemeSwitch` 與 localStorage 的 `ep-theme` 已移除，`<head>` 的小腳本仍清掉舊值。
  - 以上由 `DesignTokenTests`、`AppearanceTests` 等測試守門。
- 營運者頁面讀 `IAuditQuery`、`ITenantDirectory` 這類在 `SharedKernel` 的契約，不經 HTTP；每次呼叫開自己的 scope，因為 Blazor Server 電路的壽命遠長於一個請求，不能持有過期的 `DbContext`。
- **密碼輸入框一律用 `Shared/PasswordField`**（眼睛鈕、大寫鎖定提醒與 `autocomplete` 都由它管，營運者與客戶入口的登入、啟用、改密碼、關閉兩步驟都是）；不得在頁面或對話框直接寫 `type="password"` 或 `InputType.Password`。`PasswordFieldTests` 掃原始碼守住，並指名檢查啟用頁；啟用頁的實際輸出由 `InvitationTests` 斷言。一次性密碼的顯示框是唯讀文字，不是輸入框，不在此限。
- **要讓人複製一段文字（網址、代碼）一律用 `Shared/CopyButton`**（參數 `Id`、`Label`、`Value`），不另寫複製鈕。它只畫按鈕，文字平常收起來、不佔畫面。複製在瀏覽器端由 `design/forms.js` 的委派處理（`data-copy-target`，靜態與互動渲染都能用，不走 JS 互通），複製的是完整文字；成功時鈕文字變「已複製」並經 `aria-live` 通知螢幕閱讀器；剪貼簿不可用時把文字顯示出來並選取；沒有 JavaScript 時鈕不出現、文字直接列出。按鈕文字就是無障礙名稱，要說明複製什麼（「複製登入網址」）。版面由 flex 換行決定（寬時並排、窄時各撐滿一列），不靠視口斷點；樣式只用設計變數。
- **租戶頁的存取網址取自請求的來源**（規則見 [`README.md`](README.md)「租戶頁的存取網址」）：不新增設定鍵、不用 `Platform:Mail:BaseUrl`；路徑來自 `PortalPaths` 與 `ApiPaths`。視覺隱藏的 `.ep-status` 只用於表格卡片的筆數播報，`.ep-table-card:has(.ep-empty) > .ep-status` 會在空狀態時收掉它，要讓人看見的資訊不得放進空狀態的表格卡片。
- 新發出的 API key 或一次性密碼只在不可由 Esc 或點背景關閉的對話框顯示一次，UI 不再重繪它；不可逆的動作（撤銷、移除方案）要確認。
- 時間以臺北時間（固定 UTC+8）顯示；長 id 縮短時留**尾端**（UUIDv7 的開頭是時間戳）。
- `Api.csproj` 設 `RequiresAspNetWebAssets`，因為 Razor 元件在類別庫裡、主機自己沒有 `.razor`；少了它 SDK 不會提供 `_framework/blazor.web.js`，電路不會連線，而伺服器渲染的 HTML 看不出任何問題。`DesignPageTests` 會抓取頁面引用的每個腳本以守住這點。

## Conventions

- 新增 `/v2` 端點、公開欄位或可選功能面時，同一次提交更新 OpenAPI、測試，以及（行為改變時）本檔的對應不變式。

## Safety

- 連線字串、計費金鑰與本機 appsettings（`appsettings.*.local.json`）的值同樣是機密，不提交。
- 產生物不手改：`src/*/Migrations/`（用 `dotnet ef`；`.editorconfig` 標為 `generated_code`；例外是 EF 無法表達的觸發器與授權：`AuditEventsAppendOnly`、兩個 context 的 `AppRoleGrants`，其 `Sql` 呼叫是手寫的）、倉庫根 `artifacts/obj/` 下的 gRPC 程式碼（來自倉庫根 proto）、`openapi/openapi_v2.json`（來自 `dotnet build`）。

## Done when

- `dotnet build` 與 `dotnet test` 通過，`dotnet format --verify-no-changes` 乾淨——由 `.github/workflows/ci.yml` 強制（Testcontainers 用 runner 上的 Docker）。
