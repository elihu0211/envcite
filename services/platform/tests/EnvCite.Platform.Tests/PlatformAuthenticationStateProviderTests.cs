using System.Security.Claims;

using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;
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

    /// <summary>circuit 的第一個網址；<c>null</c> 是還沒設定（框架尚未初始化導覽）。</summary>
    private sealed class FakeNavigation : NavigationManager
    {
        public FakeNavigation(string? uri)
        {
            if (uri is not null)
                Initialize("http://localhost/", uri);
        }

        protected override void NavigateToCore(string uri, NavigationOptions options) => throw new NotSupportedException();
    }

    private static ClaimsPrincipal Anonymous => new(new ClaimsIdentity());

    private static ClaimsPrincipal Operator => new(new ClaimsIdentity([new Claim(ClaimTypes.Email, "op@example.com")], "ui"));

    private static ClaimsPrincipal Member => new(new ClaimsIdentity(
        [new Claim(PlatformClaimTypes.PortalUserId, "user-1"), new Claim(PlatformClaimTypes.TenantIdentifier, "acme")], "portal"));

    private static PlatformAuthenticationStateProvider Provider(FakeOperators operators, FakePortal portal, string? startUri = "http://localhost/ui") =>
        new(NullLoggerFactory.Instance, operators, portal, new FakeNavigation(startUri));

    /// <summary>連線層把兩個 cookie 認出來的人交給 circuit，circuit 啟動後的使用者是哪一個。</summary>
    private static async Task<ClaimsPrincipal> StartedAsAsync(ClaimsPrincipal connection, string? startUri)
    {
        using var provider = Provider(new FakeOperators(known: true), new FakePortal(valid: true), startUri);
        ((IHostEnvironmentAuthenticationStateProvider)provider).SetAuthenticationState(Task.FromResult(new AuthenticationState(connection)));
        return (await provider.GetAuthenticationStateAsync()).User;
    }

    private static ClaimsPrincipal Both => CircuitIdentities.Combine(Operator, Member);

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

    // 瀏覽器可以同時帶著兩張 cookie；這條 circuit 是為哪一頁開的，由第一個網址決定，之後它只看得見那一邊。
    [Theory]
    [InlineData("http://localhost/portal/acme", true)]
    [InlineData("http://localhost/portal/acme/c/6f1f2f0e-0000-0000-0000-000000000000", true)]
    [InlineData("http://localhost/Portal/Acme", true)]
    [InlineData("http://localhost/ui", false)]
    [InlineData("http://localhost/ui/tenants?x=1", false)]
    [InlineData("http://localhost/", false)]
    public async Task ACircuitStartedWithBothCookiesIsOnlyEverTheSideItsFirstPageBelongsTo(string startUri, bool portalSide)
    {
        var user = await StartedAsAsync(Both, startUri);

        Assert.True(user.Identity!.IsAuthenticated);
        Assert.Single(user.Identities);
        Assert.Equal(portalSide, user.FindFirst(PlatformClaimTypes.PortalUserId) is not null);
        Assert.Equal(portalSide ? "user-1" : null, user.FindFirst(PlatformClaimTypes.PortalUserId)?.Value);
        Assert.Equal(portalSide ? null : "op@example.com", user.FindFirst(ClaimTypes.Email)?.Value);
    }

    // 要的那一邊沒有人就是匿名，不退而求其次：進入口頁帶著營運者的身分，或進後台帶著入口成員的身分，都只會被授權擋下。
    [Theory]
    [InlineData("http://localhost/portal/acme")]
    [InlineData("http://localhost/ui")]
    public async Task ACircuitNeverFallsBackToTheOtherSide(string startUri)
    {
        var portalPage = startUri.Contains("/portal/", StringComparison.Ordinal);

        var onlyTheOtherSide = await StartedAsAsync(portalPage ? Operator : Member, startUri);

        Assert.False(onlyTheOtherSide.Identity!.IsAuthenticated);
        Assert.Empty(onlyTheOtherSide.Claims);
    }

    [Fact]
    public async Task AnAnonymousConnectionStaysAnonymousWhicheverPageItStartsOn()
    {
        Assert.False((await StartedAsAsync(Anonymous, "http://localhost/portal/acme")).Identity!.IsAuthenticated);
        Assert.False((await StartedAsAsync(Anonymous, "http://localhost/ui")).Identity!.IsAuthenticated);
    }

    // 網址還沒設定好時不猜：維持只有營運者的舊行為，不會因為讀不到網址就把入口成員放進後台。
    [Fact]
    public async Task WithoutAStartingUrlTheCircuitIsTheOperatorSideAsItAlwaysWas()
    {
        var user = await StartedAsAsync(Both, startUri: null);

        Assert.Single(user.Identities);
        Assert.Equal("op@example.com", user.FindFirst(ClaimTypes.Email)?.Value);
        Assert.Null(user.FindFirst(PlatformClaimTypes.PortalUserId));
    }

    // 伺服器端渲染時框架交進來的就是單一身分的請求使用者，挑選不改它。
    [Fact]
    public async Task ARequestUserWithASingleIdentityPassesThroughUnchanged()
    {
        var operatorPage = await StartedAsAsync(Operator, "http://localhost/ui");
        var portalPage = await StartedAsAsync(Member, "http://localhost/portal/acme");

        Assert.Equal("op@example.com", operatorPage.FindFirst(ClaimTypes.Email)?.Value);
        Assert.Equal("user-1", portalPage.FindFirst(PlatformClaimTypes.PortalUserId)?.Value);
    }

    // 挑出來的身分接著照原本的分流重驗：入口成員只問入口，營運者只問營運者。
    [Fact]
    public async Task ThePickedIdentityIsRevalidatedBySideItBelongsTo()
    {
        var (operators, portal) = (new FakeOperators(known: true), new FakePortal(valid: true));
        using var provider = Provider(operators, portal, "http://localhost/portal/acme");
        ((IHostEnvironmentAuthenticationStateProvider)provider).SetAuthenticationState(Task.FromResult(new AuthenticationState(Both)));

        var state = await provider.GetAuthenticationStateAsync();
        Assert.True(await provider.IsStillValidAsync(state.User, CancellationToken.None));

        Assert.Equal((0, 1), (operators.Asked, portal.Asked));
    }
}

