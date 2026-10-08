using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Tests;

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
    [InlineData("Ontology:InternalToken", "", "Ontology:InternalToken is not configured")]
    [InlineData("Consult:Address", "", "Consult:Address must be an absolute URI")]
    [InlineData("Consult:Timeout", "00:00:00", "Consult:Timeout must be positive")]
    [InlineData("Platform:AdminKeySha256", "not-hex", "Platform:AdminKeySha256 must be 64 hexadecimal")]
    [InlineData("Platform:TenantPlans:acme", "gold", "Platform:TenantPlans:acme names unknown plan 'gold'")]
    [InlineData("Platform:DefaultPlan", "gold", "Platform:DefaultPlan names unknown plan 'gold'")]
    [InlineData("Platform:Plans:standard:PermitLimit", "0", "Platform:Plans:standard needs a positive PermitLimit")]
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

    // 後台是可選功能：Secret 常晚於程式碼上線，缺 Google 憑證不能讓整個 API 起不來。
    [Fact]
    public async Task ProductionWithoutGoogleCredentialsDisablesTheUiButTheApiRuns()
    {
        await using var host = factory.WithWebHostBuilder(b => b
            .UseEnvironment("Production")
            .UseSetting("Platform:Ui:Google:ClientId", string.Empty)
            .UseSetting("Platform:Ui:Google:ClientSecret", string.Empty));
        using var client = host.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var health = await client.GetAsync(new Uri("/health/ready", UriKind.Relative));
        using var api = await client.GetAsync(new Uri("/v2/me", UriKind.Relative));
        using var ui = await client.GetAsync(new Uri("/ui", UriKind.Relative));
        using var login = await client.GetAsync(new Uri("/ui/login", UriKind.Relative));

        Assert.Equal(System.Net.HttpStatusCode.OK, health.StatusCode);
        Assert.Equal(System.Net.HttpStatusCode.Unauthorized, api.StatusCode); // API 在，只是沒帶 key
        Assert.Equal(System.Net.HttpStatusCode.NotFound, ui.StatusCode);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, login.StatusCode);
    }

    [Fact]
    public void ValidSettingsAreInjectedAsOptions()
    {
        var plans = factory.Services.GetRequiredService<IOptions<Modules.ApiAccess.ApiAccessOptions>>().Value;

        Assert.Equal(PlatformFactory.RateLimitPermits, plans.Plans["standard"].PermitLimit);
        Assert.Equal("pro", plans.TenantPlans[PlatformFactory.ProTenant]);
    }
}