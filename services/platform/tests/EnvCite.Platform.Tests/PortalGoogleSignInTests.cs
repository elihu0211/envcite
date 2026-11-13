using System.Net;
using System.Text;
using System.Text.Json;
using System.Web;

using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Authentication.Google;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 客戶入口的 Google 登入：全站一個用戶端、固定回呼，租戶由受保護的 state 帶著；只對應到已存在而且已啟用的成員，不建立帳號。
/// Google 本身以假的取代（權杖端點與使用者資訊端點），其餘是真的主機、真的資料庫與真的 cookie。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
public sealed class PortalGoogleSignInTests(PlatformFactory factory)
{
    /// <summary>Google 的權杖端點與使用者資訊端點：回的是目前設定的那個人。</summary>
    private sealed class FakeGoogle : HttpMessageHandler
    {
        public string Subject { get; set; } = string.Empty;

        public string Email { get; set; } = string.Empty;

        public bool EmailVerified { get; set; } = true;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri!.AbsolutePath;
            var body = path.EndsWith("/token", StringComparison.Ordinal)
                ? """{"access_token":"fake-token","token_type":"Bearer","expires_in":3600}"""
                : path.EndsWith("/userinfo", StringComparison.Ordinal)
                    ? JsonSerializer.Serialize(new { sub = Subject, email = Email, email_verified = EmailVerified, name = "測試" })
                    : null;
            return Task.FromResult(body is null
                ? new HttpResponseMessage(HttpStatusCode.NotFound)
                : new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") });
        }
    }

    private WebApplicationFactory<Program> Host(FakeGoogle google) => factory.WithWebHostBuilder(b => b
        .UseSetting("Platform:Portal:Google:Enabled", "true")
        .UseSetting("Platform:Portal:Google:ClientId", "test-client.apps.googleusercontent.com")
        .UseSetting("Platform:Portal:Google:ClientSecret", "test-secret")
        .ConfigureServices(s => s.PostConfigure<GoogleOptions>(PlatformAuth.PortalGoogleScheme, o => o.Backchannel = new HttpClient(google))));

