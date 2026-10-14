using System.Net;

using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.SharedKernel;


using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 客戶入口的帳密登入：租戶在路徑裡、靜態渲染、真的 cookie 與 antiforgery。
/// 營運者經手的初始密碼要在第一次登入時改掉；失敗不洩漏帳號有沒有；session 只對簽發的租戶有效。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class PortalLoginTests(PlatformFactory factory)
{
    private Task<(TenantResponse Tenant, string Email, string Initial)> MemberAsync(string prefix, string role = CustomerRoles.Owner) =>
        PortalTestSupport.MemberAsync(factory, prefix, role);

    [Fact]
    public async Task TheFirstLoginMustChangeTheOperatorIssuedPasswordBeforeAnythingElse()
    {
        var (tenant, email, initial) = await MemberAsync("portal-a");
        using var browser = new PortalBrowser(factory);

        // 沒登入：導去這個租戶的登入頁，並帶回來的路徑。
        var anonymous = await browser.GetAsync(Home(tenant.Identifier));
        Assert.Equal(HttpStatusCode.Redirect, anonymous.Status);
        Assert.StartsWith(Login(tenant.Identifier), anonymous.Location, StringComparison.Ordinal);

        // 登入頁自己正常出現，有表單與標籤。
        var page = await browser.GetAsync(Login(tenant.Identifier));
        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("<label for=\"email\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("name=\"__RequestVerificationToken\"", page.Html, StringComparison.Ordinal);

        // 初始密碼對：導去改密碼頁，不是首頁。
        var signedIn = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        Assert.Equal(HttpStatusCode.Redirect, signedIn.Status);
        Assert.Equal(PasswordPage(tenant.Identifier), signedIn.Location);

        // 還沒改密碼就想進首頁：導去改密碼頁。
        var pending = await browser.GetAsync(Home(tenant.Identifier));
        Assert.Equal(HttpStatusCode.Redirect, pending.Status);
        Assert.Equal(PasswordPage(tenant.Identifier), pending.Location);

        var form = await browser.GetAsync(PasswordPage(tenant.Identifier));
        Assert.Equal(HttpStatusCode.OK, form.Status);
        Assert.Contains("營運人員給你的初始密碼", form.Html, StringComparison.Ordinal);

        // 改密碼：目前的密碼錯、兩次不一樣、太短，各有說明。
        var wrongCurrent = await browser.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange("not-the-password", NewPassword));
        Assert.Contains("目前的密碼不正確", wrongCurrent.Html, StringComparison.Ordinal);
        var mismatch = await browser.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange(initial, NewPassword, "something else"));
        Assert.Contains("兩次輸入的新密碼不一樣", mismatch.Html, StringComparison.Ordinal);
        var tooShort = await browser.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange(initial, "short"));
        Assert.Contains("至少要 12 個字元", tooShort.Html, StringComparison.Ordinal);

        var changed = await browser.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange(initial, NewPassword));
        Assert.Equal(HttpStatusCode.Redirect, changed.Status);
        Assert.Equal(Home(tenant.Identifier), changed.Location);

        // 現在進得了入口；「帳號與組織」頁看得到自己的組織、帳號與角色。
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(Home(tenant.Identifier))).Status);
        var home = await browser.GetAsync(AccountPage(tenant.Identifier));
        Assert.Equal(HttpStatusCode.OK, home.Status);
        Assert.Contains(email, home.Html, StringComparison.Ordinal);
        Assert.Contains("擁有者", home.Html, StringComparison.Ordinal);
        Assert.Contains("王小明", home.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheInitialPasswordStopsWorkingOnceItIsChangedAndTheNewOneWorks()
    {
        var (tenant, email, initial) = await MemberAsync("portal-b");
        using var first = new PortalBrowser(factory);
        _ = await first.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        var changed = await first.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange(initial, NewPassword));
        Assert.Equal(HttpStatusCode.Redirect, changed.Status);

        using var second = new PortalBrowser(factory);
        var old = await second.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        Assert.Equal(HttpStatusCode.OK, old.Status); // 表單重新出現，帶錯誤
        Assert.Contains("電子郵件或密碼不正確", old.Html, StringComparison.Ordinal);

        var fresh = await second.SubmitAsync(Login(tenant.Identifier), Credentials(email, NewPassword));
        Assert.Equal(HttpStatusCode.Redirect, fresh.Status);
        Assert.Equal(Home(tenant.Identifier), fresh.Location); // 改過了，直接進首頁
    }
}