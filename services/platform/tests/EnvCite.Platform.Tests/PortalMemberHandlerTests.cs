using System.Security.Claims;

using EnvCite.Platform.Modules.Members;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authorization;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口授權的每一道檢查各自單獨測：端對端的測試裡，使用者表的租戶 filter 會先擋下別租戶的 cookie，
/// 把這裡的檢查蓋住——拿掉它端對端測試照樣通過。所以直接測 handler，讓每一層都有自己的證明。
/// </summary>
public sealed class PortalMemberHandlerTests
{
    private static AsyncLocalMultiTenantContextAccessor<TenantInfo> Accessor(string? resolvedTenant)
    {
        var accessor = new AsyncLocalMultiTenantContextAccessor<TenantInfo>();
        ((IMultiTenantContextSetter)accessor).MultiTenantContext = new MultiTenantContext<TenantInfo>(
            resolvedTenant is null ? null : new TenantInfo { Id = "id", Identifier = resolvedTenant });
        return accessor;
    }

    private static ClaimsPrincipal Member(string? tenant, bool passwordChangePending = false, bool withUserId = true, bool mfaEnrollmentPending = false)
    {
        var claims = new List<Claim>();
        if (withUserId)
            claims.Add(new Claim(PlatformClaimTypes.PortalUserId, "user-1"));

        if (tenant is not null)
            claims.Add(new Claim(PlatformClaimTypes.TenantIdentifier, tenant));

        if (passwordChangePending)
            claims.Add(new Claim(PlatformClaimTypes.PasswordChangeRequired, "true"));

        if (mfaEnrollmentPending)
            claims.Add(new Claim(PlatformClaimTypes.MfaEnrollmentRequired, "true"));

        return new ClaimsPrincipal(new ClaimsIdentity(claims, "test"));
    }

    private static async Task<bool> SucceedsAsync(
        string? resolvedTenant, ClaimsPrincipal user, bool allowPending, bool allowMfaEnrollment = false, object? resource = null)
    {
        var requirement = new PortalMemberRequirement(allowPending, allowMfaEnrollment);
        var context = new AuthorizationHandlerContext([requirement], user, resource);
        await new PortalMemberHandler(Accessor(resolvedTenant)).HandleAsync(context);
        return context.HasSucceeded;
    }

    // RouteData 要求頁面型別是元件；內容不重要。
    private sealed class AnyPage : Microsoft.AspNetCore.Components.ComponentBase;

    /// <summary>互動式 circuit 裡 <c>AuthorizeRouteView</c> 傳進來的授權資源：路由資料，路由值裡的 <c>Tenant</c> 是這一頁所屬的租戶。</summary>
    private static Microsoft.AspNetCore.Components.RouteData Route(string? tenant) =>
        new(typeof(AnyPage), tenant is null
            ? new Dictionary<string, object?>()
            : new Dictionary<string, object?> { [PortalMemberHandler.RouteTenant] = tenant });

    // circuit 裡沒有租戶解析，環境租戶是空的：授權只看路由資料。
    [Fact]
    public async Task InACircuitTheRouteTenantDecidesAndNoAmbientTenantIsNeeded() =>
        Assert.True(await SucceedsAsync(resolvedTenant: null, Member("acme"), allowPending: false, resource: Route("acme")));

    // 資源是路由資料時不能再看環境租戶：就算環境租戶剛好等於 cookie 的租戶，路由屬於別的租戶也要拒絕。
    [Fact]
    public async Task ARouteOfAnotherTenantFailsEvenWhenTheAmbientTenantMatchesTheSession() =>
        Assert.False(await SucceedsAsync("acme", Member("acme"), allowPending: true, resource: Route("other")));

    [Fact]
    public async Task ARouteWithoutATenantFailsEvenWhenTheAmbientTenantMatchesTheSession()
    {
        Assert.False(await SucceedsAsync("acme", Member("acme"), allowPending: true, resource: Route(null)));
        Assert.False(await SucceedsAsync("acme", Member("acme"), allowPending: true, resource: Route(string.Empty)));
    }

    // 路由值是使用者打的網址（可能大小寫不同）；識別碼只有小寫，不分大小寫比對不會讓兩個租戶相撞。
    [Fact]
    public async Task TheRouteTenantIsMatchedIgnoringTheCaseOfTheUrl() =>
        Assert.True(await SucceedsAsync(null, Member("acme"), allowPending: false, resource: Route("ACME")));

