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
    [InlineData("Platform:Plans:free:PermitLimit", "0", "Platform:Plans:free needs a positive PermitLimit")]
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

    [Fact]
    public void ProductionWithoutGoogleCredentialsFailsAtStartup()
    {
        var message = StartFailure(factory.WithWebHostBuilder(b => b
            .UseEnvironment("Production")
            .UseSetting("Platform:Ui:Google:ClientId", "")));

        Assert.Contains("Platform:Ui:Google:ClientId and Platform:Ui:Google:ClientSecret are not configured", message, StringComparison.Ordinal);
    }

    [Fact]
    public void ValidSettingsAreInjectedAsOptions()
    {
        var plans = factory.Services.GetRequiredService<IOptions<Modules.ApiAccess.ApiAccessOptions>>().Value;

        Assert.Equal(PlatformFactory.RateLimitPermits, plans.Plans["free"].PermitLimit);
        Assert.Equal("pro", plans.TenantPlans[PlatformFactory.ProTenant]);
    }
}