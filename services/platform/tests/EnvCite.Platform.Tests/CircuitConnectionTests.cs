using System.Net;
using System.Security.Claims;

using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 互動式 circuit 的連線（<c>/_blazor</c>）認得出誰：入口的工作區是互動式頁面，連線上沒有路徑裡的租戶，
/// 瀏覽器又可能同時帶著營運者與入口成員兩張 cookie。連線本身不經授權，所以這裡在連線路徑上掛一個只回報使用者的端點，
/// 看中介層交給 circuit 的是什麼（circuit 啟動時再依第一個網址挑一邊，見 <c>PlatformAuthenticationStateProviderTests</c>）。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
public sealed class CircuitConnectionTests(PlatformFactory factory)
{
    private const string Probe = "/_blazor/probe";

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    /// <summary>連線路徑上的探針：回報使用者有幾個 identity、入口成員與營運者的 id。</summary>
    private sealed class ProbeFilter : IStartupFilter
    {
        public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next) => app =>
        {
            next(app);
            app.Map(Probe, branch => branch.Run(context =>
            {
                var user = context.User;
                return context.Response.WriteAsync(
                    $"identities={user.Identities.Count()};portal={user.FindFirst(PlatformClaimTypes.PortalUserId)?.Value ?? "-"};"
                    + $"operator={user.FindFirst(PlatformClaimTypes.OperatorId)?.Value ?? "-"}");
            }));
        };
    }

    private WebApplicationFactory<Program> Host() =>
        factory.WithWebHostBuilder(builder => builder.ConfigureServices(services => services.AddTransient<IStartupFilter, ProbeFilter>()));

    private static HttpClient Client(WebApplicationFactory<Program> host, params string[] cookies)
    {
        var client = host.CreateClient(new WebApplicationFactoryClientOptions { BaseAddress = new Uri("https://localhost"), AllowAutoRedirect = false });
        if (cookies.Length > 0)
            client.DefaultRequestHeaders.Add("Cookie", string.Join("; ", cookies));

        return client;
    }

    private static string PortalCookie(WebApplicationFactory<Program> host, ClaimsPrincipal session)
    {
        var options = host.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.PortalCookieScheme);
        var ticket = options.TicketDataFormat.Protect(new AuthenticationTicket(session, new AuthenticationProperties(), PlatformAuth.PortalCookieScheme));
        return $"{options.Cookie.Name}={ticket}";
    }

    private static string OperatorCookie(WebApplicationFactory<Program> host)
    {
        var options = host.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        var session = host.SessionOf(host.EnsureOperatorAsync().GetAwaiter().GetResult());
        return $"{options.Cookie.Name}={host.CookieFor(session)}";
    }

    private async Task<(WebApplicationFactory<Program> Host, TenantResponse Tenant, string UserId, string Cookie)> MemberAsync(string slug)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(slug);
        var (userId, _) = await PortalTestSupport.AddMemberAsync(factory, tenant.Id, PortalTestSupport.NewEmail(), CustomerRoles.Viewer);
        var host = Host();
        return (host, tenant, userId, PortalCookie(host, await PortalTestSupport.MemberPrincipalAsync(factory, tenant.Id, userId)));
    }

    private static async Task<(string Body, bool PortalCookieCleared)> ProbeAsync(HttpClient client)
    {
        using var response = await client.GetAsync(new Uri(Probe, UriKind.Relative), Ct);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var cleared = response.Headers.TryGetValues("Set-Cookie", out var values)
            && values.Any(v => v.StartsWith("EnvCite.Portal=", StringComparison.Ordinal));
        return (await response.Content.ReadAsStringAsync(Ct), cleared);
    }

    // 這就是「工作區被導去後台登入頁」的原因：連線只認營運者的 cookie，入口成員在 circuit 裡永遠是匿名。
    [Fact]
    public async Task APortalMemberIsRecognisedOnTheCircuitConnection()
    {
        var (host, _, userId, cookie) = await MemberAsync("ccn-a");
        await using var _ = host;
        using var client = Client(host, cookie);

        var (body, cleared) = await ProbeAsync(client);

        Assert.Equal($"identities=1;portal={userId};operator=-", body);
        Assert.False(cleared); // 有效的 cookie 不會因為連線而被收回
    }

    [Fact]
    public async Task AnOperatorIsStillRecognisedAsBeforeAndWithoutAnyPortalIdentity()
    {
        await using var host = Host();
        using var client = Client(host, OperatorCookie(host));

        var (body, _) = await ProbeAsync(client);

        Assert.StartsWith("identities=1;portal=-;operator=", body, StringComparison.Ordinal);
        Assert.DoesNotContain("operator=-", body, StringComparison.Ordinal);
    }

    // 兩張 cookie 並存：連線把兩個 identity 都交給 circuit，由 circuit 依第一個網址挑一個，不在這裡猜。
    [Fact]
    public async Task BothCookiesTogetherReachTheCircuitAsTwoSeparateIdentities()
    {
        var (host, _, userId, portalCookie) = await MemberAsync("ccn-b");
        await using var _ = host;
        using var client = Client(host, OperatorCookie(host), portalCookie);

        var (body, cleared) = await ProbeAsync(client);

        Assert.StartsWith($"identities=2;portal={userId};operator=", body, StringComparison.Ordinal);
        Assert.DoesNotContain("operator=-", body, StringComparison.Ordinal);
        Assert.False(cleared);
    }

    [Fact]
    public async Task NoCookieMeansAnonymousOnTheCircuitConnection()
    {
        await using var host = Host();
        using var client = Client(host);

        Assert.Equal("identities=1;portal=-;operator=-", (await ProbeAsync(client)).Body); // 匿名的那一個空 identity
    }

    // 失效要靠 cookie 自己的租戶 claim 判斷（連線沒有環境租戶）：改密碼換了安全戳記，這張 cookie 就不再是人。
    [Fact]
    public async Task AMemberWhoseSessionWasEndedIsNotRecognisedAndTheDeadCookieIsCleared()
    {
        var (host, tenant, userId, cookie) = await MemberAsync("ccn-c");
        await using var _ = host;
        using var client = Client(host, cookie);
        Assert.Equal($"identities=1;portal={userId};operator=-", (await ProbeAsync(client)).Body);

        Assert.True((await factory.Services.GetRequiredService<IMemberAdmin>().ResetPasswordAsync(OperatorSession.Principal(), tenant.Id, userId, Ct)).IsOk);
        var (body, cleared) = await ProbeAsync(client);

        Assert.Equal("identities=1;portal=-;operator=-", body);
        Assert.True(cleared);
    }

    [Fact]
    public async Task ADisabledMemberIsNotRecognisedEvenWithTheStampUnchanged()
    {
        var (host, tenant, userId, cookie) = await MemberAsync("ccn-d");
        await using var _ = host;
        using var client = Client(host, cookie);

        var flagged = await factory.Services.GetRequiredService<TenantScopes>().InAsync<bool>(tenant.Id, async scope =>
        {
            var users = scope.Get<UserManager<ApplicationUser>>();
            var user = (await users.FindByIdAsync(userId))!;
            user.Disabled = true; // 直接設旗標、不動戳記
            return AdminOutcome.Ok((await users.UpdateAsync(user)).Succeeded);
        });
        Assert.True(flagged.Value);

        Assert.Equal("identities=1;portal=-;operator=-", (await ProbeAsync(client)).Body);
    }

    // 帳號在 A 租戶，cookie 的租戶 claim 卻寫 B：在 B 裡找不到這個人。
    [Fact]
    public async Task ACookieWhoseTenantClaimNamesAnotherTenantIsNotRecognised()
    {
        var (host, _, _, _) = await MemberAsync("ccn-e");
        await using var _ = host;
        var (other, _) = await factory.CreateTenantWithKeyAsync("ccn-f");
        var (userId, _) = await PortalTestSupport.AddMemberAsync(factory, other.Id, PortalTestSupport.NewEmail(), CustomerRoles.Viewer);
        var session = await PortalTestSupport.MemberPrincipalAsync(factory, other.Id, userId);
        var elsewhere = new ClaimsPrincipal(new ClaimsIdentity(
            session.Claims.Where(c => c.Type != PlatformClaimTypes.TenantIdentifier).Append(new Claim(PlatformClaimTypes.TenantIdentifier, "ccn-e")),
            "Identity.Application"));
        using var client = Client(host, PortalCookie(host, elsewhere));

        Assert.Equal("identities=1;portal=-;operator=-", (await ProbeAsync(client)).Body);
    }
}