    [Fact]
    public async Task ASessionWithoutATenantClaimFailsInACircuitToo() =>
        Assert.False(await SucceedsAsync(null, Member(null), allowPending: true, resource: Route("acme")));

    [Fact]
    public async Task EveryOtherCheckStillAppliesInACircuit()
    {
        Assert.False(await SucceedsAsync(null, Member("acme", withUserId: false), allowPending: true, resource: Route("acme")));
        Assert.False(await SucceedsAsync(null, Member("acme", passwordChangePending: true), allowPending: false, resource: Route("acme")));
        Assert.True(await SucceedsAsync(null, Member("acme", passwordChangePending: true), allowPending: true, resource: Route("acme")));
        Assert.False(await SucceedsAsync(null, Member("acme", mfaEnrollmentPending: true), allowPending: false, resource: Route("acme")));
        Assert.True(await SucceedsAsync(null, Member("acme", mfaEnrollmentPending: true), allowPending: false, allowMfaEnrollment: true, resource: Route("acme")));
    }

    // HTTP 請求的資源不是路由資料（端點或 HttpContext）：照舊以環境租戶為準，而且仍是精確比對。
    [Fact]
    public async Task AnyOtherResourceStillUsesTheAmbientTenantExactly()
    {
        Assert.True(await SucceedsAsync("acme", Member("acme"), allowPending: false, resource: new object()));
        Assert.False(await SucceedsAsync("acme", Member("ACME"), allowPending: false, resource: new object()));
        Assert.False(await SucceedsAsync(null, Member("acme"), allowPending: false, resource: new object()));
    }

    // 路由資料只在 Routes 把它當授權資源傳進來時才會到 handler：少了 Resource，circuit 裡的入口頁一律被拒。
    [Fact]
    public void TheRouterPassesTheRouteDataAsTheAuthorizationResource()
    {
        var routes = File.ReadAllText(Path.Combine(WebPath(), "Components", "Routes.razor"));

        Assert.Contains("<AuthorizeRouteView RouteData=\"routeData\" Resource=\"routeData\"", routes, StringComparison.Ordinal);
    }

    private static string WebPath([System.Runtime.CompilerServices.CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web"));

    [Fact]
    public async Task AMemberOfTheResolvedTenantPasses() =>
        Assert.True(await SucceedsAsync("acme", Member("acme"), allowPending: false));

    [Fact]
    public async Task ASessionForAnotherTenantFailsEvenWithEveryOtherClaimInOrder() =>
        Assert.False(await SucceedsAsync("acme", Member("other"), allowPending: true));

    [Fact]
    public async Task TenantNamesAreComparedExactly() =>
        Assert.False(await SucceedsAsync("acme", Member("ACME"), allowPending: true));

    [Fact]
    public async Task WhenNoTenantWasResolvedNothingPasses() =>
        Assert.False(await SucceedsAsync(null, Member("acme"), allowPending: true));

    [Fact]
    public async Task ASessionWithoutATenantClaimFails() =>
        Assert.False(await SucceedsAsync("acme", Member(null), allowPending: true));

    [Fact]
    public async Task APrincipalThatIsNotAPortalMemberFails() =>
        Assert.False(await SucceedsAsync("acme", Member("acme", withUserId: false), allowPending: true));

    [Fact]
    public async Task APendingPasswordChangeOnlyOpensThePagesThatAllowIt()
    {
        Assert.False(await SucceedsAsync("acme", Member("acme", passwordChangePending: true), allowPending: false));
        Assert.True(await SucceedsAsync("acme", Member("acme", passwordChangePending: true), allowPending: true));
    }

    [Fact]
    public async Task ARequiredButNotYetEnrolledSessionOnlyOpensTheEnrollmentPage()
    {
        var notEnrolled = Member("acme", mfaEnrollmentPending: true);

        Assert.False(await SucceedsAsync("acme", notEnrolled, allowPending: false, allowMfaEnrollment: false)); // 入口其餘頁面
        Assert.True(await SucceedsAsync("acme", notEnrolled, allowPending: false, allowMfaEnrollment: true)); // 註冊頁
    }

    [Fact]
    public async Task EnrollmentDoesNotComeBeforeChangingTheOperatorIssuedPassword()
    {
        // 註冊前先要有一個只有本人知道的密碼：兩者都還沒做的人，註冊頁也不開。
        var both = Member("acme", passwordChangePending: true, mfaEnrollmentPending: true);

        Assert.False(await SucceedsAsync("acme", both, allowPending: false, allowMfaEnrollment: true));
    }
}