using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

using EnvLaw.Platform.Modules.Members;
using EnvLaw.Platform.Modules.Tenancy;
using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Time.Testing;

using OtpNet;

using static EnvLaw.Platform.Tests.PortalTestSupport;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// TOTP 兩步驟驗證：註冊、登入的第二步、復原碼、重放、鎖定的共用計數、營運者重設。
/// 每個測試用自己的主機與自己的假時鐘，才能撥動「下一個三十秒」而不影響其他測試。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed partial class PortalMfaTests(PlatformFactory factory)
{
    private const string GenericMfaError = "驗證碼不正確，或已經用過";

    private static string Security(string tenant) => $"/portal/{tenant}/security";

    private static string Mfa(string tenant) => $"/portal/{tenant}/login/mfa";

    private static Dictionary<string, string> Code(string code) => new() { ["Input.Code"] = code };

    private static string CodeAt(string key, DateTimeOffset at) => new Totp(Base32Encoding.ToBytes(key)).ComputeTotp(at.UtcDateTime);

    private (WebApplicationFactory<Program> Host, FakeTimeProvider Clock) HostWithOwnClock()
    {
        var clock = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var host = factory.WithWebHostBuilder(b => b.ConfigureServices(s =>
            s.Replace(ServiceDescriptor.Singleton<TimeProvider>(clock))));
        return (host, clock);
    }

    private sealed record Enrolled(TenantResponse Tenant, string Email, string Password, string Key, IReadOnlyList<string> RecoveryCodes, string UserId);

    /// <summary>建立成員、登入、改密碼、註冊兩步驟驗證；回傳金鑰與復原碼。時鐘停在註冊那一刻的下一個時間步。</summary>
    private async Task<Enrolled> EnrolledMemberAsync(WebApplicationFactory<Program> host, FakeTimeProvider clock, string prefix)
    {
        var (tenant, email, initial) = await MemberAsync(factory, prefix);
        using var browser = new PortalBrowser(host);
        var password = await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync(Security(tenant.Identifier));
        var key = SharedKey().Match(page.Html).Groups[1].Value.Replace(" ", string.Empty, StringComparison.Ordinal);
        var enabled = await browser.SubmitAsync(Security(tenant.Identifier), Code(CodeAt(key, clock.GetUtcNow())));
        Assert.Contains("兩步驟驗證已啟用", enabled.Html, StringComparison.Ordinal);
        var codes = RecoveryCodeItem().Matches(enabled.Html).Select(m => m.Groups[1].Value).ToList();

        clock.Advance(TimeSpan.FromSeconds(30)); // 註冊用掉了這一步；之後的登入用下一步
        var members = await host.Services.GetRequiredService<IMemberAdmin>().ListAsync(tenant.Id, CancellationToken.None);
        return new Enrolled(tenant, email, password, key, codes, members.Value!.Single().Id);
    }

    [GeneratedRegex("""id="shared-key">([^<]+)</code>""")]
    private static partial Regex SharedKey();

    [GeneratedRegex("""<li><code class="ep-mono">([^<]+)</code></li>""")]
    private static partial Regex RecoveryCodeItem();

    [Fact]
    public async Task EnrollingNeedsTheRightCodeAndEndsWithTenOneTimeRecoveryCodes()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var (tenant, email, initial) = await MemberAsync(factory, "mfa-a");
        using var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync(Security(tenant.Identifier));
        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("class=\"ep-qr\"", page.Html, StringComparison.Ordinal); // QR 在伺服器上畫好
        var key = SharedKey().Match(page.Html).Groups[1].Value.Replace(" ", string.Empty, StringComparison.Ordinal);
        Assert.True(key.Length >= 16);

        // 重新整理拿到同一把金鑰：已經掃進手機的 QR 不會失效。
        var again = await browser.GetAsync(Security(tenant.Identifier));
        Assert.Equal(key, SharedKey().Match(again.Html).Groups[1].Value.Replace(" ", string.Empty, StringComparison.Ordinal));

        var wrong = await browser.SubmitAsync(Security(tenant.Identifier), Code("000000"));
        Assert.Contains("驗證碼不正確", wrong.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("兩步驟驗證已啟用", wrong.Html, StringComparison.Ordinal);

        var enabled = await browser.SubmitAsync(Security(tenant.Identifier), Code(CodeAt(key, clock.GetUtcNow())));
        var codes = RecoveryCodeItem().Matches(enabled.Html).Select(m => m.Groups[1].Value).ToList();
        Assert.Equal(10, codes.Count);
        Assert.Equal(10, codes.Distinct().Count());

        // 復原碼只在這一個回應裡：再開頁面只剩「已啟用」，沒有金鑰、沒有 QR、沒有復原碼。
        var after = await browser.GetAsync(Security(tenant.Identifier));
        Assert.Contains("已啟用", after.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("class=\"ep-qr\"", after.Html, StringComparison.Ordinal); // 導覽的圖示也是 <svg>，所以認 QR 的容器
        Assert.DoesNotContain(codes[0], after.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(key, after.Html.Replace(" ", string.Empty, StringComparison.Ordinal), StringComparison.Ordinal);
    }

    [Fact]
    public async Task PasswordAloneNoLongerSignsInOnceTheSecondStepIsOn()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var member = await EnrolledMemberAsync(host, clock, "mfa-b");
        using var browser = new PortalBrowser(host);

        var step1 = await browser.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        Assert.Equal(Mfa(member.Tenant.Identifier), step1.Location);

        // 暫存憑證不是登入：首頁與改密碼頁都進不去。
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(member.Tenant.Identifier))).Status);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Security(member.Tenant.Identifier))).Status);

        var wrong = await browser.SubmitAsync(Mfa(member.Tenant.Identifier), Code("123456"));
        Assert.Contains(GenericMfaError, wrong.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(member.Tenant.Identifier))).Status);

        var ok = await browser.SubmitAsync(Mfa(member.Tenant.Identifier), Code(CodeAt(member.Key, clock.GetUtcNow())));
        Assert.Equal(Home(member.Tenant.Identifier), ok.Location);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(Home(member.Tenant.Identifier))).Status);
    }

    [Fact]
    public async Task ACodeCannotBeUsedTwiceEvenWithinItsThirtySeconds()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var member = await EnrolledMemberAsync(host, clock, "mfa-c");
        var code = CodeAt(member.Key, clock.GetUtcNow());

        using var first = new PortalBrowser(host);
        _ = await first.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        Assert.Equal(Home(member.Tenant.Identifier), (await first.SubmitAsync(Mfa(member.Tenant.Identifier), Code(code))).Location);

        // 旁觀者在三十秒內看到同一組碼、拿去重放：被擋。
        using var replay = new PortalBrowser(host);
        _ = await replay.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        var replayed = await replay.SubmitAsync(Mfa(member.Tenant.Identifier), Code(code));
        Assert.Contains(GenericMfaError, replayed.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.Redirect, (await replay.GetAsync(Home(member.Tenant.Identifier))).Status);

        // 下一個三十秒的新碼是好的。
        clock.Advance(TimeSpan.FromSeconds(30));
        var next = await replay.SubmitAsync(Mfa(member.Tenant.Identifier), Code(CodeAt(member.Key, clock.GetUtcNow())));
        Assert.Equal(Home(member.Tenant.Identifier), next.Location);
    }

    [Fact]
    public async Task ARecoveryCodeSignsInOnceAndOnlyOnce()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var member = await EnrolledMemberAsync(host, clock, "mfa-d");
        var recovery = member.RecoveryCodes[0];

        using var phoneLost = new PortalBrowser(host);
        _ = await phoneLost.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        var used = await phoneLost.SubmitAsync($"{Mfa(member.Tenant.Identifier)}?recovery=1", Code(recovery));
        Assert.Equal(Home(member.Tenant.Identifier), used.Location);

        using var again = new PortalBrowser(host);
        _ = await again.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        var reused = await again.SubmitAsync($"{Mfa(member.Tenant.Identifier)}?recovery=1", Code(recovery));
        Assert.Contains("復原碼不正確", reused.Html, StringComparison.Ordinal);

        // 另一組還是好的。
        var other = await again.SubmitAsync($"{Mfa(member.Tenant.Identifier)}?recovery=1", Code(member.RecoveryCodes[1]));
        Assert.Equal(Home(member.Tenant.Identifier), other.Location);
    }

    // 兩個計數若分開，知道密碼的人每輪重新輸入密碼就能把計數歸零，無限次猜六位數。
    [Fact]
    public async Task WrongCodesAndWrongPasswordsShareOneLockoutAndTypingThePasswordAgainDoesNotResetIt()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var member = await EnrolledMemberAsync(host, clock, "mfa-e");
        using var attacker = new PortalBrowser(host);

        for (var i = 0; i < MembersModule.MaxFailedAccessAttempts - 1; i++)
        {
            _ = await attacker.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password)); // 密碼對，歸零了嗎？
            _ = await attacker.SubmitAsync(Mfa(member.Tenant.Identifier), Code($"00000{i}"));
        }

        // 第 5 次（碼錯）鎖定；之後連對的碼也進不去。
        _ = await attacker.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        _ = await attacker.SubmitAsync(Mfa(member.Tenant.Identifier), Code("999999"));

        using var owner = new PortalBrowser(host);
        var locked = await owner.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        Assert.Equal(HttpStatusCode.OK, locked.Status); // 表單重新出現，沒有轉去第二步
        Assert.Contains("電子郵件或密碼不正確", locked.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task APendingTicketOnlyWorksForTheTenantThatIssuedIt()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var member = await EnrolledMemberAsync(host, clock, "mfa-f");
        var (other, _) = await factory.CreateTenantWithKeyAsync("mfa-f2");
        using var browser = new PortalBrowser(host);
        _ = await browser.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));

        // 拿 A 租戶的暫存憑證去 B 租戶的第二步頁面：當作沒有，回到輸入密碼。
        var crossTenant = await browser.GetAsync(Mfa(other.Identifier));
        Assert.Equal(HttpStatusCode.Redirect, crossTenant.Status);
        Assert.Equal(Login(other.Identifier), crossTenant.Location);
    }

    [Fact]
    public async Task TheSecondStepPageNeedsAPasswordFirst()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("mfa-g");
        using var browser = new PortalBrowser(factory);

        var page = await browser.GetAsync(Mfa(tenant.Identifier));

        Assert.Equal(HttpStatusCode.Redirect, page.Status);
        Assert.Equal(Login(tenant.Identifier), page.Location);
    }

    [Fact]
    public async Task AnOperatorResetLetsALockedOutMemberBackInWithJustAPasswordAndEndsTheirSessions()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var member = await EnrolledMemberAsync(host, clock, "mfa-h");
        using var session = new PortalBrowser(host);
        _ = await session.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        _ = await session.SubmitAsync(Mfa(member.Tenant.Identifier), Code(CodeAt(member.Key, clock.GetUtcNow())));
        Assert.Equal(HttpStatusCode.OK, (await session.GetAsync(Home(member.Tenant.Identifier))).Status);

        var reset = await host.Services.GetRequiredService<IMemberAdmin>()
            .ResetTwoFactorAsync(OperatorSession.Principal("ops@example.com"), member.Tenant.Id, member.UserId, CancellationToken.None);

        Assert.True(reset.IsOk);
        Assert.False(reset.Value!.TwoFactorEnabled);
        Assert.Equal(HttpStatusCode.Redirect, (await session.GetAsync(Home(member.Tenant.Identifier))).Status); // 既有 session 失效
        using var fresh = new PortalBrowser(host);
        var signedIn = await fresh.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        Assert.Equal(Home(member.Tenant.Identifier), signedIn.Location); // 只靠密碼
        // 舊的復原碼與金鑰都已丟掉，不能再用；要能重新註冊。
        Assert.Contains("class=\"ep-qr\"", (await fresh.GetAsync(Security(member.Tenant.Identifier))).Html, StringComparison.Ordinal);

        var audit = await host.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: member.Tenant.Id, Action: AuditActions.MemberMfaReset), CancellationToken.None);
        var evt = Assert.Single(audit.Items);
        Assert.Equal(("operator-user", "ops@example.com"), (evt.ActorType, evt.ActorId));
    }

    [Fact]
    public async Task AResetCannotReachAnotherTenantsMember()
    {
        var (host, clock) = HostWithOwnClock();
        await using var hostScope = host;
        var member = await EnrolledMemberAsync(host, clock, "mfa-i");
        var (other, _) = await factory.CreateTenantWithKeyAsync("mfa-i2");

        var reset = await host.Services.GetRequiredService<IMemberAdmin>()
            .ResetTwoFactorAsync(OperatorSession.Principal(), other.Id, member.UserId, CancellationToken.None);

        Assert.Equal(AdminStatus.NotFound, reset.Status);
    }

    // 金鑰、復原碼、碼本身都是密鑰：稽核（只能附加、刪不掉）與日誌裡一個都不能有。
    [Fact]
    public async Task NoSecretOfTheSecondStepEverReachesTheAuditTrailOrTheLogs()
    {
        var logs = new CapturingLoggerProvider();
        var clock = new FakeTimeProvider(DateTimeOffset.UtcNow);
        await using var host = factory.WithWebHostBuilder(b => b
            .ConfigureLogging(l => l.AddProvider(logs))
            .ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<TimeProvider>(clock))));
        var member = await EnrolledMemberAsync(host, clock, "mfa-j");
        using var browser = new PortalBrowser(host);
        _ = await browser.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        var typedCode = CodeAt(member.Key, clock.GetUtcNow());
        _ = await browser.SubmitAsync(Mfa(member.Tenant.Identifier), Code(typedCode));
        using var lostPhone = new PortalBrowser(host);
        _ = await lostPhone.SubmitAsync(Login(member.Tenant.Identifier), Credentials(member.Email, member.Password));
        _ = await lostPhone.SubmitAsync($"{Mfa(member.Tenant.Identifier)}?recovery=1", Code(member.RecoveryCodes[2]));

        var all = new List<AuditRecord>();
        foreach (var action in AuditActions.All)
        {
            all.AddRange((await host.Services.GetRequiredService<IAuditQuery>()
                .QueryAsync(new AuditFilter(Tenant: member.Tenant.Id, Action: action), CancellationToken.None)).Items);
        }

        var dump = JsonSerializer.Serialize(all) + string.Join('\n', logs.Messages);
        Assert.NotEmpty(logs.Messages);
        Assert.DoesNotContain(member.Key, dump, StringComparison.Ordinal);
        Assert.DoesNotContain("otpauth", dump, StringComparison.Ordinal);
        Assert.DoesNotContain(typedCode, dump, StringComparison.Ordinal);
        foreach (var recovery in member.RecoveryCodes)
        {
            Assert.DoesNotContain(recovery, dump, StringComparison.Ordinal);
        }

        Assert.Contains(all, e => e.Action == AuditActions.PortalMfaEnabled);
        Assert.Contains(all, e => e.Action == AuditActions.PortalRecoveryCodeUsed);
    }

    [Fact]
    public async Task EveryPortalResponseIsMarkedNoStore()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("mfa-k");
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var login = await client.GetAsync(new Uri(Login(tenant.Identifier), UriKind.Relative));
        using var protectedPage = await client.GetAsync(new Uri(Security(tenant.Identifier), UriKind.Relative));

        foreach (var response in new[] { login, protectedPage })
        {
            Assert.True(response.Headers.CacheControl?.NoStore, response.RequestMessage!.RequestUri!.AbsolutePath);
        }
    }

    [Theory]
    [InlineData("123456", "123456")]
    [InlineData("123 456", "123456")]
    [InlineData("123-456", "123456")]
    [InlineData(" 123456 ", "123456")]
    [InlineData("12345", null)]
    [InlineData("1234567", null)]
    [InlineData("12345a", null)]
    [InlineData("", null)]
    [InlineData(null, null)]
    public void CodesAreNormalisedLeniently(string? typed, string? expected) =>
        Assert.Equal(expected, TotpVerifierNormalize(typed));

    private static string? TotpVerifierNormalize(string? code) =>
        (string?)typeof(MembersModule).Assembly.GetType("EnvLaw.Platform.Modules.Members.TotpVerifier")!
            .GetMethod("Normalize", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic)!
            .Invoke(null, [code]);
}