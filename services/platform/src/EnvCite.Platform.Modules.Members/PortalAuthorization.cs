using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 登入 cookie 裡放什麼：使用者 Id、角色、租戶，以及「還必須先改密碼」。授權之後只讀這些 claim，
/// 不再回頭問 Identity。租戶取自目前請求解析出的租戶（登入前由路徑決定）。
/// </summary>
internal sealed class PortalClaimsPrincipalFactory(
    UserManager<ApplicationUser> users, IOptions<IdentityOptions> options, IMultiTenantContextAccessor<TenantInfo> tenant,
    IOptions<MembersOptions> members)
    : UserClaimsPrincipalFactory<ApplicationUser>(users, options)
{
    protected override async Task<ClaimsIdentity> GenerateClaimsAsync(ApplicationUser user)
    {
        var identity = await base.GenerateClaimsAsync(user);
        identity.AddClaim(new Claim(PlatformClaimTypes.PortalUserId, user.Id));
        identity.AddClaim(new Claim(ClaimTypes.Role, user.Role));
        if (user.Email is { } email)
            identity.AddClaim(new Claim(ClaimTypes.Email, email));

        if (!string.IsNullOrWhiteSpace(user.DisplayName))
            identity.AddClaim(new Claim(ClaimTypes.GivenName, user.DisplayName));

        if (tenant.MultiTenantContext?.TenantInfo?.Identifier is { } identifier)
            identity.AddClaim(new Claim(PlatformClaimTypes.TenantIdentifier, identifier));

        if (user.MustChangePassword)
            identity.AddClaim(new Claim(PlatformClaimTypes.PasswordChangeRequired, "true"));

        if (PortalTwoFactor.RequiresTwoFactor(user.Role, members.Value) && !user.TwoFactorEnabled)
            identity.AddClaim(new Claim(PlatformClaimTypes.MfaEnrollmentRequired, "true"));

        return identity;
    }
}

internal sealed record PortalMemberRequirement(bool AllowPasswordChangePending, bool AllowMfaEnrollmentPending) : IAuthorizationRequirement;

/// <summary>
/// 一個入口 session 只對它簽發時的那個租戶有效：cookie 的 tenant claim 必須等於這個請求所屬的租戶。
/// 否則 A 租戶的人帶著 cookie 打 B 租戶的路徑，會被當成沒登入。「請求所屬的租戶」有兩個來源：
/// HTTP 請求是租戶解析的結果（環境租戶）；互動式 circuit 裡的頁面切換不是 HTTP 請求，沒有環境租戶，
/// 由 <c>AuthorizeRouteView</c> 把路由資料當授權資源傳進來（Routes 要設 <c>Resource="routeData"</c>），租戶取自路由值 <c>Tenant</c>。
/// 資源是路由資料時<b>只看路由值</b>、不看環境租戶；路由裡沒有租戶就拒絕。
/// </summary>
internal sealed class PortalMemberHandler(IMultiTenantContextAccessor<TenantInfo> tenant) : AuthorizationHandler<PortalMemberRequirement>
{
    /// <summary>路由裡租戶的參數名（<c>@page "/portal/{Tenant}/…"</c>）。</summary>
    internal const string RouteTenant = "Tenant";

    protected override Task HandleRequirementAsync(AuthorizationHandlerContext context, PortalMemberRequirement requirement)
    {
        var user = context.User;
        var sessionTenant = user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value;

        var valid = user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            && BelongsToRequestedTenant(context.Resource, sessionTenant)
            && (requirement.AllowPasswordChangePending || user.FindFirst(PlatformClaimTypes.PasswordChangeRequired) is null)
            && (requirement.AllowMfaEnrollmentPending || user.FindFirst(PlatformClaimTypes.MfaEnrollmentRequired) is null);

        if (valid)
            context.Succeed(requirement);

        return Task.CompletedTask;
    }

    private bool BelongsToRequestedTenant(object? resource, string? sessionTenant)
    {
        if (sessionTenant is null)
            return false;

        if (resource is Microsoft.AspNetCore.Components.RouteData route)
        {
            // 路由值是使用者打的網址：識別碼只有小寫英文、數字與連字號（建立時驗證），不分大小寫比對不會讓兩個租戶相撞。
            return route.RouteValues.TryGetValue(RouteTenant, out var value)
                && Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture) is { Length: > 0 } requested
                && string.Equals(requested, sessionTenant, StringComparison.OrdinalIgnoreCase);
        }

        var current = tenant.MultiTenantContext?.TenantInfo?.Identifier;
        return current is not null && string.Equals(current, sessionTenant, StringComparison.Ordinal);
    }
}

