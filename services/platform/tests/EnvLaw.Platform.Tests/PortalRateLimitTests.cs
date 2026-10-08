using System.Net;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;

using static EnvLaw.Platform.Tests.PortalTestSupport;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// 登入的速率限制：帳號鎖定擋不了「一個來源試很多帳號」，這一道才是。依來源位址，只限制 POST，成功與失敗都算。
/// 測試主機的來源位址只有一個，所以這裡證明額度與時機，不證明「不同來源各有各的額度」。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class PortalRateLimitTests(PlatformFactory factory)
{
    private const int Limit = 3;

    private WebApplicationFactory<Program> Limited() =>
        factory.WithWebHostBuilder(b => b.UseSetting("Platform:Portal:SignInAttemptsPerMinute", Limit.ToString(System.Globalization.CultureInfo.InvariantCulture)));

    [Fact]
    public async Task OnlyTheSubmissionsAreLimitedAndTheSourceIsToldHowLongToWait()
    {
        var (tenant, email, _) = await MemberAsync(factory, "rl-a");
        await using var host = Limited();
        using var browser = new PortalBrowser(host);

        for (var i = 0; i < Limit; i++)
        {
            var attempt = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, $"wrong-guess-{i}-number"));
            Assert.Equal(HttpStatusCode.OK, attempt.Status); // 額度內：正常回「不正確」
        }

        var blocked = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, "one-guess-too-many"));
        Assert.Equal(HttpStatusCode.TooManyRequests, blocked.Status);
        Assert.Contains("嘗試次數太多", blocked.Html, StringComparison.Ordinal);

        // 只擋提交：頁面照樣打得開（不讓人以為網站壞了），別的頁面不受影響。
        Assert.Equal(HttpStatusCode.OK, await browser.StatusAsync(Login(tenant.Identifier)));
    }

    [Fact]
    public async Task ARejectionCarriesRetryAfter()
    {
        var (tenant, email, _) = await MemberAsync(factory, "rl-b");
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
            last = await client.PostAsync(
                new Uri(Login(tenant.Identifier), UriKind.Relative),
                new FormUrlEncodedContent(new Dictionary<string, string> { ["Input.Email"] = email, ["Input.Password"] = "x" }));
        }

        Assert.Equal(HttpStatusCode.TooManyRequests, last!.StatusCode);
        var header = last.Headers.TryGetValues("Retry-After", out var values) ? string.Join(",", values) : "(none)";
        Assert.True(int.TryParse(header, out var seconds) && seconds > 0, $"Retry-After was: {header}");
        last.Dispose();
    }

    [Fact]
    public async Task SuccessfulSignInsCountToo()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "rl-c");
        await using var host = Limited();
        using var browser = new PortalBrowser(host);

        // 登入 + 改密碼各算一次；之後每次提交都算。
        _ = await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        _ = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, NewPassword));
        var blocked = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, NewPassword));

        Assert.Equal(HttpStatusCode.TooManyRequests, blocked.Status);
    }

    [Fact]
    public async Task TheLimitDoesNotTouchTheApi()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("rl-d");
        await using var host = Limited();
        using var tenantClient = host.CreateClient();
        tenantClient.DefaultRequestHeaders.Authorization = new("Bearer", key.Key);

        for (var i = 0; i < Limit + 2; i++)
        {
            using var response = await tenantClient.GetAsync(new Uri("/v2/me", UriKind.Relative));
            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        }
    }

    // 有人拿到一個 session（被偷的 cookie）就能無限次猜「目前的密碼」：改密碼頁也要限。
    [Fact]
    public async Task ChangingThePasswordIsLimitedToo()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "rl-e");
        await using var host = Limited();
        using var browser = new PortalBrowser(host);
        _ = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial)); // 第 1 次提交

        var statuses = new List<HttpStatusCode>();
        for (var i = 0; i < Limit; i++)
        {
            statuses.Add((await browser.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange($"guess-{i}-of-current", NewPassword))).Status);
        }

        Assert.Equal(HttpStatusCode.TooManyRequests, statuses[^1]); // 登入占了 1 次，所以第 3 次改密碼就超額
        Assert.DoesNotContain(HttpStatusCode.TooManyRequests, statuses[..^1]);
    }

    // 不過分限流：機關與顧問公司常整個辦公室共用一個出口位址，早上同時上班登入的人不該被擋。
    // 限流擋的是自動化的猜測，不是正常使用。預設值是這個決定的記錄，要降低它得有理由。
    [Fact]
    public void TheDefaultAllowanceIsGenerousEnoughForAnOfficeBehindOneAddress() =>
        Assert.True(new Web.Auth.PortalOptions().SignInAttemptsPerMinute >= 60);

    [Fact]
    public async Task AnOfficeSigningInAtNineIsNotBlocked()
    {
        var (tenant, email, _) = await MemberAsync(factory, "rl-f");
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Portal:SignInAttemptsPerMinute", "60"));
        using var browser = new PortalBrowser(host);

        // 50 次提交（多數人輸入錯誤一兩次是常態）都在額度內；全部正常回應，沒有 429。
        for (var i = 0; i < 50; i++)
        {
            var attempt = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, $"honest-typo-{i}-abc"));
            Assert.NotEqual(HttpStatusCode.TooManyRequests, attempt.Status);
        }
    }
}