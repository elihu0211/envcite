# AGENTS.md

倉庫層規範見根 [`AGENTS.md`](../../AGENTS.md)；本檔只管 `services/platform/`。

## Project

- **Role:** 多租戶控制平面（租戶、API key、限流、計量、對外 REST API），經 gRPC 呼叫 `services/canon`（共享、不分租戶）與 `services/consult`（接地、租戶隔離）。
- **Auth surfaces:** 租戶 API key（`/v2`）、營運者帳號加強制 TOTP（`/ui`）、客戶入口帳號（`/portal`，設定開關）、目錄同步 token（`/scim/v2`，設定開關）。

## Architecture

### Modular Monolith 與依賴規則

- **編譯期模組**：單一 solution 與部署單位，各模組以 `AddXModule()` 註冊（不做執行期動態載入）。模組僅參考 `SharedKernel` 與 `Data`，彼此不直接參考（唯一例外：`Modules.Consult` 因 `consult.proto` 匯入 `canon.proto` 而參考 `Modules.Canon`）。
- **ArchitectureTests 守門**：
  1. **專案參考邊界**：`SharedKernel` 不參考任何平台專案；`Web` 只參考 `SharedKernel`。
  2. **禁用服務定位器**：除組合根（`Api`、`WebModule.cs`）與 `IServiceProvider` 自身實作外，不注入 `IServiceProvider`；惰性依賴一律注入 `Lazy<T>`。
  3. **禁用 `IHttpContextAccessor`**：元件與 `Web` 透過 `AuthenticationState` 取身分、路由參數取租戶。
  4. **禁用可處置 Transient**：不將實作 `IDisposable`／`IAsyncDisposable` 的自有型別註冊為 transient（測試主機啟用 `ValidateScopes` 與 `ValidateOnBuild`）。
- **入口側欄擴充**：功能模組實作 `IPortalNavContributor`（`Components/Layout/Navigation/`，以 `AddPortalNavContributor<T>()` 註冊為 scoped），宣告群組與 `Order`（服務 10–29、組織 30–59）；文字須經 `L` 以便資源檔掃描，授權由頁面與服務層執行。

### 資料存取、租戶範圍與中介層

- **兩個 DbContext（不再增加）**：`TenancyDbContext`（Finbuckle `EFCoreStoreDbContext`）與 `PlatformDbContext`（所有租戶資料，schema `platform`）。
- **Data Protection 金鑰**：存於 `TenancyDbContext`（早於租戶解析載入；產生 OpenAPI 時不註冊）。預設明文 XML；成對設定 `Platform:DataProtection:CertificatePath` 與 `CertificatePassword`（密碼只經環境變數）時以憑證加密新金鑰，所有副本須用同一張憑證。
- **中介層承重順序**：`UseAuthentication` → `UseMultiTenant` → `UseAuthorization` → `UseRateLimiter`。`MultiTenantDbContext` 不得在租戶解析前解析。
- **跨租戶與無環境租戶操作（`TenantScopes.InAsync`）**：於 `Data` 開設獨立 DI scope 並綁定目標租戶（一個操作一個 `DbContext`）。委派內以 `scope.Get<T>()` 取服務、`scope.CreateDb()` 取 `PlatformDbContext`；禁止手寫 `CreateAsyncScope`、傳出 `TenantScope` 或於其他 async 輔助方法綁定（`AsyncLocal` 返回即遺失）。由 `TenantScopesTests` 守門。
- **Blazor Server 電路**：為有狀態 SignalR 連線，多副本須設定入口 session affinity（部署庫）或外部 backplane。

### 介面與測試策略

- **Web 專案**：`src/EnvCite.Platform.Web` 為 Razor 元件庫（`/ui`、`/portal`），由 `Api` 掛載，不在 `/v2` 限流與計量群組內。
- **整合與瀏覽器測試**：xUnit + `WebApplicationFactory` + Testcontainers PostgreSQL（上游以行程內 h2c gRPC fake 取代）。`WorkspaceE2ETests` 以 Kestrel + Playwright（`tests/e2e/workspace.mjs`）驗證 SignalR circuit 身分與授權導向（無網路設 `ENVCITE_SKIP_E2E=1`，自訂瀏覽器設 `ENVCITE_CHROMIUM_PATH`）。寄信測試連行程內 `TestSmtpServer`。

