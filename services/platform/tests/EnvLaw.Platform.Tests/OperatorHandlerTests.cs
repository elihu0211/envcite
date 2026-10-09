using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;

using Microsoft.AspNetCore.Authorization;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// 後台授權的每一道檢查各自單獨測，不經過資料庫：端對端的測試裡，一個失敗的條件常被別的條件蓋住
/// （例如停用的帳號同時也換了安全戳記）。所以直接測 handler，讓每一層都有自己的證明。
/// </summary>
public sealed class OperatorHandlerTests
{
    private sealed class FakeSessions(OperatorSessionState? session) : IOperatorSessions
    {
        public ClaimsPrincipal? Asked { get; private set; }

        public Task<OperatorSessionState?> FindAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
        {
            Asked = user;
            return Task.FromResult(session);
        }
    }

    private static readonly ClaimsPrincipal Someone = new(new ClaimsIdentity([new Claim(PlatformClaimTypes.OperatorId, "op-1")], "test"));

    private static async Task<bool> SucceedsAsync(OperatorSessionState? session, bool allowPasswordChange, bool allowEnrollment)
    {
        var requirement = new OperatorRequirement(allowPasswordChange, allowEnrollment);
        var context = new AuthorizationHandlerContext([requirement], Someone, null);
        await new OperatorHandler(new FakeSessions(session)).HandleAsync(context);
        return context.HasSucceeded;
    }

    [Theory]
    [InlineData(false, false)]
    [InlineData(true, false)]
    [InlineData(false, true)]
    [InlineData(true, true)]
    public async Task NoAccountMeansNoAccessUnderEveryPolicy(bool allowPasswordChange, bool allowEnrollment) =>
        // 帳號被刪、被停用、安全戳記不符，sessions 都回 null：每一層 policy 都不放行。
        Assert.False(await SucceedsAsync(null, allowPasswordChange, allowEnrollment));

    [Fact]
    public async Task TheFullPolicyNeedsAChangedPasswordAndAnEnrolledAuthenticator()
    {
        Assert.True(await SucceedsAsync(new OperatorSessionState(MustChangePassword: false, TwoFactorEnabled: true), false, false));
        Assert.False(await SucceedsAsync(new OperatorSessionState(MustChangePassword: true, TwoFactorEnabled: true), false, false));
        Assert.False(await SucceedsAsync(new OperatorSessionState(MustChangePassword: false, TwoFactorEnabled: false), false, false));
    }

    [Fact]
    public async Task TheEnrolmentPolicyLetsAnUnenrolledOperatorInButNotOneWhoStillHasTheIssuedPassword()
    {
        Assert.True(await SucceedsAsync(new OperatorSessionState(MustChangePassword: false, TwoFactorEnabled: false), false, true));
        Assert.True(await SucceedsAsync(new OperatorSessionState(MustChangePassword: false, TwoFactorEnabled: true), false, true));
        Assert.False(await SucceedsAsync(new OperatorSessionState(MustChangePassword: true, TwoFactorEnabled: false), false, true));
    }

    [Fact]
    public async Task ThePasswordPagePolicyAcceptsAnyValidAccount()
    {
        Assert.True(await SucceedsAsync(new OperatorSessionState(MustChangePassword: true, TwoFactorEnabled: false), true, true));
        Assert.True(await SucceedsAsync(new OperatorSessionState(MustChangePassword: false, TwoFactorEnabled: true), true, true));
    }

    [Fact]
    public async Task TheHandlerAsksAboutThePrincipalOfTheRequestEveryTime()
    {
        var sessions = new FakeSessions(new OperatorSessionState(false, true));
        var handler = new OperatorHandler(sessions);

        await handler.HandleAsync(new AuthorizationHandlerContext([new OperatorRequirement(false, false)], Someone, null));

        Assert.Same(Someone, sessions.Asked);
    }
}