/// <summary>連線層的合併與 circuit 啟動時的挑選：兩邊的 claim 型別大量重疊，所以挑完以後不能留下另一邊的任何東西。</summary>
public sealed class CircuitIdentitiesTests
{
    private static ClaimsPrincipal Operator => new(new ClaimsIdentity(
        [
            new Claim(PlatformClaimTypes.OperatorId, "op-1"),
            new Claim(ClaimTypes.NameIdentifier, "op-1"),
            new Claim(ClaimTypes.Email, "op@example.com"),
            new Claim("AspNet.Identity.SecurityStamp", "operator-stamp"),
            new Claim(PlatformClaimTypes.MfaEnrollmentRequired, "true"),
        ], "Identity.Application"));

    private static ClaimsPrincipal Member => new(new ClaimsIdentity(
        [
            new Claim(PlatformClaimTypes.PortalUserId, "user-1"),
            new Claim(ClaimTypes.NameIdentifier, "user-1"),
            new Claim(ClaimTypes.Email, "member@example.com"),
            new Claim("AspNet.Identity.SecurityStamp", "member-stamp"),
            new Claim(PlatformClaimTypes.TenantIdentifier, "acme"),
        ], "Identity.Application")); // 兩邊的 identity 連 authentication type 都一樣，只能靠 claim 分辨

    private static ClaimsPrincipal Anonymous => new(new ClaimsIdentity());

    [Fact]
    public void ACombinedConnectionCarriesBothIdentitiesAndAnAnonymousOneJustTheMember()
    {
        Assert.Equal(2, CircuitIdentities.Combine(Operator, Member).Identities.Count());

        var member = Member;
        Assert.Same(member, CircuitIdentities.Combine(Anonymous, member));
    }

    [Fact]
    public void PickingThePortalSideLeavesNothingOfTheOperator()
    {
        var picked = CircuitIdentities.Narrow(CircuitIdentities.Combine(Operator, Member), portalSurface: true);

        Assert.Single(picked.Identities);
        Assert.Equal(["user-1"], picked.FindAll(ClaimTypes.NameIdentifier).Select(c => c.Value));
        Assert.Equal(["member-stamp"], picked.FindAll("AspNet.Identity.SecurityStamp").Select(c => c.Value));
        Assert.Null(picked.FindFirst(PlatformClaimTypes.OperatorId));
        Assert.Null(picked.FindFirst(PlatformClaimTypes.MfaEnrollmentRequired)); // 營運者待註冊驗證器的旗標不能讓入口成員被擋
    }

    [Fact]
    public void PickingTheOperatorSideLeavesNothingOfThePortalMember()
    {
        var picked = CircuitIdentities.Narrow(CircuitIdentities.Combine(Operator, Member), portalSurface: false);

        Assert.Single(picked.Identities);
        Assert.Equal(["op-1"], picked.FindAll(ClaimTypes.NameIdentifier).Select(c => c.Value));
        Assert.Equal(["operator-stamp"], picked.FindAll("AspNet.Identity.SecurityStamp").Select(c => c.Value));
        Assert.Null(picked.FindFirst(PlatformClaimTypes.PortalUserId));
        Assert.Null(picked.FindFirst(PlatformClaimTypes.TenantIdentifier));
    }

    [Fact]
    public void ANonPortalPickKeepsWhateverElseWasAuthenticatedExactlyAsBefore()
    {
        var operatorOnly = Operator;
        Assert.Same(operatorOnly, CircuitIdentities.Narrow(operatorOnly, portalSurface: false));

        var memberOnly = CircuitIdentities.Narrow(Member, portalSurface: false);
        Assert.False(memberOnly.Identity!.IsAuthenticated);
    }

    [Theory]
    [InlineData("/portal", true)]
    [InlineData("/portal/acme", true)]
    [InlineData("/PORTAL/acme/c/1?q=x", true)]
    [InlineData("https://platform.test/portal/acme", true)]
    [InlineData("https://platform.test/portal/acme/c/1?x=%2Fui", true)]
    [InlineData("/ui", false)]
    [InlineData("/ui?next=/portal/acme", false)]
    [InlineData("https://platform.test/", false)]
    [InlineData("https://platform.test/portals/acme", false)]
    [InlineData("https://platform.test/design", false)]
    [InlineData("", false)]
    public void OnlyPagesUnderThePortalPrefixCountAsThePortalSide(string uri, bool portal) =>
        Assert.Equal(portal, CircuitIdentities.IsPortalPath(uri));
}