## Commands

從**本目錄**執行（`EnvCite.Platform.slnx` 所在處）。

```bash
dotnet restore && dotnet tool restore  # 改了 Directory.Packages.props 或 csproj 後加 --force-evaluate 並提交 packages.lock.json
dotnet build                         # 警告即錯誤；同時產生 openapi_v2.json
dotnet test                          # 需要 Docker
dotnet format --verify-no-changes    # CI 檢查；去掉旗標即套用
dotnet run --project src/EnvCite.Platform.Api   # Development：連本機 PostgreSQL 自動遷移；worktree 用 `wt platform`

# 遷移（context 所在專案即為啟動專案）
dotnet ef migrations add <Name> -p src/EnvCite.Platform.Data -s src/EnvCite.Platform.Data
dotnet ef migrations add <Name> -p src/EnvCite.Platform.Modules.Tenancy -s src/EnvCite.Platform.Modules.Tenancy

docker build -f Dockerfile ../..   # 映像（context = 倉庫根）
```

共用建置設定在 `Directory.Build.props`。正式環境由映像內 EF Core migration bundle 依序執行遷移（`tenancy` 先、`platform` 後）。

## Contract

### REST / OpenAPI 與失敗代碼

- **OpenAPI 契約**：`openapi/openapi_v2.json` 於 `dotnet build` 自動產生並隨變更提交。新 `/v2` 端點須標註 `.WithName`／`.WithTags`／`.WithSummary`／`.WithDescription` 與所有狀態的 `.Produces…`。不相容變更進 `/v3`；若直接破壞 `/v2`，PR 須加 `api-breaking` 標籤並於說明寫明理由。
- **對外用語獨立於內部 gRPC**：端點負責將法律詞彙（如 `pointInTime`）對映至 gRPC 欄位（如 `as_of`）。`.proto` 一律自倉庫根 `proto/` 編譯，不得複製。
- **穩定失敗代碼（`Outcome.Code`）**：常數定義於 `SharedKernel/OutcomeCodes.cs`。`Message` 與 API `detail` 固定英文（`OutcomeCodes.English`）；介面依 `Labels` 資源檔 `Outcome.{代碼}` 本地化（`OutcomeText.Localized()`）。新增代碼須同步更新英文、中文與英文資源檔（測試守門）；Identity 錯誤須經 `IdentityOutcomes` 轉換。

### 內部 gRPC 與錯誤對映

- **統一註冊**：經 `SharedKernel.PlatformGrpcClient.AddPlatformGrpcClient` 註冊，自動附加租戶（取自 Finbuckle context，不讀 `HttpContext`）與請求 metadata。
- **逾時與重試**：預設逾時於 `AddInternalGrpcClient` 統一設定（僅推論 `ReasoningTimeout` 等特殊需求自帶 deadline）。僅重試 `UNAVAILABLE`；`DEADLINE_EXCEEDED` 與 `RESOURCE_EXHAUSTED` 不重試。連線選項須與 `envcite_grpc.connection` 對齊。
- **錯誤對映**：gRPC `InvalidArgument` → 400，`NotFound` → 404，`Unavailable`／`DeadlineExceeded`／`ResourceExhausted` → 503。
- **未捕捉例外**：非 Development 環境由 `UseExceptionHandler()` 回 500 `problem+json`（`code = "internal.error"`，附 `traceId`，不洩漏訊息與堆疊）；僅接受 HTML 則回空本文 500。

### 計量與冪等性

- **計量原則**：只記成功呼叫（含 `consult` 拒答），不猜測用量。
- **`Idempotency-Key`（`POST /v2/ground`）**：鍵為 `(租戶, 操作, key)`，請求本文只存雜湊，僅存成功回應（同 key 不同內容回 `422`）。回應與用量於同一次交易儲存（`IIdempotencyStore.CompleteAsync`），並行重複請求僅一筆成功、另一筆用量回滾。

## Configuration

### 選項繫結與驗證

