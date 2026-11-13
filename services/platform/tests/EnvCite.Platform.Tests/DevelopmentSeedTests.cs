using EnvCite.Platform.Api;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 本機開發的第一組資料。每個測試用自己的全新資料庫（共用的庫已經有別的測試的資料，也不該多出一個 dev 租戶）。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class DevelopmentSeedTests(PlatformFactory factory)
{
    private async Task<WebApplicationFactory<Program>> HostAsync(string environment, bool seed)
    {
        var database = await factory.CreateDatabaseAsync();
        return factory.WithWebHostBuilder(builder =>
        {
            builder.UseEnvironment(environment);
            builder.UseSetting("ConnectionStrings:Platform", database);
            builder.UseSetting("Platform:SeedDevelopmentData", seed ? "true" : "false");
        });
    }

    private static async Task<TenantSummary?> DevTenantAsync(IServiceProvider services) =>
        (await services.GetRequiredService<ITenantDirectory>().ListAsync(CancellationToken.None))
            .FirstOrDefault(t => t.Identifier == DevelopmentSeed.TenantIdentifier);

    [Fact]
    public async Task StartingInDevelopmentSeedsTheTenantAndItsOwnerAsTheSystem()
    {
        await using var host = await HostAsync("Development", seed: true);

        var tenant = await DevTenantAsync(host.Services);
        Assert.NotNull(tenant);
        var members = await host.Services.GetRequiredService<IMemberAdmin>().ListAsync(tenant.Id, CancellationToken.None);
        var owner = Assert.Single(members.Value!);
        Assert.Equal((DevelopmentSeed.OwnerEmail, CustomerRoles.Owner, true), (owner.Email, owner.Role, owner.MustChangePassword));

        // 稽核誠實地記「系統」做的，不假裝是某把 admin key 或某個人。
        var audit = host.Services.GetRequiredService<IAuditQuery>();
        foreach (var action in new[] { AuditActions.TenantCreated, AuditActions.MemberCreated })
        {
            var evt = Assert.Single((await audit.QueryAsync(new AuditFilter(Action: action), CancellationToken.None)).Items);
            Assert.Equal(AuditActors.System, evt.ActorType);
        }
    }

    [Fact]
    public async Task StartingInDevelopmentSeedsADemoOperatorAsTheSystem()
    {
        await using var host = await HostAsync("Development", seed: true);

        var operators = (await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!;
        var demo = Assert.Single(operators);
        Assert.Equal((DevelopmentSeed.OperatorEmail, true, false, false), (demo.Email, demo.MustChangePassword, demo.TwoFactorEnabled, demo.Disabled));

        var created = Assert.Single((await host.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Action: AuditActions.OperatorCreated), CancellationToken.None)).Items);
        Assert.Equal((AuditActors.System, demo.Id), (created.ActorType, created.TargetId));
    }

    // 只有 Development 而且設定開著才種：正式環境即使誤開設定也不會有預設租戶。
    [Theory]
    [InlineData("Production", true)]
    [InlineData("Staging", true)]
    [InlineData("Development", false)]
    public async Task NothingIsSeededOutsideDevelopmentOrWithTheSettingOff(string environment, bool seed)
    {
        await using var host = await HostAsync(environment, seed);

        Assert.Empty(await host.Services.GetRequiredService<ITenantDirectory>().ListAsync(CancellationToken.None));
        Assert.Empty((await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!);
    }

    [Fact]
    public async Task TheDemoOperatorSignsInWithTheShownPasswordThenMustChangeItAndEnrolAnAuthenticator()
    {
        await using var host = await HostAsync("Development", seed: false);

        var seeded = await DevelopmentSeed.RunOperatorAsync(host.Services, CancellationToken.None);

        Assert.NotNull(seeded);
        Assert.True(seeded.Password.Length >= 16);
        Assert.Contains(seeded.Password, DevelopmentSeed.Describe(seeded), StringComparison.Ordinal);
        Assert.Contains("/ui/login", DevelopmentSeed.Describe(seeded), StringComparison.Ordinal);
        using var browser = new PortalBrowser(host);
        Assert.Equal(OperatorTestSupport.UiPassword, (await browser.SubmitAsync(OperatorTestSupport.UiLogin, Credentials(seeded.Email, seeded.Password))).Location);
        Assert.Equal(OperatorTestSupport.UiHome, (await browser.SubmitAsync(OperatorTestSupport.UiPassword, PasswordChange(seeded.Password, NewPassword))).Location);
        // 營運者沒有本機的例外：還要註冊驗證器才進得了後台。
        Assert.Equal(OperatorTestSupport.UiSecurity, (await browser.GetAsync(OperatorTestSupport.UiHome)).Location);
    }

    // 與擁有者相同的規則：一次性密碼只回傳給呼叫端（它印在終端機），不進日誌，也不進稽核。
    [Fact]
    public async Task TheDemoOperatorPasswordReachesNeitherTheLogsNorTheAuditTrail()
    {
        var logs = new CapturingLoggerProvider();
        await using var host = (await HostAsync("Development", seed: false)).WithWebHostBuilder(b => b.ConfigureLogging(l => l.AddProvider(logs)));

        var seeded = await DevelopmentSeed.RunOperatorAsync(host.Services, CancellationToken.None);

        Assert.NotNull(seeded);
        var audit = await host.Services.GetRequiredService<IAuditQuery>().QueryAsync(new AuditFilter(Action: AuditActions.OperatorCreated), CancellationToken.None);
        var dump = System.Text.Json.JsonSerializer.Serialize(audit) + string.Join('\n', logs.Messages);
        Assert.DoesNotContain(seeded.Password, dump, StringComparison.Ordinal);
    }

    [Fact]
    public async Task RunningTheOperatorSeedAgainChangesNothingAndShowsNoPassword()
    {
        await using var host = await HostAsync("Development", seed: false);
        var first = await DevelopmentSeed.RunOperatorAsync(host.Services, CancellationToken.None);

        var second = await DevelopmentSeed.RunOperatorAsync(host.Services, CancellationToken.None);

        Assert.NotNull(first);
        Assert.Null(second);
        Assert.Single((await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!);
    }

    // 已經有營運者的環境（例如開發者自己建過）不會被硬塞一位示範營運者進去。
    [Fact]
    public async Task AnEnvironmentThatAlreadyHasOperatorsIsLeftAlone()
    {
        await using var host = await HostAsync("Development", seed: false);
        var admin = host.Services.GetRequiredService<IOperatorAdmin>();
        Assert.True((await admin.CreateAsync(OperatorSession.Principal(), "mine@example.com", null, CancellationToken.None)).IsOk);

        var seeded = await DevelopmentSeed.RunOperatorAsync(host.Services, CancellationToken.None);

        Assert.Null(seeded);
        Assert.Equal("mine@example.com", Assert.Single((await admin.ListAsync(CancellationToken.None)).Value!).Email);
    }

    [Fact]
    public async Task TheOwnerCanSignInWithTheShownPasswordAndMustChangeItFirst()
    {
        await using var host = await HostAsync("Development", seed: false);

        var owner = await DevelopmentSeed.RunAsync(host.Services, CancellationToken.None);

        Assert.NotNull(owner);
        Assert.True(owner.Password.Length >= 16);
        Assert.Contains(owner.Password, DevelopmentSeed.Describe(owner), StringComparison.Ordinal);
        Assert.Contains($"/portal/{owner.TenantIdentifier}/login", DevelopmentSeed.Describe(owner), StringComparison.Ordinal);
        using var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, owner.TenantIdentifier, owner.Email, owner.Password);
    }

    [Fact]
    public async Task RunningItAgainChangesNothingAndShowsNoPassword()
    {
        await using var host = await HostAsync("Development", seed: false);
        var first = await DevelopmentSeed.RunAsync(host.Services, CancellationToken.None);

        var second = await DevelopmentSeed.RunAsync(host.Services, CancellationToken.None);

        Assert.NotNull(first);
        Assert.Null(second);
        var tenants = await host.Services.GetRequiredService<ITenantDirectory>().ListAsync(CancellationToken.None);
        Assert.Single(tenants);
        var members = await host.Services.GetRequiredService<IMemberAdmin>().ListAsync(tenants[0].Id, CancellationToken.None);
        Assert.Single(members.Value!);
    }

    // 已經有人在用的 dev 租戶（例如開發者自己加過成員）不會被硬塞一位擁有者進去。
    [Fact]
    public async Task ATenantThatAlreadyHasMembersIsLeftAlone()
    {
        await using var host = await HostAsync("Development", seed: false);
        var provisioner = host.Services.GetRequiredService<ITenantProvisioner>();
        var tenant = (await provisioner.CreateAsync(OperatorSession.Principal(), DevelopmentSeed.TenantIdentifier, "mine", CancellationToken.None)).Value!;
        var members = host.Services.GetRequiredService<IMemberAdmin>();
        await members.CreateAsync(OperatorSession.Principal(), tenant.Id, "someone@example.com", null, CustomerRoles.Viewer, CancellationToken.None);

        var seeded = await DevelopmentSeed.RunAsync(host.Services, CancellationToken.None);

        Assert.Null(seeded);
        var list = (await members.ListAsync(tenant.Id, CancellationToken.None)).Value!;
        Assert.Equal("someone@example.com", Assert.Single(list).Email);
    }

    // 租戶在但沒有成員：補上擁有者，不重複建租戶。
    [Fact]
    public async Task AnEmptyDevTenantGetsItsOwnerWithoutASecondTenant()
    {
        await using var host = await HostAsync("Development", seed: false);
        var tenant = (await host.Services.GetRequiredService<ITenantProvisioner>()
            .CreateAsync(OperatorSession.Principal(), DevelopmentSeed.TenantIdentifier, "mine", CancellationToken.None)).Value!;

        var seeded = await DevelopmentSeed.RunAsync(host.Services, CancellationToken.None);

        Assert.NotNull(seeded);
        Assert.Single(await host.Services.GetRequiredService<ITenantDirectory>().ListAsync(CancellationToken.None));
        var list = (await host.Services.GetRequiredService<IMemberAdmin>().ListAsync(tenant.Id, CancellationToken.None)).Value!;
        Assert.Equal(DevelopmentSeed.OwnerEmail, Assert.Single(list).Email);
    }
}