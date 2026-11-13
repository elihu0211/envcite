using System.Net;
using System.Text.Json;

using EnvCite.Platform.Modules.Members;

using Microsoft.AspNetCore.Hosting;

namespace EnvCite.Platform.Tests;

/// <summary>客戶入口帳密登入的防線：不洩漏、會鎖定、session 綁租戶、兩種 cookie 不互通、稽核不含密碼。</summary>
[Collection(PlatformTestGroup.Group4)]
public sealed class PortalSecurityTests(PlatformFactory factory)
{
    private const string GenericError = "電子郵件或密碼不正確，或登入已被暫時停用";

    private async Task<List<AuditRecord>> EventsAsync(string tenantId, string action) =>
        [.. (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenantId, Action: action), CancellationToken.None)).Items];

    [Fact]
    public async Task AnUnknownEmailAndAWrongPasswordLookTheSame()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-a");
        using var browser = new PortalBrowser(factory);

        var unknown = await browser.SubmitAsync(Login(tenant.Identifier), Credentials("nobody@example.com", initial));
        var wrong = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, "not-the-password"));

        foreach (var response in new[] { unknown, wrong })
        {
            Assert.Equal(HttpStatusCode.OK, response.Status); // 表單重新出現，沒有轉址
            Assert.Contains(GenericError, response.Html, StringComparison.Ordinal);
        }

        // 同一句話：說得出差別，就是在告訴攻擊者哪些 email 有帳號。
        Assert.Equal(ErrorLine(unknown.Html), ErrorLine(wrong.Html));
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(tenant.Identifier))).Status); // 都沒有登入
    }

    // 重新渲染的表單會帶著輸入框的值：密碼不能因此又被傳回瀏覽器。
    [Fact]
    public async Task APasswordIsNeverEchoedBackIntoAForm()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-echo");
        using var browser = new PortalBrowser(factory);
        const string typed = "typed-password-that-must-not-come-back";

        var failedLogin = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, typed));
        Assert.Contains(GenericError, failedLogin.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(typed, failedLogin.Html, StringComparison.Ordinal);
        Assert.Contains(email, failedLogin.Html, StringComparison.Ordinal); // 帳號留著，少打一次

        _ = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        var failedChange = await browser.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange(initial, typed, "mismatch-confirm-value-here"));
        Assert.Contains("兩次輸入的新密碼不一樣", failedChange.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(typed, failedChange.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("mismatch-confirm-value-here", failedChange.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(initial, failedChange.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task FiveWrongPasswordsLockTheAccountEvenAgainstTheRightOneAndAResetUnlocksIt()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-b");
        using var attacker = new PortalBrowser(factory);

        for (var i = 0; i < MembersModule.MaxFailedAccessAttempts; i++)
            _ = await attacker.SubmitAsync(Login(tenant.Identifier), Credentials(email, $"guess-number-{i}-wrong"));

        // 鎖定了：連對的密碼也進不去，而且訊息與一般失敗一樣（不告訴對方帳號存在、也不告訴他被鎖了）。
        var locked = await attacker.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        Assert.Equal(HttpStatusCode.OK, locked.Status);
        Assert.Contains(GenericError, locked.Html, StringComparison.Ordinal);

        var reasons = (await EventsAsync(tenant.Id, AuditActions.PortalSignInFailed))
            .Select(e => e.Detail.GetProperty("reason").GetString()).ToList();
        Assert.Contains("locked_out", reasons);

        // 營運者重設：解除鎖定、給新的一次性密碼。
        var reset = await factory.Services.GetRequiredService<IMemberAdmin>().ResetPasswordAsync(
            OperatorSession.Principal(), tenant.Id, (await factory.Services.GetRequiredService<IMemberAdmin>().ListAsync(tenant.Id, CancellationToken.None)).Value!.Single().Id, CancellationToken.None);
        Assert.True(reset.IsOk);

        using var member = new PortalBrowser(factory);
        var unlocked = await member.SubmitAsync(Login(tenant.Identifier), Credentials(email, reset.Value!.Password));
        Assert.Equal(PasswordPage(tenant.Identifier), unlocked.Location); // 重設後一樣要先改密碼
    }

    [Fact]
    public async Task ASessionIsOnlyValidForTheTenantItWasIssuedFor()
    {
        var (tenantA, emailA, initialA) = await MemberAsync(factory, "sec-c1");
        var (tenantB, _) = await factory.CreateTenantWithKeyAsync("sec-c2");
        // 同一個 email 在兩個租戶是兩個互不相干的帳號。
        var (_, initialB) = await AddMemberAsync(factory, tenantB.Id, emailA);

        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenantA.Identifier, emailA, initialA);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(Home(tenantA.Identifier))).Status);

        // 拿 A 租戶的 cookie 打 B 租戶的路徑：當作沒登入。
        var crossTenant = await browser.GetAsync(Home(tenantB.Identifier));
        Assert.Equal(HttpStatusCode.Redirect, crossTenant.Status);
        Assert.StartsWith(Login(tenantB.Identifier), crossTenant.Location, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(PasswordPage(tenantB.Identifier))).Status);

        // A 的密碼登不進 B（雖然 email 一樣）。
        using var other = new PortalBrowser(factory);
        var wrongTenant = await other.SubmitAsync(Login(tenantB.Identifier), Credentials(emailA, NewPassword));
        Assert.Contains(GenericError, wrongTenant.Html, StringComparison.Ordinal);
        var right = await other.SubmitAsync(Login(tenantB.Identifier), Credentials(emailA, initialB));
        Assert.Equal(PasswordPage(tenantB.Identifier), right.Location);
    }

    [Fact]
    public async Task ThePortalCookieOpensNothingButThePortalAndTheOperatorCookieOpensNoPortalPage()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-d");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        Assert.Equal(HttpStatusCode.Unauthorized, await browser.StatusAsync("/v2/me"));
        var ui = await browser.GetAsync("/ui");
        Assert.Equal(HttpStatusCode.Redirect, ui.Status);
        Assert.StartsWith("/ui/login", ui.Location, StringComparison.Ordinal);

        using var operatorClient = factory.SignedInClient();
        using var response = await operatorClient.GetAsync(new Uri(Home(tenant.Identifier), UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.StartsWith(Login(tenant.Identifier), response.Headers.Location?.OriginalString, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ChangingThePasswordSignsOutTheOtherDevicesButNotTheOneThatChangedIt()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-e");

        using var phone = new PortalBrowser(factory);
        await SignInFullyAsync(phone, tenant.Identifier, email, initial);
        using var laptop = new PortalBrowser(factory);
        var laptopLogin = await laptop.SubmitAsync(Login(tenant.Identifier), Credentials(email, NewPassword));
        Assert.Equal(Home(tenant.Identifier), laptopLogin.Location);
        Assert.Equal(HttpStatusCode.OK, (await laptop.GetAsync(Home(tenant.Identifier))).Status);

        var changed = await phone.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange(NewPassword, "another long passphrase here"));
        Assert.Equal(Home(tenant.Identifier), changed.Location);

        Assert.Equal(HttpStatusCode.OK, (await phone.GetAsync(Home(tenant.Identifier))).Status);
        Assert.Equal(HttpStatusCode.Redirect, (await laptop.GetAsync(Home(tenant.Identifier))).Status);
    }

    [Fact]
    public async Task ASignedOutSessionCannotBeUsedAgain()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-f");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var loggedOut = await browser.SubmitAsync(Home(tenant.Identifier), [], $"{Home(tenant.Identifier)}/logout");

        Assert.Equal(Login(tenant.Identifier), loggedOut.Location);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(tenant.Identifier))).Status);
        Assert.Single(await EventsAsync(tenant.Id, AuditActions.PortalSignedOut));
    }

    [Theory]
    [InlineData("https://evil.example/steal")]
    [InlineData("//evil.example/steal")]
    [InlineData("/portal/someone-else/password")]
    [InlineData("/ui")]
    public async Task TheReturnUrlCannotLeaveThisTenantsPortal(string returnUrl)
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-g");
        using var setup = new PortalBrowser(factory);
        var password = await SignInFullyAsync(setup, tenant.Identifier, email, initial);

        using var browser = new PortalBrowser(factory);
        var path = $"{Login(tenant.Identifier)}?returnUrl={Uri.EscapeDataString(returnUrl)}";
        var signedIn = await browser.SubmitAsync(path, Credentials(email, password));

        Assert.Equal(Home(tenant.Identifier), signedIn.Location);
    }

    [Fact]
    public async Task ARealReturnUrlInsideThePortalIsHonoured()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-h");
        using var setup = new PortalBrowser(factory);
        var password = await SignInFullyAsync(setup, tenant.Identifier, email, initial);

        using var browser = new PortalBrowser(factory);
        var path = $"{Login(tenant.Identifier)}?returnUrl={Uri.EscapeDataString(PasswordPage(tenant.Identifier))}";
        var signedIn = await browser.SubmitAsync(path, Credentials(email, password));

        Assert.Equal(PasswordPage(tenant.Identifier), signedIn.Location);
    }

    [Fact]
    public async Task AnUnknownTenantIsA404AndNeverAnInternalError()
    {
        using var browser = new PortalBrowser(factory);

        Assert.Equal(HttpStatusCode.NotFound, await browser.StatusAsync(Login("no-such-tenant")));
        var home = await browser.GetAsync(Home("no-such-tenant"));
        Assert.True(home.Status is HttpStatusCode.NotFound or HttpStatusCode.Redirect, home.Status.ToString());
    }

    [Fact]
    public async Task AViewerSeesTheirAccountButNoApiKeysAndADeveloperSeesOnlyPrefixes()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("sec-i");
        var (viewerEmail, developerEmail) = (NewEmail(), NewEmail());
        var (_, viewerInitial) = await AddMemberAsync(factory, tenant.Id, viewerEmail, CustomerRoles.Viewer);
        var (_, developerInitial) = await AddMemberAsync(factory, tenant.Id, developerEmail, CustomerRoles.Developer);

        using var viewer = new PortalBrowser(factory);
        await SignInFullyAsync(viewer, tenant.Identifier, viewerEmail, viewerInitial);
        var viewerHome = await viewer.GetAsync(AccountPage(tenant.Identifier));
        Assert.Contains("唯讀", viewerHome.Html, StringComparison.Ordinal);
        Assert.Contains("不能查看 API key", viewerHome.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(key.ApiKey.Prefix, viewerHome.Html, StringComparison.Ordinal);

        using var developer = new PortalBrowser(factory);
        await SignInFullyAsync(developer, tenant.Identifier, developerEmail, developerInitial);
        var developerHome = await developer.GetAsync(AccountPage(tenant.Identifier));
        Assert.Contains(key.ApiKey.Prefix, developerHome.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(key.Key, developerHome.Html, StringComparison.Ordinal); // 完整的 key 只在簽發當下出現
    }

    [Fact]
    public async Task ThePortalIsOffOutsideDevelopmentUnlessSwitchedOn()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("sec-j");

        await using var off = factory.WithWebHostBuilder(b => b.UseEnvironment("Production"));
        using var offBrowser = new PortalBrowser(off);
        Assert.Equal(HttpStatusCode.NotFound, await offBrowser.StatusAsync(Login(tenant.Identifier)));

        await using var on = factory.WithWebHostBuilder(b => b.UseEnvironment("Production").UseSetting("Platform:Portal:Enabled", "true"));
        using var onBrowser = new PortalBrowser(on);
        Assert.Equal(HttpStatusCode.OK, await onBrowser.StatusAsync(Login(tenant.Identifier)));
    }

    // 稽核表只能附加、刪不掉：人常把密碼貼到帳號欄，所以失敗的登入絕不記使用者打的字。
    [Fact]
    public async Task AuditNamesTheMemberButNeverRecordsWhatWasTyped()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sec-k");
        using var browser = new PortalBrowser(factory);
        const string typedIntoTheEmailField = "my-secret-password-pasted-in-the-wrong-box";
        _ = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(typedIntoTheEmailField, typedIntoTheEmailField));
        _ = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, "wrong-password-typed-here"));
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var all = new List<AuditRecord>();
        foreach (var action in new[] { AuditActions.PortalSignInFailed, AuditActions.PortalSignedIn, AuditActions.PortalPasswordChanged, AuditActions.MemberCreated })
            all.AddRange(await EventsAsync(tenant.Id, action));

        var dump = JsonSerializer.Serialize(all);
        Assert.DoesNotContain(typedIntoTheEmailField, dump, StringComparison.Ordinal);
        Assert.DoesNotContain("wrong-password-typed-here", dump, StringComparison.Ordinal);
        Assert.DoesNotContain(initial, dump, StringComparison.Ordinal);
        Assert.DoesNotContain(NewPassword, dump, StringComparison.Ordinal);

        var signedIn = all.First(e => e.Action == AuditActions.PortalSignedIn);
        Assert.Equal(AuditActors.TenantUser, signedIn.ActorType); // 操作者是租戶成員（記 Id），不是匿名
        Assert.NotNull(signedIn.ActorId);
        Assert.Equal(tenant.Id, signedIn.TenantId);
    }

    private static string ErrorLine(string html)
    {
        var start = html.IndexOf("ep-alert-error", StringComparison.Ordinal);
        var end = html.IndexOf("</p>", start, StringComparison.Ordinal);
        return html[start..end];
    }
}