- **單一讀法**：設定群組實作 `ISectionOptions` 並以 `AddSectionOptions<T>()` 註冊（含 `ValidateOnStart` 與 `IValidateOptions<T>`），使用端注入 `IOptions<T>`。除 `Telemetry.cs` 的 `OTEL_EXPORTER_OTLP_ENDPOINT`、`AddPlatformDataProtection` 憑證載入與 `mail preview` 前置綁定外，不直接碰 `IConfiguration`。
- **鍵名契約與機密**：鍵名為與部署庫之契約（`ENVCITE_Section__Key`）；機密只經環境變數注入，Api 專案不設 `UserSecretsId`。
- **預設關閉原則**：可選功能面（營運者後台、客戶入口、有依據的回答、寄信、MCP）預設關閉（關閉時回 `404` 且不簽發 cookie），僅於 Development 開啟。

### 系統名稱、預設方案與日誌

- **系統名稱**：唯一來源為 `Platform:Brand:Name`（`BrandOptions`），經 `BrandText.Name` 與 `IOptions<BrandOptions>` 讀取，帶名稱文字使用格式鍵（`L["登入 · {0}", Brand.Name]`），由 `BrandTests` 守門；標誌 SVG 不讀設定。
- **預設方案與種子資料**：部署環境不設 `DefaultPlan`（無方案租戶於資料端點回 `402`）；`SeedDevelopmentData` 僅於 Development 且表空時執行，一次性密碼只印至 `Console.WriteLine`。
- **命令列輸出隔離**：`appsettings.json` 將 `Microsoft.EntityFrameworkCore.Database.Command` 設為 `Warning`（`appsettings.Development.json` 不覆寫），避免 SQL 日誌蓋掉 `OperatorCli` 標準輸出（由 `ConfigurationTests`、`OperatorCliTests` 守門）。
- **Proxy 轉發**：TLS 終止 proxy 後須設 `ASPNETCORE_FORWARDEDHEADERS_ENABLED=true`，確保依來源位址限流正常。

## Observability

- **追蹤與租戶標籤**：已認證租戶僅標於追蹤 `tenant.id`，不進指標。
- **RED 指標與探針**：HTTP 用標準伺服器指標（探針設 `DisableHttpMetrics()` 排除）；gRPC 用戶端由 `GrpcClientMetrics` 量測。`/health/live` 不檢查依賴；`/health/ready` 檢查 `TenancyDbContext`（故障回 `503`），探針不追蹤。
- **模型呼叫遙測**：`IChatClient` 一律經 `AnswerTelemetry.Wrap` 包裝並**明確關閉 `EnableSensitiveData`**（由 `AnswerTelemetryTests` 守門，僅留模型、token 數、延遲與結束原因）。
- **作答結果計數**：`envcite.platform.answer.outcomes`（`AnswerMetrics`）於每次作答得出結果時遞增，標籤僅限 `outcome` 與 `bare_topic`（由 `AskServiceTests` 守門）。

## Auth & tenancy

### 通用認證、過濾與密碼規則

- **租戶 API 認證**：`Authorization: Bearer <key>` → 前綴查詢與 SHA-256 定時間比對 → Finbuckle 解析租戶。`cookie` 永不認證 `/v2`；`/ui` 與 `/portal` 回應皆設 `Cache-Control: no-store`。
- **查詢過濾**：租戶實體查詢一律受 `TenantId` 過濾；僅審查過的跨租戶查詢（憑證認證、期限清理）可按名稱繞過（`IgnoreQueryFilters([TenantToken])`）。
- **防探測與保密**：登入失敗不透露帳號存在、鎖定或停用狀態（不存在仍付雜湊成本），不回填密碼／驗證碼；一次性密碼只顯示一次並存雜湊，不進日誌與稽核。
- **密碼規則（`PasswordPolicy`）**：依 NIST SP 800-63B（夠長、不含 email、zxcvbn 強度），兩端共用 `MembersModule.AddPasswordPolicy<TUser>()` 與 `PasswordPolicyValidator`；密碼與 email 不進日誌或錯誤描述。

### 共用 UI 基底、模板與流程（`ComponentTemplateTests` 守門）

