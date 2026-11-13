using System.Net;

using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 後台 session 的絕對期限：cookie 會滑動續期，但從簽發起算超過 <c>Platform:Ui:SessionMaxAge</c> 一律拒絕，
/// 被竊的 cookie 不能靠持續使用而永不過期。期限的起點存在票的屬性裡（滑動續期會重寫 <c>IssuedUtc</c>，所以不能用它）。
/// </summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class OperatorSessionLifetimeTests(PlatformFactory factory)
{
    private static readonly TimeSpan MaxAge = TimeSpan.FromHours(12); // 與 UiOptions 預設一致

    private async Task<HttpResponseMessage> GetUiAsync(TimeSpan? age, bool withStart = true)
    {
        var user = await factory.EnsureOperatorAsync(OperatorTestSupport.NewOperatorEmail());
        var properties = withStart ? factory.SessionProperties(age) : new AuthenticationProperties();
        using var client = factory.ClientFor(factory.SessionOf(user), properties);
        return await client.GetAsync(new Uri("/ui", UriKind.Relative), TestContext.Current.CancellationToken);
    }

    [Fact]
    public async Task ASessionYoungerThanTheLimitIsLetIn()
    {
        using var response = await GetUiAsync(MaxAge - TimeSpan.FromMinutes(1));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task ASessionOlderThanTheLimitIsRejectedAndTheCookieCleared()
    {
        using var response = await GetUiAsync(MaxAge + TimeSpan.FromMinutes(1));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Contains(UiEndpoints.LoginPath, response.Headers.Location!.OriginalString, StringComparison.Ordinal);
        Assert.Contains(response.Headers.GetValues("Set-Cookie"), c => c.StartsWith("EnvCite.Ui=;", StringComparison.Ordinal));
    }

    // 沒有起點就無從計算年齡：拒絕，不是放行。
    [Fact]
    public async Task ASessionWithoutAStartIsRejected()
    {
        using var response = await GetUiAsync(null, withStart: false);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
    }

    // 滑動續期換發新票（IssuedUtc 變新），絕對期限的起點必須原封不動。
    [Fact]
    public async Task SlidingRenewalDoesNotMoveTheStartOfTheAbsoluteLimit()
    {
        var user = await factory.EnsureOperatorAsync(OperatorTestSupport.NewOperatorEmail());
        var properties = factory.SessionProperties(TimeSpan.FromHours(5));
        var started = properties.Items[OperatorSessionLimit.StartedAtKey];
        // 剩餘不到一半的滑動視窗（8 小時）→ 這個請求會換發。cookie 處理器與主機共用同一個 TimeProvider。
        var now = factory.Time.GetUtcNow();
        properties.IssuedUtc = now - TimeSpan.FromHours(5);
        properties.ExpiresUtc = now + TimeSpan.FromHours(3);
        using var client = factory.ClientFor(factory.SessionOf(user), properties);

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var renewed = Assert.Single(response.Headers.GetValues("Set-Cookie"), c => c.StartsWith("EnvCite.Ui=", StringComparison.Ordinal));
        var value = renewed["EnvCite.Ui=".Length..renewed.IndexOf(';', StringComparison.Ordinal)];
        var options = factory.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        var ticket = options.TicketDataFormat.Unprotect(Uri.UnescapeDataString(value))!;

        Assert.True(ticket.Properties.IssuedUtc > now - TimeSpan.FromMinutes(1)); // 真的換發了
        Assert.Equal(started, ticket.Properties.Items[OperatorSessionLimit.StartedAtKey]);
    }
}