using System.Net;
using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;
using EnvLaw.Platform.Web.Presentation;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Tests;

public static class OperatorPresentationTests
{
    [Fact]
    public static void EveryAuditActionThatIsWrittenHasAReadableLabel() =>
        Assert.All(AuditActions.All, action => Assert.NotEqual(action, AuditLabels.Action(action)));

    [Fact]
    public static void ShortenedIdsKeepTheRandomEndNotTheTimestampStart()
    {
        // 兩個同一段時間建立的 UUIDv7：開頭相同，結尾不同。
        var a = "0198c8b2-7d1e-7a3b-9f10-111111111111";
        var b = "0198c8b2-7d1e-7a3b-9f10-222222222222";

        Assert.NotEqual(AuditLabels.Shorten(a), AuditLabels.Shorten(b));
        Assert.Equal("short", AuditLabels.Shorten("short"));
    }

    [Fact]
    public static void AnUnknownActionIsShownAsItsKeyRatherThanHidden() =>
        Assert.Equal("future.event", AuditLabels.Action("future.event"));

    [Theory]
    [InlineData(AuditActors.OperatorKey, null, "營運者金鑰")]
    [InlineData(AuditActors.OperatorUser, "op@example.com", "op@example.com")]
    [InlineData(AuditActors.TenantKey, "0198c8b2-7d1e-7a3b-9f10-aaaaaaaaaaaa", "租戶 API key …aaaaaaaa")]
    [InlineData(AuditActors.Anonymous, null, "匿名")]
    public static void ActorsAreNamedForPeople(string type, string? id, string expected) =>
        Assert.Equal(expected, AuditLabels.Actor(type, id));

    [Theory]
    [InlineData("2026-10-08T02:00:00Z", "2026-10-08 10:00:00")]
    [InlineData("2026-10-08T23:30:00Z", "2026-10-09 07:30:00")] // 跨日
    [InlineData("2026-10-08T10:00:00+08:00", "2026-10-08 10:00:00")]
    public static void TimesAreShownInTaipeiTime(string input, string expected) =>
        Assert.Equal(expected, TaipeiTime.Format(DateTimeOffset.Parse(input, System.Globalization.CultureInfo.InvariantCulture)));
}