全面禁止於 `.razor` 使用 `@inject`：單服務薄頁繼承 `StaticPageBase<TService>`／`PortalPageBase<TService>`，其餘具依賴元件以 `.razor.cs` 主建構子注入。

| 類別 | 元件／項目 | 職責 |
|------|------------|------|
| 基底（`Components/Bases/`） | `PlatformComponentBase`、`NavigableComponentBase`、`PlatformLayoutBase`、`PortalLayoutBase` | 集中 `L`、`Brand`、`Navigation` 與入口雙版面導覽 |
| 頁面／對話框基底 | `PageBase`、`StaticPageBase<TService>`、`FormPageBase<TForm>`、`PortalPageBase<TService>`、`AdminPageBase`、`FormDialogBase`、`SecretDialogBase` | 狀態管理、`LoadingAsync`、`ConfirmAsync`、只顯一次機密鎖定 |
| 版面模板（`Components/Shared/`） | `DocumentTitle`、`PageHeading`、`FormAlert`、`PostForm`、`FormField`、`SubmitButton`、`DialogError`、`DialogSubmitActions`、`SectionCard`、`StatGrid`、`DataTable<TItem>`、`LoadedView`、`SelectField`、`OptionSelect`、`CheckboxField`、`SecretReveal`、`SourceCards`、`TaskMarker`、`PageTabs`／`TabPanel`、`AskComposer` | 封裝 CSS 類別與無障礙標記，頁面禁止手寫原始 `ep-*` 類別或呼叫 `ShowMessageBoxAsync` |
| 共用認證流程（`Components/Shared/`） | `LoginFlow`、`TwoFactorSignInFlow`、`TwoFactorSetupFlow`、`ChangePasswordFlow`、`ActivationFlow` | 僅依賴 `IAccountLifecycle`、`ITwoFactor` 與 `SignInRoutes`（含 `SafeReturnUrl`）；表單模型共用 `Bases/Forms.cs` |
| 密碼欄位 | `Shared/NewPasswordField`、`Shared/PasswordField` | 自動綁定 `PasswordPolicy` 與漸進增強（`forms.js`）；禁止頁面手寫 `type="password"`（`PasswordFieldTests` 守門） |

### 營運者帳號（`/ui`）

1. **獨立儲存**：營運者不屬任何租戶，Identity 主鍵為 `Guid`（避免與租戶成員 `string` 主鍵混階）；不開放自助註冊。
2. **強制安全順序**：先設本人密碼再綁定 TOTP（無角色豁免，啟用不簽發 session）。
3. **每請求重查 DB 與絕對期限**：授權每個請求重查 DB 狀態與 security stamp（circuit 由 `PlatformAuthenticationStateProvider` 每分鐘重驗）；session 受 `Platform:Ui:SessionMaxAge` 絕對期限限制（起點記於 `OperatorSessionLimit.StartedAtKey`）。登入稽核採 fail closed（先寫稽核再簽發 cookie）。

### 客戶入口（`/portal/{tenant}/…`）

1. **渲染與租戶驗證**：除工作區（Interactive Server）外皆為靜態 SSR。HTTP 請求與 cookie 租戶須完全一致；circuit 切換頁面須由 `Routes.razor` 傳入 `Resource="routeData"` 供 `PortalMemberHandler` 取路由值 `Tenant` 驗證。
2. **Circuit 雙身分隔離（`/_blazor`）**：`UsePlatformWebAuthentication` 與 `UsePlatformPortalAuthentication` 於 `/_blazor` 併入 principal，circuit 啟動時由 `PlatformAuthenticationStateProvider` 依第一個網址（`/portal` 留成員、其餘留營運者）裁剪為單一身分（`CircuitIdentities.Combine`／`Narrow`），由 `CircuitConnectionTests` 守門。
3. **中介層與 `AsyncLocal` 規則**：`UsePlatformPortalAuthentication` 須在 `UseMultiTenant` 之後；`MemberAdmin` 須於呼叫端方法內綁定租戶 scope。
4. **Google 登入（`Platform:Portal:Google`）**：
   - 使用框架 Google handler 與 `AspNetUserLogins`（`PlatformDbContext` 以 `AdjustKey` 將 `TenantId` 併入主鍵）。
   - 全站單一回呼 `/signin-google`（位於 `/portal` 外），靠受保護 `PortalGoogle.TenantItem` 傳遞租戶並於第二段比對；**不自動建帳**，僅對應已啟用既有成員；本地兩步驟與鎖定照舊，不信任 Google MFA。
