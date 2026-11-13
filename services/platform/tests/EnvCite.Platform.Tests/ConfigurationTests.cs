using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 設定錯誤要在主機啟動時失敗（k8s 看到 CrashLoopBackOff 與明確訊息），不是等到第一個請求。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class ConfigurationTests(PlatformFactory factory)
{
    private static string StartFailure(WebApplicationFactory<Program> host)
    {
        using (host)
        {
            var failure = Assert.ThrowsAny<Exception>(() => host.CreateClient());
            return failure.ToString();
        }
    }

    [Theory]
    [InlineData("Ontology:Address", "not-a-uri", "Ontology:Address must be an absolute URI")]
    [InlineData("Consult:Address", "", "Consult:Address must be an absolute URI")]
    [InlineData("Consult:Timeout", "00:00:00", "Consult:Timeout must be positive")]
    [InlineData("Platform:TenantPlans:acme", "gold", "Platform:TenantPlans:acme names unknown plan 'gold'")]
    [InlineData("Platform:DefaultPlan", "gold", "Platform:DefaultPlan names unknown plan 'gold'")]
    [InlineData("Platform:EntitlementRefresh", "00:00:00", "Platform:EntitlementRefresh must be at least one second")]
    [InlineData("Platform:Plans:standard:PermitLimit", "0", "Platform:Plans:standard needs a positive PermitLimit")]
    [InlineData("Platform:Idempotency:Window", "00:00:00", "Platform:Idempotency:Window must be between 1 minute and 7 days")]
    [InlineData("Platform:Portal:SignInAttemptsPerMinute", "0", "Platform:Portal:SignInAttemptsPerMinute must be between 1 and 1000")]
    [InlineData("Platform:Ui:SignInAttemptsPerMinute", "0", "Platform:Ui:SignInAttemptsPerMinute must be between 1 and 1000")]
    [InlineData("Platform:Ui:SessionMaxAge", "00:00:00", "Platform:Ui:SessionMaxAge must be between 1 minute and 7 days")]
    [InlineData("Platform:Ui:SessionMaxAge", "8.00:00:00", "Platform:Ui:SessionMaxAge must be between 1 minute and 7 days")]
    [InlineData("Platform:Directory:MaxPageSize", "0", "Platform:Directory:MaxPageSize must be between 1 and 1000")]
    [InlineData("Platform:Directory:MaxPageSize", "1001", "Platform:Directory:MaxPageSize must be between 1 and 1000")]
    [InlineData("Platform:Directory:RequestsPerMinute", "0", "Platform:Directory:RequestsPerMinute must be between 1 and 10000")]
    [InlineData("Platform:Directory:StaleAfter", "00:00:30", "Platform:Directory:StaleAfter must be at least one minute")]
    [InlineData("Platform:Brand:Name", "", "Platform:Brand:Name must not be blank")]
    [InlineData("Platform:Brand:Name", "   ", "Platform:Brand:Name must not be blank")]
    [InlineData("Platform:Brand:Name", "一二三四五六七八九十一二三四五六七八九十一二三四五六", "Platform:Brand:Name must be at most 25 characters")]
    [InlineData("Platform:Brand:Name", "名稱\n第二行", "Platform:Brand:Name must not contain control characters")]
    public void InvalidSettingFailsAtStartupAndNamesTheKey(string key, string value, string expected)
    {
        var message = StartFailure(factory.WithWebHostBuilder(b => b.UseSetting(key, value)));

        Assert.Contains(expected, message, StringComparison.Ordinal);
    }

    // 寄信預設關閉；開了就必須指名中繼、寄件者與連結來源，否則啟動就停下並指名鍵（而不是第一封信才失敗）。
    [Theory]
    [InlineData("Platform:Mail:Host", "", "Platform:Mail:Host is required")]
    [InlineData("Platform:Mail:Port", "0", "Platform:Mail:Port must be between 1 and 65535")]
    [InlineData("Platform:Mail:From", "not-a-mail", "Platform:Mail:From must be a valid mail address")]
    [InlineData("Platform:Mail:BaseUrl", "", "Platform:Mail:BaseUrl must be an https origin")]
    [InlineData("Platform:Mail:BaseUrl", "http://platform.example", "Platform:Mail:BaseUrl must be an https origin")]
    [InlineData("Platform:Mail:BaseUrl", "https://platform.example/portal", "Platform:Mail:BaseUrl must be an https origin")]
    [InlineData("Platform:Mail:TimeoutSeconds", "0", "Platform:Mail:TimeoutSeconds must be between 1 and 120")]
    [InlineData("Platform:Mail:LinkLifetime", "00:10:00", "Platform:Mail:LinkLifetime must be between 1 hour and 14 days")]
    [InlineData("Platform:Mail:LinkLifetime", "15.00:00:00", "Platform:Mail:LinkLifetime must be between 1 hour and 14 days")]
    [InlineData("Platform:Mail:Security", "42", "Platform:Mail:Security must be one of")]
    [InlineData("Platform:Mail:Security", "Bogus", "Platform:Mail:Security")]
    [InlineData("Platform:Mail:Username", "login@relay.test", "Platform:Mail:Password is required when Platform:Mail:Username is set")]
    [InlineData("Platform:Mail:Password", "only-a-password", "Platform:Mail:Username is required when Platform:Mail:Password is set")]
    [InlineData("Platform:Mail:ReplyTo", "not-a-mail", "Platform:Mail:ReplyTo must be a valid mail address")]
    public void InvalidMailSettingFailsAtStartupWhenMailIsEnabled(string key, string value, string expected)
    {
        var message = StartFailure(factory.WithWebHostBuilder(b => b
            .UseSetting("Platform:Mail:Enabled", "true")
            .UseSetting("Platform:Mail:Host", "relay.test")
            .UseSetting("Platform:Mail:From", "envcite@relay.test")
            .UseSetting("Platform:Mail:BaseUrl", "https://platform.test")
            .UseSetting(key, value)));

        Assert.Contains(expected, message, StringComparison.Ordinal);
    }

    // 系統名稱有預設值，不必設就能啟動；上限是 25 個字（以看到的字計，不是碼元），剛好 25 個字可以。
    [Fact]
    public void TheBrandNameDefaultsAndTheLimitIsInclusive()
    {
        Assert.Equal("環境知識", new BrandOptions().Name);

        using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Brand:Name", "一二三四五六七八九十一二三四五六七八九十一二三四五")).Server;
        Assert.Equal(25, host.Services.GetRequiredService<IOptions<BrandOptions>>().Value.Name.Length);
    }

    [Fact]
    public void MailIsOffByDefaultAndItsSettingsAreIgnoredWhileOff()
    {
        Assert.False(new MailOptions().Enabled);

        // 沒啟用就不檢查（部署庫可以先放著不完整的值）。
        using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Mail:Port", "0")).Server;
        Assert.NotNull(host);
    }

    // EF Core 把每條 SQL 以 Information 記在 Database.Command，一次命令列或一個請求就是一長串；預設只留 Warning 以上，
    // 其餘的 EF 日誌（遷移進度等）不受影響。要看 SQL 的人以環境變數調回（見 CONTRIBUTING〈看 SQL 日誌〉）。
    [Fact]
    public void EntityFrameworkSqlIsLoggedOnlyAtWarningAndAboveWhileTheRestOfEfIsNot()
    {
        var loggers = factory.Services.GetRequiredService<ILoggerFactory>();
        var sql = loggers.CreateLogger("Microsoft.EntityFrameworkCore.Database.Command");

        Assert.False(sql.IsEnabled(LogLevel.Information));
        Assert.True(sql.IsEnabled(LogLevel.Warning));
        Assert.True(loggers.CreateLogger("Microsoft.EntityFrameworkCore.Migrations").IsEnabled(LogLevel.Information));
    }

    [Fact]
    public void MissingConnectionStringFailsAtStartup()
    {
        var message = StartFailure(factory.WithWebHostBuilder(b => b.UseSetting("ConnectionStrings:Platform", "")));

        Assert.Contains("ConnectionStrings:Platform is required", message, StringComparison.Ordinal);
    }

    // 後台是可選功能，預設關閉：沒開就不掛 /ui，API 照常。
    [Fact]
    public async Task ProductionWithTheUiNotEnabledDisablesItButTheApiRuns()
    {
        await using var host = factory.WithWebHostBuilder(b => b
            .UseEnvironment("Production")
            .UseSetting("Platform:Ui:Enabled", "false"));
        using var client = host.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var health = await client.GetAsync(new Uri("/health/ready", UriKind.Relative), TestContext.Current.CancellationToken);
        using var api = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
        using var ui = await client.GetAsync(new Uri("/ui", UriKind.Relative), TestContext.Current.CancellationToken);
        using var login = await client.GetAsync(new Uri("/ui/login", UriKind.Relative), TestContext.Current.CancellationToken);
        using var root = await client.GetAsync(new Uri("/", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(System.Net.HttpStatusCode.OK, health.StatusCode);
        Assert.Equal(System.Net.HttpStatusCode.Unauthorized, api.StatusCode); // API 在，只是沒帶 key
        Assert.Equal(System.Net.HttpStatusCode.NotFound, ui.StatusCode);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, login.StatusCode);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, root.StatusCode);
    }

    // 與入口對稱：什麼都不設就是關。
    [Fact]
    public void TheUiAndThePortalAreOffUnlessEnabled()
    {
        Assert.False(new Web.Auth.UiOptions().Enabled);
        Assert.False(new Web.Auth.PortalOptions().Enabled);
    }

    // 目錄同步同樣預設關閉：部署者不設就沒有這個功能面（共用的測試主機為了測它才開）。
    [Fact]
    public void DirectorySyncIsOffUnlessEnabled() =>
        Assert.False(new Modules.Members.DirectorySync.DirectoryOptions().Enabled);

    // 要明確開：Production 設了 Platform:Ui:Enabled 才掛 /ui（登入頁在，而且是公開的）。
    [Fact]
    public async Task ProductionWithTheUiEnabledServesTheLoginPage()
    {
        await using var host = factory.WithWebHostBuilder(b => b
            .UseEnvironment("Production")
            .UseSetting("Platform:Ui:Enabled", "true"));
        using var client = host.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var login = await client.GetAsync(new Uri("/ui/login", UriKind.Relative), TestContext.Current.CancellationToken);
        using var ui = await client.GetAsync(new Uri("/ui", UriKind.Relative), TestContext.Current.CancellationToken);
        using var root = await client.GetAsync(new Uri("/", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(System.Net.HttpStatusCode.OK, login.StatusCode);
        Assert.Equal(System.Net.HttpStatusCode.Redirect, ui.StatusCode); // 沒登入 → 導去登入，不是 404
        Assert.Equal(System.Net.HttpStatusCode.Redirect, root.StatusCode); // 根路徑 → 導去 /ui
    }

    [Fact]
    public void ValidSettingsAreInjectedAsOptions()
    {
        var plans = factory.Services.GetRequiredService<IOptions<Modules.ApiAccess.ApiAccessOptions>>().Value;

        Assert.Equal(PlatformFactory.RateLimitPermits, plans.Plans["standard"].PermitLimit);
        Assert.Equal("pro", plans.TenantPlans[PlatformFactory.ProTenant]);
    }
}