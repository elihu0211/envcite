using System.Security.Claims;

using EnvCite.Platform.Web.Auth;

using Microsoft.Extensions.Logging.Abstractions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// circuit 的重驗要把兩種身分分開：入口成員走入口的檢查，其餘已登入者一律當營運者驗。
/// 營運者那一頭的端對端見 <c>OperatorSessionChecksTests</c>；這裡只證明分流本身：
/// 誰被送去哪裡、另一邊有沒有被問、不認得的身分是失效而不是放行。
/// </summary>
public sealed class PlatformAuthenticationStateProviderTests
{
    private sealed class FakeOperators(bool known) : IOperatorSessions
    {
        public int Asked { get; private set; }

        public Task<OperatorSessionState?> FindAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
        {
            Asked++;
            return Task.FromResult<OperatorSessionState?>(known ? new OperatorSessionState(false, true) : null);
        }
    }

    private sealed class FakePortal(bool valid) : IPortalSessions
    {
        public int Asked { get; private set; }

        public Task<bool> IsStillValidAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
        {
            Asked++;
            return Task.FromResult(valid);
        }
    }

    private static ClaimsPrincipal Anonymous => new(new ClaimsIdentity());

    private static ClaimsPrincipal Operator => new(new ClaimsIdentity([new Claim(ClaimTypes.Email, "op@example.com")], "ui"));

    private static ClaimsPrincipal Member => new(new ClaimsIdentity(
        [new Claim(PlatformClaimTypes.PortalUserId, "user-1"), new Claim(PlatformClaimTypes.TenantIdentifier, "acme")], "portal"));

    private static PlatformAuthenticationStateProvider Provider(FakeOperators operators, FakePortal portal) =>
        new(NullLoggerFactory.Instance, operators, portal);

    [Fact]
    public async Task AnAnonymousVisitorHasNothingToRevalidateAndNobodyIsAsked()
    {
        var (operators, portal) = (new FakeOperators(known: false), new FakePortal(valid: false));

        Assert.True(await Provider(operators, portal).IsStillValidAsync(Anonymous, CancellationToken.None));

        Assert.Equal((0, 0), (operators.Asked, portal.Asked));
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task APortalMemberIsJudgedByThePortalAndTheOperatorsAreNeverAsked(bool portalSaysValid)
    {
        var (operators, portal) = (new FakeOperators(known: true), new FakePortal(portalSaysValid)); // 營運者那邊就算「認得」也不能放行

        Assert.Equal(portalSaysValid, await Provider(operators, portal).IsStillValidAsync(Member, CancellationToken.None));

        Assert.Equal((0, 1), (operators.Asked, portal.Asked));
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task AnyOtherSignedInPrincipalIsJudgedAsAnOperatorAndThePortalIsNeverAsked(bool operatorKnown)
    {
        var (operators, portal) = (new FakeOperators(operatorKnown), new FakePortal(valid: true));

        Assert.Equal(operatorKnown, await Provider(operators, portal).IsStillValidAsync(Operator, CancellationToken.None));

        Assert.Equal((1, 0), (operators.Asked, portal.Asked));
    }
}