5. **入口 MFA（TOTP，RFC 6238）**：
   - 預設關閉（`Platform:Portal:RequireMfaForAdmins` 可對管理員強制），全用 Identity 原生流程。
   - 密碼與驗證碼共用失敗計數器；金鑰以 `GenerateNewAuthenticatorKey` + `SetAuthenticationTokenAsync` 產生（不用 `ResetAuthenticatorKeyAsync` 以免斷開渲染中 session）；QR 碼於伺服器端繪製，機密與 URI 不進稽核／日誌。

### 邀請與信箱驗證

1. **權杖與流程**：登入不要求信箱已驗證（未驗證信箱不得作復原管道）。權杖使用 Identity 原生電子郵件確認權杖（不自製不儲存）；`GET` 僅檢視、`POST` 才消耗並以 `AddPasswordAsync` 旋轉 security stamp，啟用不簽發 session；重寄時無密碼者先換 stamp。連結與權杖不進日誌或輸出。
2. **設定與傳輸分層（`AddMailModule()`）**：
   - 契約與實作分界：`SharedKernel` 只放呼叫端用到的 `IMailSender`、`IMailComposer`、`MailOptions` 與其資料型別（核心不依賴 MailKit 與 Razor）；傳輸、版型、文案與傳輸／寄件者選項在 `Modules.Mail`，只由 `Api` 註冊，其他模組不註冊也不參考它。
   - 選項拆為 `MailOptions`、`MailTransportOptions`、`MailIdentityOptions` 與 `MailTemplateOptions`（僅啟用時驗證）。
   - `SmtpMailSender` 每次自 `ISmtpClientFactory` 取新用戶端；`Username`／`Password`（僅傳輸層讀取，不進任何日誌／輸出）皆未設時不送 AUTH；協商交給 MailKit，不自設 `ServerCertificateValidationCallback`。
   - `mail test` 依 `MailTestReport` 錯誤碼對照表輸出（不含帳密或例外原文）。
3. **組信與渲染安全**：
   - 分三層：`ActivationMailModel`（資料）→ `IMailComposer`（組 `ComposedMail`）→ `IMailSender`（`multipart/alternative` 寄出）。文案置於 `MailTexts` 資源檔，語系取自 `MailTemplateOptions.Culture`。
   - 版型色票（WCAG AA ≥ 4.5:1）、寬度、字型由 `MailTemplateOptionsValidator` 於啟動驗證；HTML 由 Razor + `HtmlRenderer` 渲染（放行全 Unicode，表格 + 內嵌樣式 + VML 後備），**信內唯一外部網址為啟用連結**（禁追蹤像素與外部資源）。
   - `mail preview` 於主機建立前執行真實 `IMailComposer`（不連 DB、占位網址用 `.example`）。

### 成員管理與目錄同步（SCIM）

- **成員管理（`MemberCore`）**：擁有者管所有人，管理員僅管較低角色；禁止對自己操作；角色讀 DB 不讀 cookie；停用立即失效所有 session。
- **目錄同步（`Modules.Members/DirectorySync/`）**：
  1. **認證與租戶**：SCIM token（`/scim/v2`）為獨立 scheme（`DirectoryToken`），與 API key 互不通用；租戶只來自 token，稽核 `ActorId` 取自 `directory_token_id`。
  2. **交易與權限邊界**：一個請求一個交易（fail closed）；角色上限為管理員（不動擁有者，不刪最後一位擁有者）；受管成員鎖定角色與停用（`directory.member_managed`）；群組角色重算一律於交易內走 `DirectoryGroupRoles.RecomputeAsync`。
  3. **限流與端點**：`DirectoryRateLimiter` 於授權後依 token 限流；PATCH 寬鬆略過未知屬性、`userName` 不可改；新簽發 token 僅於回應顯示一次（`SecretReveal`），`Shared/DirectoryStatusCard` 共用唯讀介面，存取控制仍在服務層。

