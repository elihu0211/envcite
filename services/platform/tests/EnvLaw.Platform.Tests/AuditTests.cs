using System.Net;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json;
using System.Text.RegularExpressions;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authentication.Google;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed partial class AuditTests(PlatformFactory factory)
{
    private async Task<JsonElement> EventsAsync(string query)
    {
        using var admin = factory.CreateAdminClient();
        using var response = await admin.GetAsync(new Uri($"/admin/audit-events?{query}", UriKind.Relative));
        response.EnsureSuccessStatusCode();
        return (await response.Content.ReadFromJsonAsync<JsonElement>()).Clone();
    }

    private async Task<List<JsonElement>> ItemsAsync(string query) =>
        (await EventsAsync(query)).GetProperty("items").EnumerateArray().ToList();

    [Fact]
    public async Task CreatingATenantIsAuditedWithTheOperatorKeyAsActor()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-t");

        var created = (await ItemsAsync($"tenant={tenant.Id}&action=tenant.created")).Single();

        Assert.Equal("operator-key", created.GetProperty("actorType").GetString());
        Assert.Equal("admin-key", created.GetProperty("actorId").GetString());
        Assert.Equal(tenant.Id, created.GetProperty("tenantId").GetString());
        Assert.Equal(tenant.Identifier, created.GetProperty("detail").GetProperty("identifier").GetString());
    }

    [Fact]
    public async Task IssuingAKeyIsAuditedWithoutTheKeyOrItsHash()
    {
        using var admin = factory.CreateAdminClient();
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("audit-k");

        using var response = await admin.GetAsync(new Uri($"/admin/audit-events?tenant={tenant.Id}&action=api_key.issued", UriKind.Relative));
        var body = await response.Content.ReadAsStringAsync();
        var issued = JsonDocument.Parse(body).RootElement.GetProperty("items").EnumerateArray().Single();

        Assert.Equal(key.ApiKey.Id.ToString(), issued.GetProperty("targetId").GetString());
        Assert.Equal(key.ApiKey.Prefix, issued.GetProperty("detail").GetProperty("prefix").GetString());
        Assert.DoesNotContain(key.Key, body, StringComparison.Ordinal);
        Assert.DoesNotContain("hash", body, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task RevokingAKeyIsAuditedOnceEvenWhenRepeated()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("audit-r");
        using var tenantClient = factory.CreateTenantClient(key.Key);
        var other = await factory.IssueKeyAsync(tenant.Id); // 撤銷的是另一把，這把還要用來呼叫

        using var first = await tenantClient.DeleteAsync(new Uri($"/v2/api-keys/{other.ApiKey.Id}", UriKind.Relative));
        using var again = await tenantClient.DeleteAsync(new Uri($"/v2/api-keys/{other.ApiKey.Id}", UriKind.Relative));

        Assert.Equal(HttpStatusCode.NoContent, first.StatusCode);
        Assert.Equal(HttpStatusCode.NoContent, again.StatusCode);
        var revoked = (await ItemsAsync($"tenant={tenant.Id}&action=api_key.revoked")).Single();
        Assert.Equal("tenant-key", revoked.GetProperty("actorType").GetString());
        Assert.Equal(key.ApiKey.Id.ToString(), revoked.GetProperty("actorId").GetString());
        Assert.Equal(other.ApiKey.Id.ToString(), revoked.GetProperty("targetId").GetString());
    }

    [Fact]
    public async Task ARejectedRequestLeavesNoAuditEvent()
    {
        using var admin = factory.CreateAdminClient();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-n");

        using var bad = await admin.PostAsJsonAsync($"/admin/tenants/{tenant.Id}/api-keys", new { name = "" });

        Assert.Equal(HttpStatusCode.BadRequest, bad.StatusCode);
        Assert.Single(await ItemsAsync($"tenant={tenant.Id}&action=api_key.issued")); // 只有建立租戶時簽的那一把
    }

    // 只能新增由資料庫強制，不是程式自律：連直接下 SQL 也改不動、刪不掉、清不空。
    [Theory]
    [InlineData("UPDATE platform.\"AuditEvents\" SET \"Action\" = 'tampered' WHERE true")]
    [InlineData("DELETE FROM platform.\"AuditEvents\" WHERE true")]
    [InlineData("TRUNCATE platform.\"AuditEvents\"")]
    public async Task TheDatabaseRefusesToChangeOrRemoveAuditEvents(string sql)
    {
        _ = await factory.CreateTenantWithKeyAsync("audit-a"); // 確保至少有一列
        await using var scope = factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();

        var failure = await Assert.ThrowsAnyAsync<Exception>(() => db.Database.ExecuteSqlRawAsync(sql));

        Assert.Contains("append-only", failure.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task EventsPageNewestFirstWithACursor()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-p");
        await factory.IssueKeyAsync(tenant.Id);
        await factory.IssueKeyAsync(tenant.Id); // 該租戶共 4 筆：建立、簽發 ×3

        var first = await EventsAsync($"tenant={tenant.Id}&limit=3");
        var firstIds = first.GetProperty("items").EnumerateArray().Select(i => i.GetProperty("id").GetGuid()).ToList();
        var cursor = first.GetProperty("nextCursor").GetGuid();
        var second = await EventsAsync($"tenant={tenant.Id}&limit=3&before={cursor}");
        var secondIds = second.GetProperty("items").EnumerateArray().Select(i => i.GetProperty("id").GetGuid()).ToList();

        Assert.Equal(3, firstIds.Count);
        Assert.Equal(firstIds.OrderByDescending(i => i), firstIds);
        Assert.Single(secondIds);
        Assert.True(secondIds[0] < firstIds[^1]);
        Assert.Equal(JsonValueKind.Null, second.GetProperty("nextCursor").ValueKind);
    }

    [Fact]
    public async Task OnlyOperatorsCanReadTheAuditLog()
    {
        using var anonymous = factory.CreateClient();
        var (_, key) = await factory.CreateTenantWithKeyAsync("audit-x");
        using var tenant = factory.CreateTenantClient(key.Key);

        using var none = await anonymous.GetAsync(new Uri("/admin/audit-events", UriKind.Relative));
        using var asTenant = await tenant.GetAsync(new Uri("/admin/audit-events", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Unauthorized, none.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, asTenant.StatusCode);
    }

    // ---- 營運者登入 ----------------------------------------------------------------------------

    private async Task<TicketReceivedContext> ReceiveGoogleTicketAsync(string email, bool verified)
    {
        await using var scope = factory.Services.CreateAsyncScope();
        var http = new DefaultHttpContext { RequestServices = scope.ServiceProvider };
        var options = factory.Services.GetRequiredService<IOptionsMonitor<GoogleOptions>>().Get(PlatformAuth.UiGoogleScheme);
        var scheme = new AuthenticationScheme(PlatformAuth.UiGoogleScheme, null, typeof(GoogleHandler));
        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [
                new Claim(ClaimTypes.Email, email),
                new Claim(OperatorAllowlist.EmailVerifiedClaim, verified ? "true" : "false"),
            ],
            "Google"));
        var context = new TicketReceivedContext(http, scheme, options, new AuthenticationTicket(principal, scheme.Name));

        await options.Events.OnTicketReceived(context);
        return context;
    }

    [Fact]
    public async Task AnAllowedOperatorSigningInIsAudited()
    {
        var context = await ReceiveGoogleTicketAsync(PlatformFactory.OperatorEmail, verified: true);

        Assert.False(context.Result?.Handled ?? false);
        var events = await ItemsAsync($"action={OperatorSignInActions.SignedIn}&actor={Uri.EscapeDataString(PlatformFactory.OperatorEmail)}");
        Assert.NotEmpty(events);
        Assert.Equal("operator-user", events[0].GetProperty("actorType").GetString());
    }

    [Fact]
    public async Task ADeniedSignInIsAuditedAsAnonymousWithTheEmailAndNoCookieIsIssued()
    {
        const string stranger = "stranger-audit@example.com";

        var context = await ReceiveGoogleTicketAsync(stranger, verified: true);

        Assert.True(context.Result?.Handled);
        Assert.Equal(UiEndpoints.DeniedPath, context.Response.Headers.Location.ToString());
        var denied = (await ItemsAsync($"action={OperatorSignInActions.SignInDenied}"))
            .Single(e => e.GetProperty("detail").GetProperty("email").GetString() == stranger);
        Assert.Equal("anonymous", denied.GetProperty("actorType").GetString());
    }

    [Fact]
    public async Task SigningOutIsAudited()
    {
        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [
                new Claim(ClaimTypes.Email, PlatformFactory.OperatorEmail),
                new Claim(OperatorAllowlist.EmailVerifiedClaim, "true"),
            ],
            PlatformAuth.UiCookieScheme));
        var cookie = factory.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        var ticket = cookie.TicketDataFormat.Protect(new AuthenticationTicket(principal, PlatformAuth.UiCookieScheme));
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false, HandleCookies = false });

        // 先取一個有效的 antiforgery token（頁面的表單裡有，cookie 在回應的 Set-Cookie）。
        using var page = new HttpRequestMessage(HttpMethod.Get, "/ui");
        page.Headers.Add("Cookie", $"{cookie.Cookie.Name}={ticket}");
        using var pageResponse = await client.SendAsync(page);
        var html = await pageResponse.Content.ReadAsStringAsync();
        var token = AntiforgeryToken().Match(html).Groups[1].Value;
        var antiforgeryCookie = pageResponse.Headers.GetValues("Set-Cookie")
            .Select(c => c.Split(';')[0]).First(c => c.StartsWith(".AspNetCore.Antiforgery", StringComparison.Ordinal));

        using var logout = new HttpRequestMessage(HttpMethod.Post, "/ui/logout")
        {
            Content = new FormUrlEncodedContent(new Dictionary<string, string> { ["__RequestVerificationToken"] = token }),
        };
        logout.Headers.Add("Cookie", $"{cookie.Cookie.Name}={ticket}; {antiforgeryCookie}");
        using var response = await client.SendAsync(logout);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.NotEmpty(await ItemsAsync($"action={OperatorSignInActions.SignedOut}&actor={Uri.EscapeDataString(PlatformFactory.OperatorEmail)}"));
    }

    [GeneratedRegex("name=\"__RequestVerificationToken\"[^>]*value=\"([^\"]+)\"")]
    private static partial Regex AntiforgeryToken();
}

/// <summary>事件鍵與 <c>OperatorSignIn</c> 的常數相同；測試不依賴內部型別，所以在這裡重述一次，改名時測試會提醒。</summary>
internal static class OperatorSignInActions
{
    public const string SignedIn = "operator.signed_in";
    public const string SignInDenied = "operator.sign_in_denied";
    public const string SignedOut = "operator.signed_out";
}