/// <summary>cookie 的導向：登入頁與改密碼頁都在同一個租戶的路徑下，所以導回「請求所屬租戶」的頁面。</summary>
internal static class PortalRedirects
{
    public static Task ToLogin(RedirectContext<CookieAuthenticationOptions> context)
    {
        // 沒有租戶的路徑不是入口的路徑；把 cookie 的導向留給預設行為會往不存在的 /Account/Login 去，所以直接 404。
        if (PortalPaths.TenantFrom(context.Request.Path) is not { } tenant)
        {
            context.Response.StatusCode = StatusCodes.Status404NotFound;
            return Task.CompletedTask;
        }

        var returnUrl = context.Request.Path + context.Request.QueryString;
        context.Response.Redirect($"{PortalPaths.Login(tenant)}?returnUrl={Uri.EscapeDataString(returnUrl)}");
        return Task.CompletedTask;
    }

    /// <summary>
    /// 已登入但不符：還必須先改密碼 → 改密碼頁；密碼改過了、但角色要求的兩步驟驗證還沒註冊 → 註冊頁；
    /// 否則（例如別的租戶的 cookie）→ 這個租戶的登入頁。先改密碼再註冊：註冊前要先是一個只有本人知道的密碼。
    /// </summary>
    public static Task ToAccessDenied(RedirectContext<CookieAuthenticationOptions> context)
    {
        if (PortalPaths.TenantFrom(context.Request.Path) is not { } tenant)
        {
            context.Response.StatusCode = StatusCodes.Status404NotFound;
            return Task.CompletedTask;
        }

        var user = context.HttpContext.User;
        var sessionIsHere = string.Equals(user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value, tenant, StringComparison.Ordinal);
        var target = !sessionIsHere ? PortalPaths.Login(tenant)
            : user.FindFirst(PlatformClaimTypes.PasswordChangeRequired) is not null ? PortalPaths.Password(tenant)
            : user.FindFirst(PlatformClaimTypes.MfaEnrollmentRequired) is not null ? $"{PortalPaths.Home(tenant)}/security"
            : PortalPaths.Login(tenant);
        context.Response.Redirect(target);
        return Task.CompletedTask;
    }
}

/// <summary>
/// 每個請求都檢查 session 的安全戳記還對不對：改密碼、營運者重設密碼都會換戳記，其他裝置的 session 立刻失效。
/// 不用 Identity 內建的 <c>SecurityStampValidator</c>：它驗證失敗時會呼叫 <c>SignInManager.SignOutAsync</c>，
/// 去登出一整組我們沒有註冊的 Identity cookie scheme（外部登入、雙因素），結果是 500。這裡只登出入口自己的 cookie。
/// 代價是每個入口請求多一次以主鍵查使用者；入口的使用者數量與流量都小，值得換立即失效。
/// </summary>
internal static class PortalSessionValidator
{
    public static async Task ValidateAsync(CookieValidatePrincipalContext context)
    {
        if (context.Principal is null)
            return;

        var signIn = context.HttpContext.RequestServices.GetRequiredService<SignInManager<ApplicationUser>>();

        if (!await IsValidAsync(signIn, context.Principal))
        {
            context.RejectPrincipal();
            await context.HttpContext.SignOutAsync(PlatformAuth.PortalCookieScheme);
        }
    }

    /// <summary>
    /// 帳號還在、沒停用、安全戳記沒變。停用也直接檢查旗標，不只靠安全戳記：停用的人，每一個請求都被當作沒登入。
    /// 要在已綁好租戶的 scope 裡呼叫（使用者表有租戶過濾）：HTTP 請求是租戶解析的結果，circuit 的重驗是 <c>PortalSessions</c>。
    /// </summary>
    internal static async Task<bool> IsValidAsync(SignInManager<ApplicationUser> signIn, ClaimsPrincipal principal) =>
        await signIn.ValidateSecurityStampAsync(principal) is { Disabled: false };
}

/// <summary>
/// 互動式 circuit 的入口成員重驗（<see cref="IPortalSessions"/>）：沒有 HTTP 請求，所以依 principal 的租戶 claim 開租戶 scope，
/// 在裡面做與 cookie 每個請求相同的檢查（<see cref="PortalSessionValidator.IsValidAsync"/>）。
/// </summary>
internal sealed class PortalSessions(TenantScopes tenants) : IPortalSessions
{
    public async Task<bool> IsStillValidAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (user.Identity?.IsAuthenticated != true
            || user.FindFirst(PlatformClaimTypes.PortalUserId) is null
            || user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is not { Length: > 0 } tenantIdentifier)
        {
            return false;
        }

        var outcome = await tenants.InByIdentifierAsync<bool>(
            tenantIdentifier,
            async scope => AdminOutcome.Ok(await PortalSessionValidator.IsValidAsync(scope.Get<SignInManager<ApplicationUser>>(), user)));

        return outcome.IsOk && outcome.Value;
    }
}