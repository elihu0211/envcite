using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;

using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Time.Testing;

using Npgsql;

using Testcontainers.PostgreSql;

namespace EnvCite.Platform.Tests;

/// <summary>Boots the API against a throwaway PostgreSQL container with a controllable clock.</summary>
public sealed class PlatformFactory : WebApplicationFactory<Program>, IAsyncLifetime
{
    public const string AdminKey = "test-admin-key";
    public const int RateLimitPermits = 5;

    /// <summary>測試預設的營運者 email：<see cref="OperatorSession.SignedInClient"/> 在第一次用到時建立這個帳號。</summary>
    public const string OperatorEmail = "operator@example.com";

    /// <summary>Tenant identifier assigned the <c>pro</c> plan, which allows twice <see cref="RateLimitPermits"/>.</summary>
    public const string ProTenant = "paying";

    private readonly PostgreSqlContainer _postgres = new PostgreSqlBuilder("postgres:18").Build();

    public FakeTimeProvider Time { get; } = new(DateTimeOffset.UtcNow);

    public FakeOntologyServer Ontology { get; } = new();

    public FakeConsultServer Consult { get; } = new();

    public async Task InitializeAsync()
    {
        await Task.WhenAll(_postgres.StartAsync(), Ontology.StartAsync(), Consult.StartAsync());
    }

    async Task IAsyncLifetime.DisposeAsync()
    {
        await DisposeAsync();
        await Ontology.DisposeAsync();
        await Consult.DisposeAsync();
        await _postgres.DisposeAsync();
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("ConnectionStrings:Platform", _postgres.GetConnectionString());
        builder.UseSetting("Platform:ApplyMigrationsOnStartup", "true");
        // appsettings.Development.json 開著本機的種子資料；共用資料庫不要多出一個 dev 租戶。種子由 DevelopmentSeedTests 以全新的資料庫測。
        builder.UseSetting("Platform:SeedDevelopmentData", "false");
        builder.UseSetting("Platform:AdminKeySha256",
            Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(AdminKey))));
        builder.UseSetting("Platform:Ui:Enabled", "true");
        builder.UseSetting("Platform:Ui:SignInAttemptsPerMinute", "1000");
        // 所有測試共用同一個來源位址：預設的每分鐘 10 次會被別的測試用完。限流本身由 PortalRateLimitTests 以自己的主機（小額度）測。
        builder.UseSetting("Platform:Portal:SignInAttemptsPerMinute", "1000");
        // 多數入口測試的成員是 Owner，而且不是在測兩步驟驗證：共用主機不強制（正式預設是強制）。
        // 強制本身由 PortalMfaRequiredTests 以自己的主機（設成 true）測。
        builder.UseSetting("Platform:Portal:RequireMfaForAdmins", "false");
        builder.UseSetting("Ontology:Address", Ontology.Address.ToString());
        builder.UseSetting("Ontology:InternalToken", FakeOntologyServer.InternalToken);
        builder.UseSetting("Consult:Address", Consult.Address.ToString());
        builder.UseSetting("Consult:InternalToken", FakeConsultServer.InternalToken);
        builder.UseSetting("Platform:Plans:standard:PermitLimit", RateLimitPermits.ToString(System.Globalization.CultureInfo.InvariantCulture));
        builder.UseSetting("Platform:Plans:pro:PermitLimit", (RateLimitPermits * 2).ToString(System.Globalization.CultureInfo.InvariantCulture));
        builder.UseSetting($"Platform:TenantPlans:{ProTenant}", "pro");
        // 測試動態建立大量租戶，無法逐一指派方案，所以給預設方案。正式環境不設（完全付費制），見 PaidOnlyTests。
        builder.UseSetting("Platform:DefaultPlan", "standard");
        builder.ConfigureServices(services =>
        {
            services.Replace(ServiceDescriptor.Singleton<TimeProvider>(Time));
            // Identity 預設 PBKDF2 十萬次，每次登入、建帳號都要算；測試驗的是流程不是雜湊強度，降低迭代數
            // 以省下大半 CPU（CI 的 runner 只有 1 核）。雜湊字串自帶迭代數，正式設定不受影響。
            services.Configure<PasswordHasherOptions>(o => o.IterationCount = 1_000);
        });
    }

    /// <summary>同一個容器裡的全新資料庫，回傳它的連線字串。要從空的開始時用（共用的庫已經有別的測試的資料）。</summary>
    public async Task<string> CreateDatabaseAsync()
    {
        var name = $"fresh_{Guid.NewGuid():N}";
        var created = await _postgres.ExecScriptAsync($"CREATE DATABASE {name};");
        Assert.Equal(0, created.ExitCode);
        return new NpgsqlConnectionStringBuilder(_postgres.GetConnectionString()) { Database = name }.ConnectionString;
    }

    public HttpClient CreateAdminClient()
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Add(AdminKeyAuthenticationHandler.HeaderName, AdminKey);
        return client;
    }

    public HttpClient CreateTenantClient(string apiKey)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        return client;
    }

    /// <summary>Creates a tenant with a unique identifier and issues one API key for it.</summary>
    public async Task<(TenantResponse Tenant, CreatedApiKey Key)> CreateTenantWithKeyAsync(
        string prefix, DateTimeOffset? expiresAt = null)
    {
        using var admin = CreateAdminClient();
        var identifier = $"{prefix}-{Guid.NewGuid():N}"[..30];

        using var tenantResponse = await admin.PostAsJsonAsync("/admin/tenants", new CreateTenantRequest(identifier, prefix));
        tenantResponse.EnsureSuccessStatusCode();
        var tenant = (await tenantResponse.Content.ReadFromJsonAsync<TenantResponse>())!;

        var key = await IssueKeyAsync(tenant.Id, expiresAt);
        return (tenant, key);
    }

    public async Task<CreatedApiKey> IssueKeyAsync(string tenantId, DateTimeOffset? expiresAt = null)
    {
        using var admin = CreateAdminClient();
        using var response = await admin.PostAsJsonAsync(
            $"/admin/tenants/{tenantId}/api-keys", new CreateApiKeyRequest("test", ["ontology:read"], expiresAt));
        response.EnsureSuccessStatusCode();
        return (await response.Content.ReadFromJsonAsync<CreatedApiKey>())!;
    }
}

[CollectionDefinition(Name)]
public sealed class PlatformTestGroup : ICollectionFixture<PlatformFactory>
{
    public const string Name = "Platform";
}