## Plans & access

- **指派與查找**：方案定義於 `Platform:Plans`，指派存於 `TenantEntitlements`（順序：DB 指派 → `TenantPlans` → `DefaultPlan`）；指派到已移除方案視為無方案（fail closed）。
- **全付費與分流**：資料端點無方案回 `402`（早於限流，不耗額度）；管理端點對已認證租戶開放並設濫用防護底限。
- **查詢與推論邊界**：`asOf` 查無回 `404`；含案情查詢改用 **POST**，**查詢文字不得進日誌或指標**；REST 與入口共用 `ILawSearch`；推論僅回候選且不暴露內部 proto。

## 有依據的回答與對話紀錄

### 回答管線（`GroundedAnswerer`）

固定四步線性流程：以直接循序 `await` 執行 `rewrite`（有前文時改寫追問為獨立問句）→ `ground`（向 `consult` 取封閉證據集）→ `write`（單次 `IChatClient.GetResponseAsync<ModelAnswerOutput>`，啟用 JSON Schema 與溫度 0）→ `verify`（強制檢查所有領域積木結構與每個有效引用代號，否則丟棄作廢）。

- **步驟規矩**：預期失敗轉結果代碼、取消與未預期錯誤照常拋出；`ground` 若已有結論（`AnswerTurn.Done`）即跳過 `write` 與 `verify`；不串流輸出（須整份結構與引用核驗後呈現），進度經 `IProgress<AnswerProgress>` 回報（不含問題與證據）。
- **強型別積木與呈現**：模型輸出多型領域積木並將代號置於結構化引用欄位，由 `FormattedAnswerView` 依型別映射渲染，`AnswerDocumentFormatter` 確定性轉出記憶文字、複製純文字與安全圖表 DSL。
- **主題詞偵測（`BareTopic`）**：與搜尋端共用 `LawSearchOptions.Shape` 判準（非問句、無句末標點、無疑問詞），命中時取證據仍用原詞，送模型改用寬鬆任務句避免誤拒答。
- **拒答原因區分**：`consult` 無命中回 `no_authority`；`consult` 有法源但模型回證據不足為 `insufficient_evidence`（仍帶 `Sources` 供介面列出）；結構不合或引用核驗作廢不帶法源。

### 對話記憶與追問改寫

- **無狀態記憶**：前文以 `IReadOnlyList<ChatMessage>` 傳入（源自 `ChatSession` 記憶體或歷史還原，由 `AnswerDocumentFormatter.ToMemoryText` 轉成帶法規名稱的文字，不用 `AgentSession`／`ChatHistoryProvider`）。
- **安全與截斷**：前文非證據；送入前剝除殘留 `[L1]` 代號（`PriorOfAsync`）；長度交由 `MessageCountingChatReducer`（`MEAI001` 僅於 `ConsultModule` 註冊處放行）與 `HistoryChars` 截斷（不用 `SummarizingChatReducer`）。
- **追問改寫原則**：`rewrite` 僅供取證據（作答仍用原句），且**不將前輪回答的條號、金額、日期帶入問句**；無前文直接放行，改寫失敗退回原句。

### 對話紀錄與工作區

- **開關與授權**：須同時開啟 `Platform:Answer:Enabled` 與 `TenantEntitlement.AiAnswerEnabled`（跟隨 DB 方案列）；頁面一律透過 `IAskService`（singleton）經 `TenantScopes.InByIdentifierAsync` 執行（非入口成員一律 fail closed）。
- **隱私與生命週期**：對話紀錄只存問題、日期、強型別 `Document`（`jsonb` 與 `SchemaVersion`）或拒答／條文摘要 `Content`、結果種類與引用（不存條文本文），**僅作者本人可見**（非本人回開新對話或 null）。釘選／重命名／封存不動 `UpdatedAt`；封存清掉釘選，於封存對話續問自動取消封存；`ForkAsync` 複製訊息時保留先後順序（微秒級 `CreatedAt`）；標題搜尋（`ILike`）須跳脫 `%` 與 `_`。
- **側欄與工作區（`Components/Portal/Workspace/`）**：
  - 保留天數讀 singleton `IConversationHistorySettings`。
  - 側欄（`PortalShell`）採上下兩段平面清單（功能區 `ep-side-actions` + 最近對話 `ep-recent-scroll`），項目「⋯」選單（`Shared/ConversationMenu`）以原生 popover + POST 表單送至 `PortalEndpoints`。
  - `ChatPage` 為入口唯一互動式頁面（`WorkspaceLayout`），狀態機封裝於 `ChatSession`、跨元件通知用 `WorkspaceState`；首頁查詢固定現行並保留 `#ask-info` 揭露 popover（由 `ArchitectureTests` 守門）。

