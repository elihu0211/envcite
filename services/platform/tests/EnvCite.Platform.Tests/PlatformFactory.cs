using System.Net.Http.Headers;

using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Time.Testing;

using Npgsql;

using Testcontainers.PostgreSql;

namespace EnvCite.Platform.Tests;

/// <summary>Boots the API against a throwaway PostgreSQL container with a controllable clock.</summary>
public sealed class PlatformFactory : WebApplicationFactory<Program>, IAsyncLifetime
{
    public const int RateLimitPermits = 5;

    /// <summary>測試預設的營運者 email：<see cref="OperatorSession.SignedInClient"/> 在第一次用到時建立這個帳號。</summary>
    public const string OperatorEmail = "operator@example.com";

    /// <summary>Tenant identifier assigned the <c>pro</c> plan, which allows twice <see cref="RateLimitPermits"/>.</summary>
    public const string ProTenant = "paying";

    private readonly PostgreSqlContainer _postgres = new PostgreSqlBuilder("postgres:18").Build();

    public FakeTimeProvider Time { get; } = new(DateTimeOffset.UtcNow);

    public FakeOntologyServer Ontology { get; } = new();

    public FakeConsultServer Consult { get; } = new();

    /// <summary>取代真的 SMTP：測試從不連外，寄出的信都收在這裡。</summary>
    public CapturingMailSender Mail { get; } = new();

    public async ValueTask InitializeAsync()
    {
        await Task.WhenAll(_postgres.StartAsync(), Ontology.StartAsync(), Consult.StartAsync());
    }

    public override async ValueTask DisposeAsync()
    {
        await base.DisposeAsync();
        await Ontology.DisposeAsync();
        await Consult.DisposeAsync();
        await _postgres.DisposeAsync();
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("ConnectionStrings:Platform", _postgres.GetConnectionString());
        builder.UseSetting("Platform:ApplyMigrationsOnStartup", "true");
        // 信裡連結的來源（寫死在設定，不取自請求）；寄信本身由 Mail 假件取代。
        builder.UseSetting("Platform:Mail:BaseUrl", "https://platform.test");
        // appsettings.Development.json 開著本機的種子資料；共用資料庫不要多出一個 dev 租戶。種子由 DevelopmentSeedTests 以全新的資料庫測。
        builder.UseSetting("Platform:SeedDevelopmentData", "false");
        // 目錄同步的功能面預設關閉；共用主機開著，關閉時的行為由 DirectorySyncTests 以自己的主機測。
        builder.UseSetting("Platform:Directory:Enabled", "true");
        // 問答歷史預設關閉；共用測試主機維持關閉（appsettings.Development.json 只為本機開啟），開啟的行為由 ConversationHistoryTests 以自己的主機測。
        builder.UseSetting("Platform:ConversationHistory:Enabled", "false");
        builder.UseSetting("Platform:Ui:Enabled", "true");
        builder.UseSetting("Platform:Ui:SignInAttemptsPerMinute", "1000");
        // 所有測試共用同一個來源位址：預設的每分鐘 10 次會被別的測試用完。限流本身由 PortalRateLimitTests 以自己的主機（小額度）測。
        builder.UseSetting("Platform:Portal:SignInAttemptsPerMinute", "1000");
        // 多數入口測試的成員是 Owner，而且不是在測兩步驟驗證：共用主機不強制（正式預設是強制）。
        // 強制本身由 PortalMfaRequiredTests 以自己的主機（設成 true）測。
        builder.UseSetting("Platform:Portal:RequireMfaForAdmins", "false");
        builder.UseSetting("Ontology:Address", Ontology.Address.ToString());
        builder.UseSetting("Consult:Address", Consult.Address.ToString());
        builder.UseSetting("Platform:Plans:standard:PermitLimit", RateLimitPermits.ToString(System.Globalization.CultureInfo.InvariantCulture));
        builder.UseSetting("Platform:Plans:pro:PermitLimit", (RateLimitPermits * 2).ToString(System.Globalization.CultureInfo.InvariantCulture));
        builder.UseSetting($"Platform:TenantPlans:{ProTenant}", "pro");
        // 測試動態建立大量租戶，無法逐一指派方案，所以給預設方案。正式環境不設（完全付費制），見 PaidOnlyTests。
        builder.UseSetting("Platform:DefaultPlan", "standard");
        builder.ConfigureServices(services =>
        {
            services.Replace(ServiceDescriptor.Singleton<TimeProvider>(Time));
            services.Replace(ServiceDescriptor.Singleton<IMailSender>(Mail));
            // Identity 預設 PBKDF2 十萬次，每次登入、建帳號都要算；測試驗的是流程不是雜湊強度，降低迭代數
            // 以省下大半 CPU（CI 的 runner 只有 1 核）。雜湊字串自帶迭代數，正式設定不受影響。
            services.Configure<PasswordHasherOptions>(o => o.IterationCount = 1_000);
        });
    }

