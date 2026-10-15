using System.Net;

using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class DataProtectionTests(PlatformFactory factory)
{
    private (string Name, string Value) SignInCookie(IServiceProvider services)
    {
        var principal = factory.SessionOf(factory.EnsureOperatorAsync().GetAwaiter().GetResult());
        var cookie = services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>()
            .Get(PlatformAuth.UiCookieScheme);
        return (cookie.Cookie.Name!, cookie.TicketDataFormat.Protect(new AuthenticationTicket(principal, PlatformAuth.UiCookieScheme)));
    }

    // 多副本的前提：金鑰存在共用的資料庫，而不是每個副本各自的記憶體或本機檔案。
    [Fact]
    public async Task KeysArePersistedInTheDatabase()
    {
        _ = SignInCookie(factory.Services);

        await using var scope = factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<TenancyDbContext>();

        Assert.True(await db.DataProtectionKeys.AnyAsync(cancellationToken: TestContext.Current.CancellationToken));
    }

    // 另一個副本（獨立的 host，連同一個資料庫）必須解得開這個副本簽的登入 cookie。
    [Fact]
    public async Task CookieIssuedByOneReplicaIsAcceptedByAnother()
    {
        var (name, value) = SignInCookie(factory.Services);

        await using var otherReplica = factory.WithWebHostBuilder(_ => { });
        Assert.NotSame(factory.Services, otherReplica.Services);

        using var client = otherReplica.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        client.DefaultRequestHeaders.Add("Cookie", $"{name}={value}");

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}