using System.Net;
using System.Text.RegularExpressions;

using EnvCite.Platform.Modules.Tenancy;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Time.Testing;

using OtpNet;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 開啟強制時，Owner 與 Admin 必須啟用兩步驟驗證（07-01 §6）；一般成員可以自己關閉；任何人都能用驗證碼重新產生復原碼。
/// 共用測試主機與預設一樣關著強制（<c>RequireMfaForAdmins=false</c>）；這裡的主機把它打開。
/// </summary>
[Collection(PlatformTestGroup.Group4)]
public sealed partial class PortalMfaRequiredTests(PlatformFactory factory)
{
    private static string Security(string tenant) => $"/portal/{tenant}/security";

    private static string CodeAt(string key, DateTimeOffset at) => new Totp(Base32Encoding.ToBytes(key)).ComputeTotp(DateTime.UtcNow); // Identity 原生驗證器用系統時間，不看 TimeProvider。

    // 這一頁有好幾個表單，隱藏欄位裡的 _handler 會是最後一個：要送哪一個得明說。
    private static Dictionary<string, string> Regenerate(string code) => new() { ["_handler"] = "portal-mfa-regenerate", ["Regenerate.Code"] = code };

    private static Dictionary<string, string> Disable(string password, string code) =>
        new() { ["_handler"] = "portal-mfa-disable", ["Disable.Password"] = password, ["Disable.Code"] = code };