## MCP 入口

- **包裝與組合**：工具包裝既有閘道（`CanonGateway`、`ConsultGateway`、`ILawSearch`），由 `WithCanonTools()`／`WithConsultTools()` 註冊至 `PlatformMcp.AddPlatformMcp()`；閘道不計重送。
- **路由與安全**：掛載於 `/v2` 資料群組（繼承 API key、402 與租戶限流；`Origin` 檢查在驗證前）；採 `HttpServerSessionMode.Stateless`，工具只讀並以 `McpException` 回報預期失敗；**不實作 OAuth 授權伺服器**（僅 API key）。

## 稽核紀錄

- **`AuditEvents` 只增不改**：由 DB 觸發器強制；執行期以非擁有者角色 `envcite_platform_app`（僅 SELECT／INSERT）連線，遷移以擁有者執行。
- **欄位規則**：「誰」取自已認證身分，對租戶事件帶 `SubjectTenantId`（非 Finbuckle 實體，讀取須手動過濾）。依附變更用 `db.StageAudit(...)`，獨立事件用 `IAuditLog.AppendAsync`；`Detail` 不含機密、雜湊或完整 key。

## 設計系統與營運者後台

- **單一外觀來源**：`Web/Design/DesignTokens.cs` 與同目錄 `DESIGN.md` 同步（`DesignTokenTests` 守門）；`platform.css` 只用變數，深淺兩套配色須過 WCAG AA；視口斷點置於 `DesignTokens.Breakpoints`；表格卡片不設 `overflow: hidden`，禁止停用縮放。
- **主題切換**：預設跟隨系統，登入後個人設定（`Shared/AppearanceSetting`）將偏好寫入伺服器端 cookie（`AppearanceOptions.Cookie`），由 `App.razor` 輸出 `data-theme`、`color-scheme` 與 `theme-color`（不存 localStorage）；`forms.js` 負責即時預覽與無 JS 表單降級。
- **共用複製與密碼元件**：複製行為一律用 `Shared/CopyButton`（搭配 `forms.js`）；密碼輸入一律用 `Shared/PasswordField`（`PasswordFieldTests` 守門）。
- **其他顯示與資源規則**：
  - 租戶頁存取網址取自請求來源（`PortalPaths`、`ApiPaths`）。
  - 營運者頁面每次呼叫開獨立 scope 讀 `SharedKernel` 契約。
  - 新發出的 key／一次性密碼僅於不可隨意關閉的對話框顯示一次；時間顯示臺北時間（UTC+8），長 id 縮短留尾端（UUIDv7）。
  - `Api.csproj` 須設 `RequiresAspNetWebAssets` 以提供 `_framework/blazor.web.js`（`DesignPageTests` 守門）。

## Conventions

- 新增 `/v2` 端點、公開欄位或可選功能面時，同一次提交更新 OpenAPI、測試與本檔對應不變式。

## Safety

- 連線字串、計費金鑰與本機 `appsettings.*.local.json` 為機密，不提交。
- **禁改產生物**：`src/*/Migrations/`（例外：`AuditEventsAppendOnly` 與兩組 `AppRoleGrants` 手寫 `Sql`）、倉庫根 `artifacts/obj/` gRPC 碼、`openapi/openapi_v2.json`。

## Done when

- `dotnet build`、`dotnet test` 通過，`dotnet format --verify-no-changes` 乾淨（由 `.github/workflows/ci.yml` 強制）。
