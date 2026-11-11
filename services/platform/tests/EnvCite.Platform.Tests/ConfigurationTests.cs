using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

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
    public void InvalidSettingFailsAtStartupAndNamesTheKey(string key, string value, string expected)
    {
        var message = StartFailure(factory.WithWebHostBuilder(b => b.UseSetting(key, value)));

        Assert.Contains(expected, message, StringComparison.Ordinal);
    }

    [Fact]
    public void MissingConnectionStringFailsAtStartup()
    {
        var message = StartFailure(factory.WithWebHostBuilder(b => b.UseSetting("ConnectionStrings:Platform", "")));

        Assert.Contains("The DatabaseOptions.Platform field is required", message, StringComparison.Ordinal);
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