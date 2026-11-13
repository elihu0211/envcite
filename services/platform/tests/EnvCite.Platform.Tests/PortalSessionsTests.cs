using System.Security.Claims;

using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口成員的重驗（互動式 circuit 用）：沒有 HTTP 請求，依 principal 的租戶 claim 自己開租戶 scope，
/// 做與 cookie 每個請求相同的檢查。要守住的是失效的每一條路——換戳記、停用、別的租戶、沒有租戶、不是入口成員。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
public sealed class PortalSessionsTests(PlatformFactory factory)
{
    private IPortalSessions Sessions => factory.Services.GetRequiredService<IPortalSessions>();

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    /// <summary>租戶加一位成員，與他登入後 cookie 裡的 principal（由真正的 claims 工廠簽發）。</summary>
    private async Task<(TenantResponse Tenant, string UserId, ClaimsPrincipal Session)> MemberSessionAsync(string slug)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(slug);
        var (userId, _) = await AddMemberAsync(factory, tenant.Id, NewEmail(), CustomerRoles.Viewer);
        return (tenant, userId, await MemberPrincipalAsync(factory, tenant.Id, userId));
    }

    private static ClaimsPrincipal WithTenantClaim(ClaimsPrincipal session, string? tenant)
    {
        var claims = session.Claims.Where(c => c.Type != PlatformClaimTypes.TenantIdentifier).ToList();
        if (tenant is not null)
            claims.Add(new Claim(PlatformClaimTypes.TenantIdentifier, tenant));

        return new ClaimsPrincipal(new ClaimsIdentity(claims, "portal"));
    }

    [Fact]
    public async Task ASessionIssuedToAnActiveMemberIsValid()
    {
        var (_, _, session) = await MemberSessionAsync("pss-a");

        Assert.True(await Sessions.IsStillValidAsync(session, Ct));
    }

    // 改密碼與營運者重設都會換安全戳記：其他裝置、其他分頁的 circuit 一分鐘內失效。
    [Fact]
    public async Task ResettingThePasswordEndsTheSession()
    {
        var (tenant, userId, session) = await MemberSessionAsync("pss-b");
        Assert.True(await Sessions.IsStillValidAsync(session, Ct));

        Assert.True((await factory.Services.GetRequiredService<IMemberAdmin>().ResetPasswordAsync(OperatorSession.Principal(), tenant.Id, userId, Ct)).IsOk);

        Assert.False(await Sessions.IsStillValidAsync(session, Ct));
    }

    [Fact]
    public async Task DisablingTheMemberEndsTheSession()
    {
        var (tenant, userId, session) = await MemberSessionAsync("pss-c");

        Assert.True((await factory.Services.GetRequiredService<IMemberAdmin>().SetDisabledAsync(OperatorSession.Principal(), tenant.Id, userId, true, Ct)).IsOk);

        Assert.False(await Sessions.IsStillValidAsync(session, Ct));
    }

    // 停用本來就會換戳記，上一個測試因此看不出旗標那一層；直接把旗標設起來、不動戳記，兩層才各自有證明。
    [Fact]
    public async Task TheDisabledFlagEndsTheSessionEvenWhenTheStampIsUnchanged()
    {
        var (tenant, userId, session) = await MemberSessionAsync("pss-h");
        Assert.True(await Sessions.IsStillValidAsync(session, Ct));

        var flagged = await factory.Services.GetRequiredService<TenantScopes>().InAsync<bool>(tenant.Id, async scope =>
        {
            var users = scope.Get<UserManager<ApplicationUser>>();
            var user = (await users.FindByIdAsync(userId))!;
            user.Disabled = true;
            return AdminOutcome.Ok((await users.UpdateAsync(user)).Succeeded);
        });
        Assert.True(flagged.Value);

        Assert.False(await Sessions.IsStillValidAsync(session, Ct));
    }

    // 帳號在 A 租戶，claim 卻寫 B 租戶：在 B 的 scope 裡找不到這個人，無效。
    [Fact]
    public async Task ASessionThatNamesAnotherTenantIsInvalid()
    {
        var (_, _, session) = await MemberSessionAsync("pss-d");
        var (other, _) = await factory.CreateTenantWithKeyAsync("pss-e");

        Assert.False(await Sessions.IsStillValidAsync(WithTenantClaim(session, other.Identifier), Ct));
    }

    [Fact]
    public async Task ASessionWithAnUnknownOrMissingTenantIsInvalid()
    {
        var (_, _, session) = await MemberSessionAsync("pss-f");

        Assert.False(await Sessions.IsStillValidAsync(WithTenantClaim(session, "no-such-tenant"), Ct));
        Assert.False(await Sessions.IsStillValidAsync(WithTenantClaim(session, null), Ct));
        Assert.False(await Sessions.IsStillValidAsync(WithTenantClaim(session, string.Empty), Ct));
    }

    [Fact]
    public async Task ThingsThatAreNotAnAuthenticatedPortalMemberAreInvalid()
    {
        var (_, _, session) = await MemberSessionAsync("pss-g");
        var withoutPortalId = new ClaimsPrincipal(new ClaimsIdentity(
            session.Claims.Where(c => c.Type != PlatformClaimTypes.PortalUserId), "portal"));
        var unauthenticated = new ClaimsPrincipal(new ClaimsIdentity(session.Claims)); // 沒有 authentication type

        Assert.False(await Sessions.IsStillValidAsync(withoutPortalId, Ct));
        Assert.False(await Sessions.IsStillValidAsync(unauthenticated, Ct));
        Assert.False(await Sessions.IsStillValidAsync(new ClaimsPrincipal(new ClaimsIdentity()), Ct));
    }
}