    private async Task<List<AuditRecord>> EventsAsync(string tenantId, string action) =>
        [.. (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenantId, Action: action, Limit: 200), CancellationToken.None)).Items];

    private static string? Via(AuditRecord record) =>
        record.Detail.ValueKind == JsonValueKind.Object && record.Detail.TryGetProperty("via", out var via) ? via.GetString() : null;

    private static string? Reason(AuditRecord record) =>
        record.Detail.ValueKind == JsonValueKind.Object && record.Detail.TryGetProperty("reason", out var reason) ? reason.GetString() : null;

    /// <summary>一位已啟用的成員：設過自己的密碼、沒有待改的初始密碼。回傳租戶與 email。</summary>
    private async Task<(TenantResponse Tenant, string Email, string Id)> ActiveMemberAsync(string prefix)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(prefix);
        var (id, email) = await AddActiveAsync(tenant, NewEmail());
        return (tenant, email, id);
    }

    private async Task<(string Id, string Email)> AddActiveAsync(TenantResponse tenant, string email)
    {
        var (id, initial) = await AddMemberAsync(factory, tenant.Id, email);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (id, email);
    }

    /// <summary>
    /// 走完一次 Google 登入（起點、Google、固定回呼、租戶路徑下的第二段），回傳最後的去處。
    /// 起點用登入頁上 Google 表單自己的 antiforgery 與 returnUrl（外殼的語言表單另有一個 returnUrl，是目前這一頁，不是它）。
    /// </summary>
    private static async Task<string?> SignInAsync(PortalBrowser browser, string tenant, string? returnUrl = null)
    {
        var path = returnUrl is null ? Login(tenant) : $"{Login(tenant)}?returnUrl={Uri.EscapeDataString(returnUrl)}";
        var page = await browser.GetAsync(path);
        var action = PortalPaths.GoogleChallenge(tenant);
        var formStart = page.Html.IndexOf($"action=\"{action}\"", StringComparison.Ordinal);
        Assert.True(formStart >= 0, "the login page has no Google form");
        var googleForm = page.Html[formStart..page.Html.IndexOf("</form>", formStart, StringComparison.Ordinal)];
        Assert.Equal(returnUrl is not null, googleForm.Contains($"name=\"returnUrl\" value=\"{returnUrl}\"", StringComparison.Ordinal));

        var fields = new Dictionary<string, string> { ["__RequestVerificationToken"] = PortalBrowser.HiddenFields(googleForm)["__RequestVerificationToken"] };
        if (returnUrl is not null)
            fields["returnUrl"] = returnUrl;

        var start = await browser.PostAsync(action, fields);
        Assert.Equal(HttpStatusCode.Redirect, start.Status);

        var query = HttpUtility.ParseQueryString(new Uri("https://accounts.google.com" + start.Location).Query);
        Assert.Equal("select_account", query["prompt"]);
        Assert.False(string.IsNullOrEmpty(query["code_challenge"])); // PKCE
        Assert.Equal($"https://localhost{PortalGoogle.CallbackPath}", query["redirect_uri"]);

        var back = await browser.GetAsync($"{PortalGoogle.CallbackPath}?code=fake-code&state={Uri.EscapeDataString(query["state"]!)}");
        Assert.Equal(HttpStatusCode.Redirect, back.Status);
        return (await browser.GetAsync(back.Location!)).Location;
    }

    private static string Failed(string tenant) => $"{Login(tenant)}?google=failed";

    [Fact]
    public async Task AnActiveMemberSignsInAndIsLinkedByTheGoogleSubjectNotTheEmail()
    {
        var (tenant, email, _) = await ActiveMemberAsync("gg-a");
        var google = new FakeGoogle { Subject = "sub-a", Email = email };
        using var host = Host(google);

        using var first = new PortalBrowser(host);
        Assert.Equal(Home(tenant.Identifier), await SignInAsync(first, tenant.Identifier));
        Assert.Equal(HttpStatusCode.OK, (await first.GetAsync(Home(tenant.Identifier))).Status);
        Assert.Single(await EventsAsync(tenant.Id, AuditActions.PortalGoogleLinked));
        Assert.Contains(await EventsAsync(tenant.Id, AuditActions.PortalSignedIn), e => Via(e) == "google");

        // 之後認的是 sub：Google 那邊的 email 變了（或被重新分配給別人）也還是同一位成員，不再連結第二次。
        google.Email = "renamed@example.com";
        using var second = new PortalBrowser(host);
        Assert.Equal(Home(tenant.Identifier), await SignInAsync(second, tenant.Identifier));
        Assert.Single(await EventsAsync(tenant.Id, AuditActions.PortalGoogleLinked));
    }

    // 外部登入的鍵是（提供者、提供者給的 Id）：不含租戶就會在第二個租戶連結同一個 Google 帳號時撞鍵。
    [Fact]
    public async Task TheSameGoogleAccountCanBeAMemberOfTwoTenants()
    {
        var (tenantA, email, _) = await ActiveMemberAsync("gg-b1");
        var (tenantB, _) = await factory.CreateTenantWithKeyAsync("gg-b2");
        await AddActiveAsync(tenantB, email);
        using var host = Host(new FakeGoogle { Subject = "sub-shared", Email = email });

        using var browser = new PortalBrowser(host);
        Assert.Equal(Home(tenantA.Identifier), await SignInAsync(browser, tenantA.Identifier));
        Assert.Equal(Home(tenantB.Identifier), await SignInAsync(browser, tenantB.Identifier));
        Assert.Single(await EventsAsync(tenantA.Id, AuditActions.PortalGoogleLinked));
        Assert.Single(await EventsAsync(tenantB.Id, AuditActions.PortalGoogleLinked));
    }

    // 沒有人能靠 Google 變成成員：失敗的結果都一樣（回登入頁、沒有 session），稽核只留原因，不留 Google 給的 email。
    [Fact]
    public async Task NoOneBecomesAMemberThroughGoogle()
    {
        var google = new FakeGoogle { Subject = "sub-denied" };
        using var host = Host(google);
        var (tenant, activeEmail, _) = await ActiveMemberAsync("gg-c");

        // 還在邀請中：帳號有了，但還沒設過自己的密碼（這裡是初始密碼還沒改）。先走信裡的連結啟用，才能用 Google。
        var pendingEmail = NewEmail();
        await AddMemberAsync(factory, tenant.Id, pendingEmail);

        var (disabledId, disabledEmail) = await AddActiveAsync(tenant, NewEmail());
        var disabled = await factory.Services.GetRequiredService<IMemberAdmin>()
            .SetDisabledAsync(OperatorSession.Principal(), tenant.Id, disabledId, true, CancellationToken.None);
        Assert.True(disabled.IsOk, disabled.Message);

        var cases = new (string Email, bool Verified, string Reason)[]
        {
            (activeEmail, false, "google_email_unverified"), // Google 沒有說這個 email 已驗證
            ("nobody@example.com", true, "google_no_member"), // 沒有這位成員：Google 不建立帳號
            (pendingEmail, true, "google_no_member"),
            (disabledEmail, true, "disabled"),
        };
        foreach (var (email, verified, _) in cases)
        {
            (google.Email, google.EmailVerified) = (email, verified);
            using var browser = new PortalBrowser(host);
            Assert.Equal(Failed(tenant.Identifier), await SignInAsync(browser, tenant.Identifier));
            Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(tenant.Identifier))).Status); // 沒有 session
        }

        var failures = (await EventsAsync(tenant.Id, AuditActions.PortalSignInFailed)).Where(e => Via(e) == "google").ToList();
        Assert.Equal(cases.Select(c => c.Reason).Order(), failures.Select(Reason).Order());
        Assert.DoesNotContain(failures, e => e.Detail.GetRawText().Contains('@'));
        Assert.Empty(await EventsAsync(tenant.Id, AuditActions.PortalGoogleLinked)); // 一個都沒連結
    }

    [Fact]
    public async Task AFlowStartedForOneTenantCannotBeFinishedInAnother()
    {
        var (tenantA, _, _) = await ActiveMemberAsync("gg-d1");
        var (tenantB, emailB, _) = await ActiveMemberAsync("gg-d2");
        using var host = Host(new FakeGoogle { Subject = "sub-moved", Email = emailB });

        // 在 A 開始、Google 回來之後，把第二段的網址改成 B 的：state 裡的租戶不符，不能因此登入 B 的成員。
        using var browser = new PortalBrowser(host);
        var page = await browser.GetAsync(Login(tenantA.Identifier));
        var token = PortalBrowser.HiddenFields(page.Html)["__RequestVerificationToken"];
        var start = await browser.PostAsync(PortalPaths.GoogleChallenge(tenantA.Identifier), new() { ["__RequestVerificationToken"] = token });
        var state = HttpUtility.ParseQueryString(new Uri("https://accounts.google.com" + start.Location).Query)["state"]!;
        var back = await browser.GetAsync($"{PortalGoogle.CallbackPath}?code=fake-code&state={Uri.EscapeDataString(state)}");
        Assert.Equal(PortalPaths.GoogleCallback(tenantA.Identifier), back.Location);

        var moved = await browser.GetAsync(PortalPaths.GoogleCallback(tenantB.Identifier));

        Assert.Equal(Failed(tenantB.Identifier), moved.Location);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(tenantB.Identifier))).Status);
    }

    [Fact]
    public async Task TheReturnUrlIsHonouredInsideThePortalAndNeverOutsideIt()
    {
        var (tenant, email, _) = await ActiveMemberAsync("gg-e");
        using var host = Host(new FakeGoogle { Subject = "sub-return", Email = email });

        using var inside = new PortalBrowser(host);
        Assert.Equal(AccountPage(tenant.Identifier), await SignInAsync(inside, tenant.Identifier, AccountPage(tenant.Identifier)));

        foreach (var outside in new[] { "https://evil.example/steal", "//evil.example/steal", "/ui" })
        {
            using var browser = new PortalBrowser(host);
            Assert.Equal(Home(tenant.Identifier), await SignInAsync(browser, tenant.Identifier, outside));
        }
    }

    [Fact]
    public async Task WhenOffThereIsNoButtonAndNoDoorAndTheFixedCallbackIsNotAnError()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("gg-f");
        using var browser = new PortalBrowser(factory);

        var login = await browser.GetAsync(Login(tenant.Identifier));
        Assert.DoesNotContain("/login/google", login.Html, StringComparison.Ordinal);
        // 帶著有效的 antiforgery 也一樣：關著就沒有這個路徑，不是被當成壞請求。
        var token = PortalBrowser.HiddenFields(login.Html)["__RequestVerificationToken"];
        Assert.Equal(HttpStatusCode.NotFound, (await browser.PostAsync(PortalPaths.GoogleChallenge(tenant.Identifier), new() { ["__RequestVerificationToken"] = token })).Status);
        Assert.Equal(HttpStatusCode.NotFound, await browser.StatusAsync(PortalPaths.GoogleCallback(tenant.Identifier)));
        Assert.Equal(HttpStatusCode.NotFound, await browser.StatusAsync($"{PortalGoogle.CallbackPath}?code=x&state=y"));
    }

    [Fact]
    public async Task WhenOnTheLoginPageOffersTheButtonAsAPostFormAndFailureSaysOnlyThat()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("gg-g");
        using var host = Host(new FakeGoogle());
        using var browser = new PortalBrowser(host);

        var login = await browser.GetAsync(Login(tenant.Identifier));
        Assert.Contains($"""action="{PortalPaths.GoogleChallenge(tenant.Identifier)}" """.TrimEnd(), login.Html, StringComparison.Ordinal);
        Assert.Contains("method=\"post\"", login.Html, StringComparison.Ordinal);

        // 起點只收 POST，且要有 antiforgery：連結或別的網站送來的請求不能把人送去 Google。
        Assert.NotEqual(HttpStatusCode.Redirect, await browser.StatusAsync(PortalPaths.GoogleChallenge(tenant.Identifier)));
        Assert.Equal(HttpStatusCode.BadRequest, (await browser.PostAsync(PortalPaths.GoogleChallenge(tenant.Identifier), [])).Status);

        var failed = await browser.GetAsync(Failed(tenant.Identifier));
        Assert.Contains("無法用這個 Google 帳號登入", failed.Html, StringComparison.Ordinal);
    }
}