    /// <summary>開啟寄信（邀請流程）執行 <paramref name="body"/>，結束一律還原：共用的主機不能把狀態留給別的測試。</summary>
    public async Task WithMail(Func<Task> body, MailDelivery result = MailDelivery.Sent)
    {
        (Mail.Enabled, Mail.Result) = (true, result);
        try
        {
            await body();
        }
        finally
        {
            (Mail.Enabled, Mail.Result) = (false, MailDelivery.Sent);
        }
    }

    /// <summary>同一個容器裡的全新資料庫，回傳它的連線字串。要從空的開始時用（共用的庫已經有別的測試的資料）。</summary>
    public async Task<string> CreateDatabaseAsync()
    {
        var name = $"fresh_{Guid.NewGuid():N}";
        var created = await _postgres.ExecScriptAsync($"CREATE DATABASE {name};");
        Assert.Equal(0, created.ExitCode);
        return new NpgsqlConnectionStringBuilder(_postgres.GetConnectionString()) { Database = name }.ConnectionString;
    }

    public HttpClient CreateTenantClient(string apiKey)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        return client;
    }

    /// <summary>Creates a tenant with exactly this identifier, the way the operator console does (for tests that assign a plan by identifier).</summary>
    public async Task<TenantResponse> CreateTenantAsync(string identifier, string name)
    {
        await using var scope = Services.CreateAsyncScope();
        var created = await scope.ServiceProvider.GetRequiredService<ITenantProvisioner>()
            .CreateAsync(OperatorSession.Principal(), identifier, name, CancellationToken.None);
        Assert.True(created.IsOk, created.Message);
        return new TenantResponse(created.Value!.Id, created.Value.Identifier, created.Value.Name);
    }

    /// <summary>Creates a tenant with a unique identifier and issues one API key for it.</summary>
    public async Task<(TenantResponse Tenant, CreatedApiKey Key)> CreateTenantWithKeyAsync(
        string prefix, DateTimeOffset? expiresAt = null)
    {
        var tenant = await CreateTenantAsync($"{prefix}-{Guid.NewGuid():N}"[..30], prefix);
        var key = await IssueKeyAsync(tenant.Id, expiresAt);
        return (tenant, key);
    }

    public async Task<CreatedApiKey> IssueKeyAsync(string tenantId, DateTimeOffset? expiresAt = null)
    {
        var issued = await Services.GetRequiredService<IApiKeyAdmin>()
            .IssueAsync(OperatorSession.Principal(), tenantId, "test", ["ontology:read"], expiresAt, CancellationToken.None);
        Assert.True(issued.IsOk, issued.Message);
        var info = issued.Value!.ApiKey;
        return new CreatedApiKey(
            issued.Value.Key,
            new ApiKeySummary(info.Id, info.Name, info.Prefix, info.Scopes, info.CreatedAt, info.ExpiresAt, info.RevokedAt));
    }
}

/// <summary>
/// 需要 <see cref="PlatformFactory"/> 的測試類別分成幾組：每組有自己的主機、PostgreSQL 容器與時鐘，組與組並行；
/// 同一組內的類別依序執行、共用資料。各組耗時要接近才能縮短總時間
/// （<c>dotnet test --report-xunit-junit</c> 的報告可加總各類別耗時）。
/// 例外：<c>RedMetricsTests</c> 的指標讀取器會收到同一個行程裡所有主機的 <c>Ground</c> 呼叫，
/// 所以會呼叫 <c>Ground</c> 的類別（<c>/v2/ground</c> 與入口頁的問答）都放在 <see cref="Group1"/>，不與它並行。
/// </summary>
public static class PlatformTestGroup
{
    public const string Group1 = "Platform.1";
    public const string Group2 = "Platform.2";
    public const string Group3 = "Platform.3";
    public const string Group4 = "Platform.4";
}

[CollectionDefinition(PlatformTestGroup.Group1)]
public sealed class PlatformTestGroup1 : ICollectionFixture<PlatformFactory>
{
}

[CollectionDefinition(PlatformTestGroup.Group2)]
public sealed class PlatformTestGroup2 : ICollectionFixture<PlatformFactory>
{
}

[CollectionDefinition(PlatformTestGroup.Group3)]
public sealed class PlatformTestGroup3 : ICollectionFixture<PlatformFactory>
{
}

[CollectionDefinition(PlatformTestGroup.Group4)]
public sealed class PlatformTestGroup4 : ICollectionFixture<PlatformFactory>
{
}