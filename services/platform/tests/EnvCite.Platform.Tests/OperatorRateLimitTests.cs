using System.Net;

using Microsoft.AspNetCore.Mvc.Testing;

using static EnvCite.Platform.Tests.OperatorTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 後台登入的速率限制，與客戶入口同一套做法（<c>PortalRateLimit</c>）：依來源位址，只限制 POST，成功與失敗都算，
/// 被限制時告訴對方多久後再試。額度是自己的一份（<c>Platform:Ui:SignInAttemptsPerMinute</c>），不與入口共用。
/// 測試主機的來源位址只有一個，所以這裡證明額度與時機，不證明「不同來源各有各的額度」。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class OperatorRateLimitTests(PlatformFactory factory)
{
    private const int Limit = 3;

    private WebApplicationFactory<Program> Limited() =>
        factory.WithWebHostBuilder(b => b.UseSetting("Platform:Ui:SignInAttemptsPerMinute", Limit.ToString(System.Globalization.CultureInfo.InvariantCulture)));

    [Fact]
    public async Task OnlyTheSubmissionsAreLimitedAndTheSourceIsToldHowLongToWait()
    {
        var (_, email, _) = await NewOperatorAsync(factory);
        await using var host = Limited();
        using var browser = new PortalBrowser(host);

        for (var i = 0; i < Limit; i++)
        {
            var attempt = await browser.SubmitAsync(UiLogin, Credentials(email, $"wrong-guess-{i}-number"));
            Assert.Equal(HttpStatusCode.OK, attempt.Status); // 額度內：正常回「不正確」
        }

        var blocked = await browser.SubmitAsync(UiLogin, Credentials(email, "one-guess-too-many"));
        Assert.Equal(HttpStatusCode.TooManyRequests, blocked.Status);
        Assert.Contains("嘗試次數太多", blocked.Html, StringComparison.Ordinal);

        // 只擋提交：頁面照樣打得開（不讓人以為網站壞了）。
        Assert.Equal(HttpStatusCode.OK, await browser.StatusAsync(UiLogin));
    }

    [Fact]
    public async Task ARejectionCarriesRetryAfter()
    {
        var (_, email, _) = await NewOperatorAsync(factory);
        await using var host = Limited();
        using var client = host.CreateClient(new WebApplicationFactoryClientOptions
        {
            BaseAddress = new Uri("https://localhost"),
            AllowAutoRedirect = false,
        });

        // 直接送（不先 GET 取 token）：被限流發生在 antiforgery 驗證與表單處理之前，所以不需要有效的表單。
        HttpResponseMessage? last = null;
        for (var i = 0; i <= Limit; i++)
        {
            last?.Dispose();
            last = await client.PostAsync(new Uri(UiLogin, UriKind.Relative), new FormUrlEncodedContent(new Dictionary<string, string> { ["Input.Email"] = email, ["Input.Password"] = "x" }), TestContext.Current.CancellationToken);
        }

        Assert.Equal(HttpStatusCode.TooManyRequests, last!.StatusCode);
        var header = last.Headers.TryGetValues("Retry-After", out var values) ? string.Join(",", values) : "(none)";
        Assert.True(int.TryParse(header, out var seconds) && seconds > 0, $"Retry-After was: {header}");
        last.Dispose();
    }

    // 第二步是匿名頁（只有暫存憑證），其餘的帳號頁要有 session——授權在限流之前，沒有 session 的請求先被導去登入。
    [Fact]
    public async Task TheSecondStepAndTheAccountPagesAreLimitedToo()
    {
        await using var host = Limited();
        using var anonymous = host.CreateClient(new WebApplicationFactoryClientOptions
        {
            BaseAddress = new Uri("https://localhost"),
            AllowAutoRedirect = false,
        });
        using var session = host.SignedInClient();
        var form = new Dictionary<string, string> { ["Input.Code"] = "000000" };

        foreach (var (client, path) in new[] { (anonymous, UiMfa), (session, UiPassword), (session, UiSecurity) })
        {
            HttpStatusCode last = default;
            for (var i = 0; i <= Limit; i++)
            {
                using var response = await client.PostAsync(new Uri(path, UriKind.Relative), new FormUrlEncodedContent(form), TestContext.Current.CancellationToken);
                last = response.StatusCode;
            }

            Assert.Equal(HttpStatusCode.TooManyRequests, last);
        }
    }

    // 兩個面各有各的額度：把後台的額度用完，客戶入口還能登入（反過來也一樣）。
    [Fact]
    public async Task TheConsoleAndThePortalDoNotShareAnAllowance()
    {
        var (tenant, member, _) = await MemberAsync(factory, "rl-ui");
        await using var host = Limited();
        using var browser = new PortalBrowser(host);
        for (var i = 0; i < Limit; i++)
            _ = await browser.SubmitAsync(UiLogin, Credentials(NewOperatorEmail(), $"wrong-guess-{i}-number"));

        var consoleBlocked = await browser.SubmitAsync(UiLogin, Credentials(NewOperatorEmail(), "one-guess-too-many"));
        var portal = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(member, "wrong-guess-number-one"));

        Assert.Equal(HttpStatusCode.TooManyRequests, consoleBlocked.Status);
        Assert.Equal(HttpStatusCode.OK, portal.Status);
    }
}