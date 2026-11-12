using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvCite.Platform.Data;
using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Microsoft.Extensions.Time.Testing;

using static EnvCite.Platform.Tests.OperatorTestSupport;
using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 營運者後台的登入，全部走真的表單（有 cookie 罐、antiforgery、https 的瀏覽器）：帳密、強制改密碼、強制註冊驗證器、
/// 第二步、鎖定、停用立即生效、稽核。需要撥動「下一個三十秒」的測試用自己的主機與假時鐘。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class OperatorSignInTests(PlatformFactory factory)
{
    private const string GenericLoginError = "電子郵件或密碼不正確";
    private const string GenericMfaError = "驗證碼不正確，或已經用過";

    private (WebApplicationFactory<Program> Host, FakeTimeProvider Clock) HostWithOwnClock()
    {
        var clock = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var host = factory.WithWebHostBuilder(b => b.ConfigureServices(s =>
            s.Replace(ServiceDescriptor.Singleton<TimeProvider>(clock))));
        return (host, clock);
    }

    private sealed record Enrolled(string Id, string Email, string Password, string Key, IReadOnlyList<string> RecoveryCodes);

    /// <summary>建立營運者、第一次登入、改密碼、註冊驗證器；回傳金鑰與復原碼。時鐘停在註冊那一刻的下一個時間步。</summary>
    private static async Task<Enrolled> EnrolledAsync(WebApplicationFactory<Program> host, FakeTimeProvider clock)
    {
        var (id, email, initial) = await NewOperatorAsync(host);
        using var browser = new PortalBrowser(host);
        Assert.Equal(UiPassword, (await browser.SubmitAsync(UiLogin, Credentials(email, initial))).Location);
        Assert.Equal(UiHome, (await browser.SubmitAsync(UiPassword, PasswordChange(initial, NewPassword))).Location);

        var key = SharedKeyOf((await browser.GetAsync(UiSecurity)).Html);
        var enabled = await browser.SubmitAsync(UiSecurity, Code(CodeAt(key, clock.GetUtcNow())));
        Assert.Contains("兩步驟驗證已啟用", enabled.Html, StringComparison.Ordinal);

        clock.Advance(TimeSpan.FromSeconds(30)); // 註冊用掉了這一步；之後的登入用下一步
        return new Enrolled(id, email, NewPassword, key, RecoveryCodesOf(enabled.Html));
    }

    /// <summary>密碼加驗證碼的完整登入；回傳有 session 的瀏覽器。用掉一個時間步，之後時鐘前進到下一步。</summary>
    private static async Task<PortalBrowser> SignedInAsync(WebApplicationFactory<Program> host, FakeTimeProvider clock, Enrolled op)
    {
        var browser = new PortalBrowser(host);
        Assert.Equal(UiMfa, (await browser.SubmitAsync(UiLogin, Credentials(op.Email, op.Password))).Location);
        Assert.Equal(UiHome, (await browser.SubmitAsync(UiMfa, Code(CodeAt(op.Key, clock.GetUtcNow())))).Location);
        clock.Advance(TimeSpan.FromSeconds(30));
        return browser;
    }

    private static async Task<List<AuditRecord>> EventsAsync(WebApplicationFactory<Program> host, string action) =>
        [.. (await host.Services.GetRequiredService<IAuditQuery>().QueryAsync(new AuditFilter(Action: action, Limit: 200), CancellationToken.None)).Items];

    private static string? Email(AuditRecord record) =>
        record.Detail.TryGetProperty("email", out var email) ? email.GetString() : null;

    private static string Reason(AuditRecord record) => record.Detail.GetProperty("reason").GetString()!;

    // 絕對期限的起點由登入簽發（OnSigningIn），不是測試手工塞的：真的走完登入，超過上限就被拒，期限內仍可用。
    [Fact]
    public async Task ARealSignInIsEndedByTheAbsoluteLimitEvenWhenItKeepsBeingUsed()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var browser = await SignedInAsync(host, clock, op);

        // 每次間隔都在閒置期限（8 小時）內，滑動續期讓 cookie 一直有效；累計超過 12 小時才被絕對期限擋下。
        clock.Advance(TimeSpan.FromHours(6));
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(UiHome)).Status);
        clock.Advance(TimeSpan.FromHours(5));
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(UiHome)).Status);

        clock.Advance(TimeSpan.FromHours(2));
        Assert.Contains(UiLogin, (await browser.GetAsync(UiHome)).Location, StringComparison.Ordinal);
    }

    // ---- 第一次登入 -------------------------------------------------------------------------------

    [Fact]
    public async Task TheFirstSignInForcesANewPasswordThenAnAuthenticatorBeforeTheConsoleOpens()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var (_, email, initial) = await NewOperatorAsync(host);
        using var browser = new PortalBrowser(host);

        // 系統管理者給的初始密碼：只能去改密碼，其他頁一律被導回改密碼頁（改密碼在先，註冊在後）。
        var signedIn = await browser.SubmitAsync(UiLogin, Credentials(email, initial));
        Assert.Equal(UiPassword, signedIn.Location);
        Assert.Equal(UiPassword, (await browser.GetAsync(UiHome)).Location);
        Assert.Equal(UiPassword, (await browser.GetAsync("/ui/tenants")).Location);
        Assert.Equal(UiPassword, (await browser.GetAsync(UiSecurity)).Location);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(UiPassword)).Status);

        var mismatch = await browser.SubmitAsync(UiPassword, PasswordChange(initial, NewPassword, confirm: "something else entirely"));
        Assert.Contains("兩次輸入的新密碼不一樣", mismatch.Html, StringComparison.Ordinal);
        var same = await browser.SubmitAsync(UiPassword, PasswordChange(initial, initial));
        Assert.Contains("不能與目前的密碼相同", same.Html, StringComparison.Ordinal);
        var tooShort = await browser.SubmitAsync(UiPassword, PasswordChange(initial, "short"));
        Assert.Contains("至少要 12 個字元", tooShort.Html, StringComparison.Ordinal);
        var wrongCurrent = await browser.SubmitAsync(UiPassword, PasswordChange("not the current password", NewPassword));
        Assert.Contains("目前的密碼不正確", wrongCurrent.Html, StringComparison.Ordinal);
        Assert.Equal(UiPassword, (await browser.GetAsync(UiHome)).Location); // 都沒改成

        var changed = await browser.SubmitAsync(UiPassword, PasswordChange(initial, NewPassword));
        Assert.Equal(UiHome, changed.Location);

        // 密碼改了，但還沒有驗證器：主控台仍不開，只開註冊頁。
        Assert.Equal(UiSecurity, (await browser.GetAsync(UiHome)).Location);
        Assert.Equal(UiSecurity, (await browser.GetAsync("/ui/tenants")).Location);
        var page = await browser.GetAsync(UiSecurity);
        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("class=\"ep-qr\"", page.Html, StringComparison.Ordinal);
        var key = SharedKeyOf(page.Html);
        Assert.True(key.Length >= 16);
        Assert.Equal(key, SharedKeyOf((await browser.GetAsync(UiSecurity)).Html)); // 重新整理拿到同一把

        var wrong = await browser.SubmitAsync(UiSecurity, Code("000000"));
        Assert.Contains("驗證碼不正確", wrong.Html, StringComparison.Ordinal);
        Assert.Equal(UiSecurity, (await browser.GetAsync(UiHome)).Location);

        var enabled = await browser.SubmitAsync(UiSecurity, Code(CodeAt(key, clock.GetUtcNow())));
        var codes = RecoveryCodesOf(enabled.Html);
        Assert.Equal(10, codes.Count);
        Assert.Equal(10, codes.Distinct().Count());

        // 註冊完成才開主控台；復原碼只在那一個回應裡。
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(UiHome)).Status);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync("/ui/tenants")).Status);
        var after = await browser.GetAsync(UiSecurity);
        Assert.DoesNotContain("class=\"ep-qr\"", after.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(codes[0], after.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ARestrictedSessionCannotReachTheApiEitherAndCanSignOut()
    {
        var (_, email, initial) = await NewOperatorAsync(factory);
        using var browser = new PortalBrowser(factory);
        _ = await browser.SubmitAsync(UiLogin, Credentials(email, initial));

        Assert.Equal(HttpStatusCode.Unauthorized, await browser.StatusAsync("/v2/me"));
        var page = await browser.GetAsync(UiPassword);
        Assert.Contains("action=\"/ui/logout\"", page.Html, StringComparison.Ordinal);
    }

    // ---- 密碼錯、鎖定 -----------------------------------------------------------------------------

    [Fact]
    public async Task AWrongPasswordAnUnknownEmailAndADisabledAccountLookTheSameAndGetNoSession()
    {
        var (_, email, _) = await NewOperatorAsync(factory);
        var (disabledId, disabledEmail, disabledPassword) = await NewOperatorAsync(factory);
        Assert.True((await factory.Services.GetRequiredService<IOperatorAdmin>()
            .SetDisabledAsync(OperatorSession.Principal(), disabledId, true, CancellationToken.None)).IsOk);
        using var browser = new PortalBrowser(factory);

        var wrong = await browser.SubmitAsync(UiLogin, Credentials(email, "not-the-password-at-all"));
        var unknown = await browser.SubmitAsync(UiLogin, Credentials(NewOperatorEmail(), "not-the-password-at-all"));
        var disabled = await browser.SubmitAsync(UiLogin, Credentials(disabledEmail, disabledPassword)); // 密碼是對的

        foreach (var attempt in new[] { wrong, unknown, disabled })
        {
            Assert.Equal(HttpStatusCode.OK, attempt.Status);
            Assert.Contains(GenericLoginError, attempt.Html, StringComparison.Ordinal);
            Assert.Null(attempt.Location);
            Assert.DoesNotContain("not-the-password-at-all", attempt.Html, StringComparison.Ordinal); // 密碼不寫回表單
        }

        Assert.DoesNotContain(disabledPassword, disabled.Html, StringComparison.Ordinal);
        Assert.StartsWith(UiLogin, (await browser.GetAsync(UiHome)).Location, StringComparison.Ordinal); // 沒有 session
    }

    [Fact]
    public async Task FiveWrongPasswordsLockTheAccountForFifteenMinutesAndEvenTheRightPasswordIsRefused()
    {
        var (id, email, initial) = await NewOperatorAsync(factory);
        using var browser = new PortalBrowser(factory);

        for (var i = 0; i < MembersModule.MaxFailedAccessAttempts; i++)
        {
            var failed = await browser.SubmitAsync(UiLogin, Credentials(email, $"wrong-guess-{i}-number"));
            Assert.Contains(GenericLoginError, failed.Html, StringComparison.Ordinal);
        }

        var right = await browser.SubmitAsync(UiLogin, Credentials(email, initial));
        Assert.Equal(HttpStatusCode.OK, right.Status);
        Assert.Null(right.Location);
        Assert.Contains(GenericLoginError, right.Html, StringComparison.Ordinal); // 鎖定與密碼錯是同一句話

        await using var scope = factory.Services.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        var user = (await users.FindByIdAsync(id))!;
        Assert.True(await users.IsLockedOutAsync(user));
        // Identity 從 .NET 11 起取時間走 TimeProvider，測試主機換成了假時鐘：剩餘時間要以同一個時鐘算。
        var remaining = (await users.GetLockoutEndDateAsync(user))!.Value - factory.Time.GetUtcNow();
        Assert.InRange(remaining.TotalMinutes, 13, 16);
        Assert.Equal(TimeSpan.FromMinutes(15), MembersModule.LockoutDuration);

        var reasons = (await EventsAsync(factory, AuditActions.OperatorSignInDenied)).Where(e => e.TargetId == id).Select(Reason).ToList();
        Assert.Equal(MembersModule.MaxFailedAccessAttempts - 1, reasons.Count(r => r == "bad_password"));
        Assert.Contains("locked_out", reasons); // 第 5 次與之後
    }

    // ---- 第二步（TOTP 為必須） --------------------------------------------------------------------

    [Fact]
    public async Task PasswordAloneNeverOpensTheConsoleOnceAnAuthenticatorIsEnrolled()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var browser = new PortalBrowser(host);

        var step1 = await browser.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));
        Assert.Equal(UiMfa, step1.Location);

        // 暫存憑證不是登入：主控台、改密碼頁、註冊頁都進不去。
        foreach (var path in new[] { UiHome, "/ui/tenants", UiPassword, UiSecurity })
        {
            var denied = await browser.GetAsync(path);
            Assert.Equal(HttpStatusCode.Redirect, denied.Status);
            Assert.StartsWith(UiLogin, denied.Location, StringComparison.Ordinal);
        }

        var wrong = await browser.SubmitAsync(UiMfa, Code("123456"));
        Assert.Contains(GenericMfaError, wrong.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(UiHome)).Status);

        var ok = await browser.SubmitAsync(UiMfa, Code(CodeAt(op.Key, clock.GetUtcNow())));
        Assert.Equal(UiHome, ok.Location);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(UiHome)).Status);
    }

    [Fact]
    public async Task TheSecondStepPageNeedsAPasswordFirst()
    {
        using var browser = new PortalBrowser(factory);

        var page = await browser.GetAsync(UiMfa);

        Assert.Equal(HttpStatusCode.Redirect, page.Status);
        Assert.Equal(UiLogin, page.Location);
    }

    [Fact]
    public async Task ACodeCannotBeUsedTwiceEvenWithinItsThirtySeconds()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        var code = CodeAt(op.Key, clock.GetUtcNow());
        using var first = new PortalBrowser(host);
        _ = await first.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));
        Assert.Equal(UiHome, (await first.SubmitAsync(UiMfa, Code(code))).Location);

        // 旁觀者在三十秒內看到同一組碼、拿去重放：被擋。
        using var replay = new PortalBrowser(host);
        _ = await replay.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));
        var replayed = await replay.SubmitAsync(UiMfa, Code(code));
        Assert.Contains(GenericMfaError, replayed.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.Redirect, (await replay.GetAsync(UiHome)).Status);

        // 下一個三十秒的新碼是好的。
        clock.Advance(TimeSpan.FromSeconds(30));
        Assert.Equal(UiHome, (await replay.SubmitAsync(UiMfa, Code(CodeAt(op.Key, clock.GetUtcNow())))).Location);
    }

    [Fact]
    public async Task ARecoveryCodeSignsInOnceAndOnlyOnce()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        var recovery = $"{UiMfa}?recovery=1";

        using var phoneLost = new PortalBrowser(host);
        _ = await phoneLost.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));
        Assert.Equal(UiHome, (await phoneLost.SubmitAsync(recovery, Code(op.RecoveryCodes[0]))).Location);
        Assert.Equal(HttpStatusCode.OK, (await phoneLost.GetAsync(UiHome)).Status);

        using var again = new PortalBrowser(host);
        _ = await again.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));
        var reused = await again.SubmitAsync(recovery, Code(op.RecoveryCodes[0]));
        Assert.Contains("復原碼不正確", reused.Html, StringComparison.Ordinal);
        Assert.Equal(UiHome, (await again.SubmitAsync(recovery, Code(op.RecoveryCodes[1]))).Location); // 另一組還是好的

        var used = await EventsAsync(host, AuditActions.OperatorRecoveryCodeUsed);
        Assert.Equal(2, used.Count(e => e.TargetId == op.Id));
    }

    // 兩個計數若分開，知道密碼的人每輪重新輸入密碼就能把計數歸零，無限次猜六位數。
    [Fact]
    public async Task WrongCodesAndWrongPasswordsShareOneLockoutAndTypingThePasswordAgainDoesNotResetIt()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var attacker = new PortalBrowser(host);

        for (var i = 0; i < MembersModule.MaxFailedAccessAttempts; i++)
        {
            _ = await attacker.SubmitAsync(UiLogin, Credentials(op.Email, op.Password)); // 密碼對，歸零了嗎？
            _ = await attacker.SubmitAsync(UiMfa, Code($"00000{i}"));
        }

        // 第 5 次（碼錯）鎖定；之後連對的密碼、對的碼都進不去。
        using var owner = new PortalBrowser(host);
        var locked = await owner.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));
        Assert.Equal(HttpStatusCode.OK, locked.Status); // 表單重新出現，沒有轉去第二步
        Assert.Contains(GenericLoginError, locked.Html, StringComparison.Ordinal);
        // 鎖定時暫存憑證被收回：攻擊者手上的第二步頁面也沒了，要從密碼重來（而密碼現在也進不去）。
        Assert.Equal(UiLogin, (await attacker.GetAsync(UiMfa)).Location);
        Assert.Equal(HttpStatusCode.Redirect, (await attacker.GetAsync(UiHome)).Status);
    }

    [Fact]
    public async Task AccountDisabledBetweenThePasswordAndTheCodeEndsTheSignIn()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var browser = new PortalBrowser(host);
        _ = await browser.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));

        Assert.True((await host.Services.GetRequiredService<IOperatorAdmin>()
            .SetDisabledAsync(OperatorSession.Principal(), op.Id, true, CancellationToken.None)).IsOk);

        var late = await browser.SubmitAsync(UiMfa, Code(CodeAt(op.Key, clock.GetUtcNow())));
        Assert.Contains(GenericMfaError, late.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(UiHome)).Status);
    }

    // ---- 停用、重設：下一個請求就生效 --------------------------------------------------------------

    [Fact]
    public async Task DisablingAnOperatorTakesEffectOnTheNextRequestAndAnOldCookieNeverComesBack()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var browser = await SignedInAsync(host, clock, op);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(UiHome)).Status);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync("/ui/tenants")).Status);
        var admin = host.Services.GetRequiredService<IOperatorAdmin>();

        Assert.True((await admin.SetDisabledAsync(OperatorSession.Principal(), op.Id, true, CancellationToken.None)).IsOk);

        // cookie 還沒過期（8 小時），但下一個請求就被擋：後台頁面與帳號頁都是。
        foreach (var path in new[] { UiHome, "/ui/tenants", "/ui/audit", UiPassword, UiSecurity })
        {
            var denied = await browser.GetAsync(path);
            Assert.Equal(HttpStatusCode.Redirect, denied.Status);
            Assert.Equal("/ui/denied", denied.Location);
        }

        // 重新啟用不讓舊 cookie 復活（安全戳記換過）；要重新登入。
        Assert.True((await admin.SetDisabledAsync(OperatorSession.Principal(), op.Id, false, CancellationToken.None)).IsOk);
        Assert.Equal("/ui/denied", (await browser.GetAsync(UiHome)).Location);
        using var fresh = await SignedInAsync(host, clock, op);
        Assert.Equal(HttpStatusCode.OK, (await fresh.GetAsync(UiHome)).Status);
    }

    [Fact]
    public async Task AnOperatorPasswordResetEndsEverySessionAndForcesANewPassword()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var browser = await SignedInAsync(host, clock, op);

        var reset = await host.Services.GetRequiredService<IOperatorAdmin>()
            .ResetPasswordAsync(OperatorSession.Principal(), op.Id, CancellationToken.None);

        Assert.True(reset.IsOk);
        Assert.Equal("/ui/denied", (await browser.GetAsync(UiHome)).Location);
        using var oldPassword = new PortalBrowser(host);
        Assert.Contains(GenericLoginError, (await oldPassword.SubmitAsync(UiLogin, Credentials(op.Email, op.Password))).Html, StringComparison.Ordinal);

        // 新的一次性密碼：第二步照樣要過，過了之後必須先改密碼。
        using var next = new PortalBrowser(host);
        Assert.Equal(UiMfa, (await next.SubmitAsync(UiLogin, Credentials(op.Email, reset.Value!.Password))).Location);
        Assert.Equal(UiPassword, (await next.SubmitAsync(UiMfa, Code(CodeAt(op.Key, clock.GetUtcNow())))).Location);
        Assert.Equal(UiPassword, (await next.GetAsync(UiHome)).Location);
    }

    [Fact]
    public async Task ATwoFactorResetEndsEverySessionAndSendsThePersonBackToEnrolment()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var browser = await SignedInAsync(host, clock, op);

        var reset = await host.Services.GetRequiredService<IOperatorAdmin>()
            .ResetTwoFactorAsync(OperatorSession.Principal(), op.Id, CancellationToken.None);

        Assert.True(reset.IsOk);
        Assert.False(reset.Value!.TwoFactorEnabled);
        Assert.Equal("/ui/denied", (await browser.GetAsync(UiHome)).Location);

        // 只有密碼：進得了註冊頁，進不了主控台；舊的復原碼已作廢，新的金鑰與舊的不同。
        using var again = new PortalBrowser(host);
        Assert.Equal(UiSecurity, (await again.SubmitAsync(UiLogin, Credentials(op.Email, op.Password))).Location);
        Assert.Equal(UiSecurity, (await again.GetAsync(UiHome)).Location);
        var newKey = SharedKeyOf((await again.GetAsync(UiSecurity)).Html);
        Assert.NotEqual(op.Key, newKey);
        var enabled = await again.SubmitAsync(UiSecurity, Code(CodeAt(newKey, clock.GetUtcNow())));
        Assert.Contains("兩步驟驗證已啟用", enabled.Html, StringComparison.Ordinal);
        Assert.Empty(RecoveryCodesOf(enabled.Html).Intersect(op.RecoveryCodes));
        Assert.Equal(HttpStatusCode.OK, (await again.GetAsync(UiHome)).Status);
    }

    [Fact]
    public async Task ChangingYourOwnPasswordEndsTheOtherSessionsButNotThisOne()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var thisOne = await SignedInAsync(host, clock, op);
        using var other = await SignedInAsync(host, clock, op);

        var changed = await thisOne.SubmitAsync(UiPassword, PasswordChange(op.Password, "another correct horse battery"));

        Assert.Equal(UiHome, changed.Location);
        Assert.Equal(HttpStatusCode.OK, (await thisOne.GetAsync(UiHome)).Status);
        Assert.Equal("/ui/denied", (await other.GetAsync(UiHome)).Location);
        // 一次是第一次登入時改掉初始密碼，一次是這裡；操作者都是營運者自己。
        var changes = (await EventsAsync(host, AuditActions.OperatorPasswordChanged)).Where(e => e.TargetId == op.Id).ToList();
        Assert.Equal(2, changes.Count);
        Assert.All(changes, e => Assert.Equal((AuditActors.OperatorUser, op.Email), (e.ActorType, e.ActorId)));
    }

    // ---- 登出、轉址 -------------------------------------------------------------------------------

    [Fact]
    public async Task SigningOutEndsTheSessionAndIsAudited()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        using var browser = await SignedInAsync(host, clock, op);

        var loggedOut = await browser.SubmitAsync(UiHome, [], postPath: "/ui/logout");

        Assert.Equal("/ui/signed-out", loggedOut.Location);
        Assert.StartsWith(UiLogin, (await browser.GetAsync(UiHome)).Location, StringComparison.Ordinal);
        var signedOut = Assert.Single(await EventsAsync(host, AuditActions.OperatorSignedOut), e => e.TargetId == op.Id);
        Assert.Equal((AuditActors.OperatorUser, op.Email), (signedOut.ActorType, signedOut.ActorId));
    }

    [Fact]
    public async Task LoginSendsYouBackOnlyToAPageUnderUi()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);

        using var good = new PortalBrowser(host);
        var step1 = await good.SubmitAsync($"{UiLogin}?returnUrl=%2Fui%2Faudit", Credentials(op.Email, op.Password));
        Assert.Equal($"{UiMfa}?returnUrl=%2Fui%2Faudit", step1.Location); // 帶到第二步
        var done = await good.SubmitAsync(step1.Location!, Code(CodeAt(op.Key, clock.GetUtcNow())));
        Assert.Equal("/ui/audit", done.Location);

        clock.Advance(TimeSpan.FromSeconds(30));
        using var evil = new PortalBrowser(host);
        var evilStep1 = await evil.SubmitAsync($"{UiLogin}?returnUrl=https%3A%2F%2Fevil.example%2Fui", Credentials(op.Email, op.Password));
        var evilDone = await evil.SubmitAsync(evilStep1.Location!, Code(CodeAt(op.Key, clock.GetUtcNow())));
        Assert.Equal(UiHome, evilDone.Location);
    }

    // ---- 沒有自助註冊 -----------------------------------------------------------------------------

    [Theory]
    [InlineData("/ui/register")]
    [InlineData("/ui/signup")]
    [InlineData("/ui/account/create")]
    [InlineData("/ui/login/create")]
    public async Task ThereIsNoSelfSignupPage(string path)
    {
        using var browser = new PortalBrowser(factory);

        Assert.Equal(HttpStatusCode.NotFound, await browser.StatusAsync(path));
    }

    [Fact]
    public async Task SigningInWithAnUnknownEmailCreatesNoAccount()
    {
        var before = (await factory.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!.Count;
        var stranger = NewOperatorEmail();
        using var browser = new PortalBrowser(factory);

        _ = await browser.SubmitAsync(UiLogin, Credentials(stranger, "a perfectly good long password"));

        var after = (await factory.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!;
        Assert.Equal(before, after.Count);
        Assert.DoesNotContain(after, o => o.Email == stranger);
    }

    // ---- cookie ----------------------------------------------------------------------------------

    [Fact]
    public void TheSessionCookieIsHttpOnlySameSiteLaxSecureAndSlidesForEightHours()
    {
        var cookie = factory.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);

        Assert.Equal("EnvCite.Ui", cookie.Cookie.Name);
        Assert.True(cookie.Cookie.HttpOnly);
        Assert.Equal(SameSiteMode.Lax, cookie.Cookie.SameSite);
        Assert.Equal(CookieSecurePolicy.Always, cookie.Cookie.SecurePolicy);
        Assert.Equal(TimeSpan.FromHours(8), cookie.ExpireTimeSpan);
        Assert.True(cookie.SlidingExpiration);
    }

    // 第一步與第二步之間的暫存憑證不是登入 cookie：不同名稱、五分鐘、不滑動。
    [Fact]
    public void ThePendingTicketIsAnotherShortLivedCookie()
    {
        var pending = factory.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get("UiMfaPending");

        Assert.Equal("EnvCite.UiMfa", pending.Cookie.Name);
        Assert.Equal(TimeSpan.FromMinutes(5), pending.ExpireTimeSpan);
        Assert.False(pending.SlidingExpiration);
        Assert.True(pending.Cookie.HttpOnly);
    }

    // ---- 稽核 -------------------------------------------------------------------------------------

    [Fact]
    public async Task SignInsAreAuditedWithTheOperatorAsActorAndFailuresWithTheTypedEmail()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var op = await EnrolledAsync(host, clock);
        var stranger = NewOperatorEmail();
        const string pastedPassword = "hunter2 is not an email but a pasted password";

        using var browser = new PortalBrowser(host);
        _ = await browser.SubmitAsync(UiLogin, Credentials(op.Email, "wrong-guess-number-one"));
        _ = await browser.SubmitAsync(UiLogin, Credentials(stranger, "wrong-guess-number-two"));
        _ = await browser.SubmitAsync(UiLogin, Credentials(pastedPassword, "wrong-guess-number-three"));
        _ = await browser.SubmitAsync(UiLogin, Credentials(op.Email, op.Password));
        _ = await browser.SubmitAsync(UiMfa, Code("000000"));
        _ = await browser.SubmitAsync(UiMfa, Code(CodeAt(op.Key, clock.GetUtcNow())));

        var denied = await EventsAsync(host, AuditActions.OperatorSignInDenied);
        var badPassword = Assert.Single(denied, e => e.TargetId == op.Id && Reason(e) == "bad_password");
        Assert.Equal((AuditActors.Anonymous, op.Email), (badPassword.ActorType, Email(badPassword)));
        var unknown = Assert.Single(denied, e => Email(e) == stranger);
        Assert.Equal(("unknown_user", AuditActors.Anonymous, null), (Reason(unknown), unknown.ActorType, unknown.TargetId));
        var badCode = Assert.Single(denied, e => e.TargetId == op.Id && Reason(e) == "bad_totp");
        Assert.Equal(AuditActors.Anonymous, badCode.ActorType);

        // 貼進帳號欄的密碼不是 email，不進只能附加的稽核表。
        var pasted = Assert.Single(denied, e => e.Detail.GetProperty("email").ValueKind == JsonValueKind.Null && Reason(e) == "unknown_user"
            && e.OccurredAt >= badPassword.OccurredAt);
        Assert.DoesNotContain("hunter2", pasted.Detail.GetRawText(), StringComparison.Ordinal);

        var signedIn = Assert.Single(await EventsAsync(host, AuditActions.OperatorSignedIn), e => e.TargetId == op.Id && e.Detail.GetProperty("method").GetString() == "totp");
        Assert.Equal((AuditActors.OperatorUser, op.Email, (string?)null), (signedIn.ActorType, signedIn.ActorId, signedIn.TenantId));
    }

    private sealed class UnavailableAuditLog : IAuditLog
    {
        public Task AppendAsync(System.Security.Claims.ClaimsPrincipal actor, AuditEntry entry, CancellationToken cancellationToken) =>
            throw new InvalidOperationException("audit store unavailable");
    }

    // 營運者只有少數幾位：寧可登不進去，也不要有一次沒留紀錄的登入（成功的與被拒絕的都是）。
    [Fact]
    public async Task IfTheAuditCannotBeWrittenTheSignInFailsAndNoSessionIsIssued()
    {
        var (_, email, initial) = await NewOperatorAsync(factory);
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureServices(s =>
            s.Replace(ServiceDescriptor.Scoped<IAuditLog, UnavailableAuditLog>())));
        using var browser = new PortalBrowser(host);

        var succeeded = await browser.SubmitAsync(UiLogin, Credentials(email, initial));
        var denied = await browser.SubmitAsync(UiLogin, Credentials(email, "wrong-guess-number-one"));

        Assert.Equal(HttpStatusCode.InternalServerError, succeeded.Status);
        Assert.Equal(HttpStatusCode.InternalServerError, denied.Status);
        Assert.StartsWith(UiLogin, (await browser.GetAsync(UiHome)).Location, StringComparison.Ordinal); // 沒有 cookie
    }

    // ---- 機密不外流 -------------------------------------------------------------------------------

    [Fact]
    public async Task NoPasswordKeyCodeOrRecoveryCodeReachesTheLogsOrTheAuditTrail()
    {
        var logs = new CapturingLoggerProvider();
        var clock = new FakeTimeProvider(DateTimeOffset.UtcNow);
        await using var host = factory.WithWebHostBuilder(b => b
            .ConfigureLogging(l => l.AddProvider(logs))
            .ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<TimeProvider>(clock))));
        var (id, email, initial) = await NewOperatorAsync(host);
        using var browser = new PortalBrowser(host);
        _ = await browser.SubmitAsync(UiLogin, Credentials(email, initial));
        _ = await browser.SubmitAsync(UiPassword, PasswordChange(initial, NewPassword));
        var key = SharedKeyOf((await browser.GetAsync(UiSecurity)).Html);
        var enrolCode = CodeAt(key, clock.GetUtcNow());
        var enabled = await browser.SubmitAsync(UiSecurity, Code(enrolCode));
        var recovery = RecoveryCodesOf(enabled.Html);
        clock.Advance(TimeSpan.FromSeconds(30));
        var typedCode = CodeAt(key, clock.GetUtcNow());
        using var second = new PortalBrowser(host);
        _ = await second.SubmitAsync(UiLogin, Credentials(email, NewPassword));
        _ = await second.SubmitAsync(UiMfa, Code(typedCode));
        using var lostPhone = new PortalBrowser(host);
        _ = await lostPhone.SubmitAsync(UiLogin, Credentials(email, NewPassword));
        _ = await lostPhone.SubmitAsync($"{UiMfa}?recovery=1", Code(recovery[2]));
        var reset = await host.Services.GetRequiredService<IOperatorAdmin>().ResetPasswordAsync(OperatorSession.Principal(), id, CancellationToken.None);

        var all = new List<AuditRecord>();
        foreach (var action in AuditActions.All)
        {
            all.AddRange(await EventsAsync(host, action));
        }

        var dump = JsonSerializer.Serialize(all) + string.Join('\n', logs.Messages);
        Assert.NotEmpty(logs.Messages);
        Assert.Contains(AuditActions.OperatorMfaEnabled, dump, StringComparison.Ordinal); // 傾倒的內容確實含這次的事件
        foreach (var secret in new[] { initial, NewPassword, reset.Value!.Password, key, enrolCode, typedCode, "otpauth" }.Concat(recovery))
        {
            Assert.DoesNotContain(secret, dump, StringComparison.Ordinal);
        }
    }
}