    private (WebApplicationFactory<Program> Host, FakeTimeProvider Clock) Host(bool required = true)
    {
        var clock = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var host = factory.WithWebHostBuilder(b => b
            .UseSetting("Platform:Portal:RequireMfaForAdmins", required ? "true" : "false")
            .ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<TimeProvider>(clock))));
        return (host, clock);
    }

    [GeneratedRegex("""id="shared-key">([^<]+)</code>""")]
    private static partial Regex SharedKey();

    [GeneratedRegex("""<li><code class="ep-mono">([^<]+)</code></li>""")]
    private static partial Regex RecoveryCodeItem();

    private static string KeyOf(string html) => SharedKey().Match(html).Groups[1].Value.Replace(" ", string.Empty, StringComparison.Ordinal);

    private static List<string> CodesOf(string html) => [.. RecoveryCodeItem().Matches(html).Select(m => m.Groups[1].Value)];

    /// <summary>登入、改密碼、（如果被要求）註冊兩步驟驗證；回傳登入後的瀏覽器與金鑰。</summary>
    private async Task<(PortalBrowser Browser, string Key, List<string> Codes, string Email, string Password, TenantResponse Tenant)> EnrolledAsync(
        WebApplicationFactory<Program> host, FakeTimeProvider clock, string prefix, string role)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(prefix);
        var email = NewEmail();
        var (_, initial) = await AddMemberAsync(factory, tenant.Id, email, role);
        var browser = new PortalBrowser(host);
        var password = await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        var key = KeyOf((await browser.GetAsync(Security(tenant.Identifier))).Html);
        var enabled = await browser.SubmitAsync(Security(tenant.Identifier), new() { ["Input.Code"] = CodeAt(key, clock.GetUtcNow()) });
        clock.Advance(TimeSpan.FromSeconds(30));
        return (browser, key, CodesOf(enabled.Html), email, password, tenant);
    }

    [Theory]
    [InlineData(CustomerRoles.Owner)]
    [InlineData(CustomerRoles.Admin)]
    public async Task AnOwnerOrAdminWithoutTheSecondStepCanOnlyReachTheEnrollmentPage(string role)
    {
        var (host, clock) = Host();
        await using var hostScope = host;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("req-a");
        var email = NewEmail();
        var (_, initial) = await AddMemberAsync(factory, tenant.Id, email, role);
        using var browser = new PortalBrowser(host);

        // 先改初始密碼（註冊前要有一個只有本人知道的密碼），再被帶去註冊。
        var signedIn = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        Assert.Equal(PasswordPage(tenant.Identifier), signedIn.Location);
        var changed = await browser.SubmitAsync(PasswordPage(tenant.Identifier), PasswordChange(initial, NewPassword));
        Assert.Equal(Home(tenant.Identifier), changed.Location);

        var home = await browser.GetAsync(Home(tenant.Identifier));
        Assert.Equal(HttpStatusCode.Redirect, home.Status);
        Assert.Equal(Security(tenant.Identifier), home.Location);
        Assert.Equal(Security(tenant.Identifier), (await browser.GetAsync($"/portal/{tenant.Identifier}/search")).Location);

        var page = await browser.GetAsync(Security(tenant.Identifier));
        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("你的角色必須使用兩步驟驗證", page.Html, StringComparison.Ordinal);

        var key = KeyOf(page.Html);
        _ = await browser.SubmitAsync(Security(tenant.Identifier), new() { ["Input.Code"] = CodeAt(key, clock.GetUtcNow()) });

        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(Home(tenant.Identifier))).Status);
    }

    [Fact]
    public async Task APasswordChangeComesBeforeEnrollmentSoTheOperatorsPasswordNeverProtectsAFactor()
    {
        var (host, _) = Host();
        await using var hostScope = host;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("req-b");
        var email = NewEmail();
        var (_, initial) = await AddMemberAsync(factory, tenant.Id, email, CustomerRoles.Owner);
        using var browser = new PortalBrowser(host);
        _ = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));

        var enrollment = await browser.GetAsync(Security(tenant.Identifier));

        Assert.Equal(HttpStatusCode.Redirect, enrollment.Status);
        Assert.Equal(PasswordPage(tenant.Identifier), enrollment.Location);
    }

    [Theory]
    [InlineData(CustomerRoles.Developer)]
    [InlineData(CustomerRoles.Billing)]
    [InlineData(CustomerRoles.Viewer)]
    public async Task OtherRolesAreNotForcedAndSwitchingTheRequirementOffFreesEveryone(string role)
    {
        var (host, _) = Host();
        await using var hostScope = host;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("req-c");
        var email = NewEmail();
        var (_, initial) = await AddMemberAsync(factory, tenant.Id, email, role);
        using var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(Home(tenant.Identifier))).Status);

        var (relaxed, _) = Host(required: false);
        await using var relaxedScope = relaxed;
        var (tenant2, email2, initial2) = await MemberAsync(factory, "req-c2", CustomerRoles.Owner);
        using var owner = new PortalBrowser(relaxed);
        await SignInFullyAsync(owner, tenant2.Identifier, email2, initial2);
        Assert.Equal(HttpStatusCode.OK, (await owner.GetAsync(Home(tenant2.Identifier))).Status);
    }

    [Fact]
    public async Task ARequiredRoleCannotTurnItOff()
    {
        var (host, clock) = Host();
        await using var hostScope = host;
        var member = await EnrolledAsync(host, clock, "req-d", CustomerRoles.Admin);
        using var browser = member.Browser;

        var page = await browser.GetAsync(Security(member.Tenant.Identifier));
        Assert.Contains("不能關閉", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("name=\"Disable.Password\"", page.Html, StringComparison.Ordinal);

        // 沒有按鈕，但服務端自己也要擋：直接呼叫關閉，被要求的角色回 Invalid，因子仍然啟用。
        var closed = await host.Services.GetRequiredService<IMemberAdmin>().ListAsync(member.Tenant.Id, CancellationToken.None);
        Assert.True(closed.Value!.Single().TwoFactorEnabled);
        using var scope = host.Services.CreateScope();
        scope.ServiceProvider.GetRequiredService<IMultiTenantContextSetter>().MultiTenantContext =
            new MultiTenantContext<TenantInfo>(new TenantInfo { Id = member.Tenant.Id, Identifier = member.Tenant.Identifier });
        var principal = new System.Security.Claims.ClaimsPrincipal(new System.Security.Claims.ClaimsIdentity(
            [
                new System.Security.Claims.Claim(PlatformClaimTypes.PortalUserId, closed.Value!.Single().Id),
                new System.Security.Claims.Claim(System.Security.Claims.ClaimTypes.Role, CustomerRoles.Admin),
            ],
            "portal"));
        var refused = await scope.ServiceProvider.GetRequiredService<IPortalTwoFactor>()
            .DisableAsync(principal, member.Password, CodeAt(member.Key, clock.GetUtcNow()), CancellationToken.None);
        Assert.Equal(AdminStatus.Invalid, refused.Status);
        Assert.True((await host.Services.GetRequiredService<IMemberAdmin>().ListAsync(member.Tenant.Id, CancellationToken.None)).Value!.Single().TwoFactorEnabled);
    }

    [Fact]
    public async Task AnOrdinaryMemberCanTurnItOffWithThePasswordAndACodeAndNeitherAloneIsEnough()
    {
        var (host, clock) = Host();
        await using var hostScope = host;
        var member = await EnrolledAsync(host, clock, "req-e", CustomerRoles.Viewer);
        using var browser = member.Browser;
        Assert.Contains("name=\"Disable.Password\"", (await browser.GetAsync(Security(member.Tenant.Identifier))).Html, StringComparison.Ordinal);

        // 只有驗證碼（拿到沒鎖的手機）、只有密碼（看到密碼）：都不行。
        var codeOnly = await browser.SubmitAsync(Security(member.Tenant.Identifier),
            Disable("not-the-password-at-all", CodeAt(member.Key, clock.GetUtcNow())));
        Assert.Contains("密碼或驗證碼不正確", codeOnly.Html, StringComparison.Ordinal);
        clock.Advance(TimeSpan.FromSeconds(30));
        var passwordOnly = await browser.SubmitAsync(Security(member.Tenant.Identifier),
            Disable(member.Password, "000000"));
        Assert.Contains("密碼或驗證碼不正確", passwordOnly.Html, StringComparison.Ordinal);

        clock.Advance(TimeSpan.FromSeconds(30));
        var off = await browser.SubmitAsync(Security(member.Tenant.Identifier),
            Disable(member.Password, CodeAt(member.Key, clock.GetUtcNow())));
        Assert.Contains("已關閉兩步驟驗證", off.Html, StringComparison.Ordinal);

        // 之後只靠密碼登入；舊的復原碼已作廢（沒有第二步可用）。
        using var fresh = new PortalBrowser(host);
        var signedIn = await fresh.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        Assert.Equal(Home(member.Tenant.Identifier), signedIn.Location);

        var audit = await host.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: member.Tenant.Id, Action: AuditActions.PortalMfaDisabled), CancellationToken.None);
        Assert.Single(audit.Items);
    }

    [Fact]
    public async Task RegeneratingRecoveryCodesNeedsACodeAndRetiresTheOldOnes()
    {
        var (host, clock) = Host();
        await using var hostScope = host;
        var member = await EnrolledAsync(host, clock, "req-f", CustomerRoles.Developer);
        using var browser = member.Browser;

        var wrong = await browser.SubmitAsync(Security(member.Tenant.Identifier), Regenerate("000000"));
        Assert.Contains("驗證碼不正確", wrong.Html, StringComparison.Ordinal);

        clock.Advance(TimeSpan.FromSeconds(30));
        var regenerated = await browser.SubmitAsync(Security(member.Tenant.Identifier), Regenerate(CodeAt(member.Key, clock.GetUtcNow())));
        var fresh = CodesOf(regenerated.Html);
        Assert.Equal(10, fresh.Count);
        Assert.Empty(fresh.Intersect(member.Codes));

        // 舊碼作廢、新碼可用。
        using var oldAttempt = new PortalBrowser(host);
        _ = await oldAttempt.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        var oldCode = await oldAttempt.SubmitAsync($"/portal/{member.Tenant.Identifier}/login/mfa?recovery=1", new() { ["Input.Code"] = member.Codes[0] });
        Assert.Contains("復原碼不正確", oldCode.Html, StringComparison.Ordinal);
        var newCode = await oldAttempt.SubmitAsync($"/portal/{member.Tenant.Identifier}/login/mfa?recovery=1", new() { ["Input.Code"] = fresh[0] });
        Assert.Equal(Home(member.Tenant.Identifier), newCode.Location);

        var audit = await host.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: member.Tenant.Id, Action: AuditActions.PortalRecoveryCodesRegenerated), CancellationToken.None);
        Assert.Single(audit.Items);
    }

    [Fact]
    public async Task AnOperatorResetSendsARequiredRoleBackToEnrollment()
    {
        var (host, clock) = Host();
        await using var hostScope = host;
        var member = await EnrolledAsync(host, clock, "req-g", CustomerRoles.Owner);
        using var browser = member.Browser;
        var id = (await host.Services.GetRequiredService<IMemberAdmin>().ListAsync(member.Tenant.Id, CancellationToken.None)).Value!.Single().Id;

        _ = await host.Services.GetRequiredService<IMemberAdmin>()
            .ResetTwoFactorAsync(OperatorSession.Principal(), member.Tenant.Id, id, CancellationToken.None);

        // 重設讓他可以只靠密碼登入，但角色仍然要求：登入後只進得了註冊頁，不是回到沒有保護的狀態。
        using var fresh = new PortalBrowser(host);
        _ = await fresh.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        var home = await fresh.GetAsync(Home(member.Tenant.Identifier));
        Assert.Equal(Security(member.Tenant.Identifier), home.Location);
    }
}