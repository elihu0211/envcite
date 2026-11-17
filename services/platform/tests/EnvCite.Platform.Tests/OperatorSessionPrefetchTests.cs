using System.Security.Claims;

using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Components.Authorization;

namespace EnvCite.Platform.Tests;

/// <summary>
/// circuit 開啟時先預取營運者的授權（第一次渲染才不用等一趟資料庫、外殼與內容同一批畫出）。
/// 查詢與「只用一次、只給那個 principal、最多幾秒」的規則見 <c>OperatorSessionChecksTests</c>；這裡只證明交給它的是 circuit 的那個身分。
/// </summary>
public sealed class OperatorSessionPrefetchTests
{
    private sealed class FakeState(ClaimsPrincipal user) : AuthenticationStateProvider
    {
        public override Task<AuthenticationState> GetAuthenticationStateAsync() => Task.FromResult(new AuthenticationState(user));
    }

    private sealed class RecordingSessions : IOperatorSessions
    {
        public List<ClaimsPrincipal> Prefetched { get; } = [];

        public Task<OperatorSessionState?> FindAsync(ClaimsPrincipal user, CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task PrefetchAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
        {
            Prefetched.Add(user);
            return Task.CompletedTask;
        }
    }

    [Fact]
    public async Task OpeningACircuitPrefetchesTheCircuitsOwnIdentity()
    {
        var user = new ClaimsPrincipal(new ClaimsIdentity([new Claim(PlatformClaimTypes.OperatorId, "op-1")], "test"));
        var sessions = new RecordingSessions();

        await new OperatorSessionPrefetch(new FakeState(user), sessions)
            .OnCircuitOpenedAsync(null!, TestContext.Current.CancellationToken);

        Assert.Same(user, Assert.Single(sessions.Prefetched));
    }
}