[Collection(PlatformTestGroup.Name)]
public sealed class OperatorPagesTests(PlatformFactory factory)
{
    private HttpClient SignedInClient(WebApplicationFactory<Program>? host = null, string email = PlatformFactory.OperatorEmail)
    {
        var app = host ?? factory;
        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [new Claim(ClaimTypes.Email, email), new Claim(OperatorAllowlist.EmailVerifiedClaim, "true")],
            PlatformAuth.UiCookieScheme));
        var cookie = app.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        var ticket = cookie.TicketDataFormat.Protect(new AuthenticationTicket(principal, PlatformAuth.UiCookieScheme));
        var client = app.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        client.DefaultRequestHeaders.Add("Cookie", $"{cookie.Cookie.Name}={ticket}");
        return client;
    }

    // Blazor 會把元件參數裡的中文輸出成數字實體；瀏覽器解碼後一樣，測試也先解碼。
    private static async Task<string> PageAsync(HttpClient client, string path)
    {
        using var response = await client.GetAsync(new Uri(path, UriKind.Relative));
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync());
    }

    [Theory]
    [InlineData("/ui/tenants")]
    [InlineData("/ui/audit")]
    public async Task ThePagesNeedAnOperator(string path)
    {
        using var anonymous = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var response = await anonymous.GetAsync(new Uri(path, UriKind.Relative));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/login", response.Headers.Location?.AbsolutePath);
    }

    [Fact]
    public async Task TheTenantListShowsEachTenantWithItsPlan()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("list-a");
        using var client = SignedInClient();

        var html = await PageAsync(client, "/ui/tenants");

        Assert.Contains(tenant.Identifier, html, StringComparison.Ordinal);
        Assert.Contains("<th", html, StringComparison.Ordinal);
        Assert.Contains("standard", html, StringComparison.Ordinal); // 測試主機設了預設方案
        Assert.Contains($"/ui/audit?tenant={tenant.Id}", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ATenantWithoutAPlanIsMarkedSoNotJustByColour()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("list-b");
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));
        using var client = SignedInClient(host);

        var html = await PageAsync(client, "/ui/tenants");

        Assert.Contains(tenant.Identifier, html, StringComparison.Ordinal);
        Assert.Contains("沒有方案", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheAuditPageListsEventsNewestFirstWithReadableLabelsAndTheTenantName()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("audit-ui");
        using var client = SignedInClient();

        var html = await PageAsync(client, $"/ui/audit?tenant={tenant.Id}");

        Assert.Contains("建立租戶", html, StringComparison.Ordinal);
        Assert.Contains("簽發 API key", html, StringComparison.Ordinal);
        Assert.Contains(tenant.Identifier, html, StringComparison.Ordinal); // 租戶欄顯示識別碼，不是 Guid
        Assert.Contains("營運者金鑰", html, StringComparison.Ordinal);
        Assert.Contains(AuditLabels.Shorten(key.ApiKey.Id.ToString()), html, StringComparison.Ordinal); // 目標只顯示結尾；完整值在展開的細節
        Assert.True(
            html.IndexOf("簽發 API key", StringComparison.Ordinal) < html.IndexOf("建立租戶", StringComparison.Ordinal),
            "newer events come first");
    }

    [Fact]
    public async Task TheAuditPageFiltersByTenantAndByAction()
    {
        var (mine, _) = await factory.CreateTenantWithKeyAsync("audit-mine");
        var (other, _) = await factory.CreateTenantWithKeyAsync("audit-other");
        using var client = SignedInClient();

        var tenantOnly = await PageAsync(client, $"/ui/audit?tenant={mine.Id}");
        var issuedOnly = await PageAsync(client, $"/ui/audit?tenant={mine.Id}&action={AuditActions.ApiKeyIssued}");

        Assert.Contains(mine.Identifier, tenantOnly, StringComparison.Ordinal);
        Assert.DoesNotContain(other.Identifier, tenantOnly, StringComparison.Ordinal);
        Assert.Contains("簽發 API key", issuedOnly, StringComparison.Ordinal);
        Assert.DoesNotContain("建立租戶", issuedOnly, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnEmptyResultSaysSoAndOffersAWayBack()
    {
        using var client = SignedInClient();

        var html = await PageAsync(client, "/ui/audit?tenant=no-such-tenant");

        Assert.Contains("沒有符合條件的稽核事件", html, StringComparison.Ordinal);
        Assert.Contains("重設篩選", html, StringComparison.Ordinal);
    }

    // 表格的語意與無障礙：真正的 th scope、有標籤的表格、狀態用 aria-live 播報、展開按鈕有 aria-expanded。
    [Fact]
    public async Task TheAuditTableIsSemanticAndLabelled()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-a11y");
        using var client = SignedInClient();

        var html = await PageAsync(client, $"/ui/audit?tenant={tenant.Id}");

        Assert.Contains("<th scope=\"col\">", html, StringComparison.Ordinal);
        Assert.Contains("aria-label=\"稽核事件，新到舊\"", html, StringComparison.Ordinal);
        Assert.Contains("role=\"status\"", html, StringComparison.Ordinal);
        Assert.Contains("aria-expanded=\"false\"", html, StringComparison.Ordinal);
        Assert.Contains("<time datetime=", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheNavigationLinksToBothPages()
    {
        using var client = SignedInClient();

        var html = await PageAsync(client, "/ui");

        Assert.Contains("href=\"/ui/tenants\"", html, StringComparison.Ordinal);
        Assert.Contains("href=\"/ui/audit\"", html, StringComparison.Ordinal);
    }
}