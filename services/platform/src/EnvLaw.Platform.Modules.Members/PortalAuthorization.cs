using System.Security.Claims;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.Members;

/// <summary>
/// 登入 cookie 裡放什麼：使用者 Id、角色、租戶，以及「還必須先改密碼」。授權之後只讀這些 claim，
/// 不再回頭問 Identity。租戶取自目前請求解析出的租戶（登入前由路徑決定）。
/// </summary>
internal sealed class PortalClaimsPrincipalFactory(
    UserManager<ApplicationUser> users, IOptions<IdentityOptions> options, IMultiTenantContextAccessor<TenantInfo> tenant)
    : UserClaimsPrincipalFactory<ApplicationUser>(users, options)
{
    protected override async Task<ClaimsIdentity> GenerateClaimsAsync(ApplicationUser user)
    {
        var identity = await base.GenerateClaimsAsync(user);
        identity.AddClaim(new Claim(PlatformClaimTypes.PortalUserId, user.Id));
        identity.AddClaim(new Claim(ClaimTypes.Role, user.Role));
        if (user.Email is { } email)
        {
            identity.AddClaim(new Claim(ClaimTypes.Email, email));
        }

        if (!string.IsNullOrWhiteSpace(user.DisplayName))
        {
            identity.AddClaim(new Claim(ClaimTypes.GivenName, user.DisplayName));
        }

        if (tenant.MultiTenantContext?.TenantInfo?.Identifier is { } identifier)
        {
            identity.AddClaim(new Claim(PlatformClaimTypes.TenantIdentifier, identifier));
        }

        if (user.MustChangePassword)
        {
            identity.AddClaim(new Claim(PlatformClaimTypes.PasswordChangeRequired, "true"));
        }

        return identity;
    }
}

internal sealed record PortalMemberRequirement(bool AllowPasswordChangePending) : IAuthorizationRequirement;

/// <summary>
/// 一個入口 session 只對它簽發時的那個租戶有效：cookie 的 tenant claim 必須等於這個請求路徑解析出的租戶。
/// 否則 A 租戶的人帶著 cookie 打 B 租戶的路徑，會被當成沒登入。
/// </summary>
internal sealed class PortalMemberHandler(IMultiTenantContextAccessor<TenantInfo> tenant) : AuthorizationHandler<PortalMemberRequirement>
{
    protected override Task HandleRequirementAsync(AuthorizationHandlerContext context, PortalMemberRequirement requirement)
    {
        var user = context.User;
        var current = tenant.MultiTenantContext?.TenantInfo?.Identifier;
        var sessionTenant = user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value;

        var valid = user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            && current is not null
            && string.Equals(current, sessionTenant, StringComparison.Ordinal)
            && (requirement.AllowPasswordChangePending || user.FindFirst(PlatformClaimTypes.PasswordChangeRequired) is null);

        if (valid)
        {
            context.Succeed(requirement);
        }

        return Task.CompletedTask;
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

    /// <summary>已登入但不符：還必須先改密碼 → 改密碼頁；否則（例如別的租戶的 cookie）→ 這個租戶的登入頁。</summary>
    public static Task ToAccessDenied(RedirectContext<CookieAuthenticationOptions> context)
    {
        if (PortalPaths.TenantFrom(context.Request.Path) is not { } tenant)
        {
            context.Response.StatusCode = StatusCodes.Status404NotFound;
            return Task.CompletedTask;
        }

        var pendingHere = context.HttpContext.User.FindFirst(PlatformClaimTypes.PasswordChangeRequired) is not null
            && string.Equals(context.HttpContext.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value, tenant, StringComparison.Ordinal);
        context.Response.Redirect(pendingHere ? PortalPaths.Password(tenant) : PortalPaths.Login(tenant));
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
        {
            return;
        }

        var signIn = context.HttpContext.RequestServices.GetRequiredService<SignInManager<ApplicationUser>>();
        if (await signIn.ValidateSecurityStampAsync(context.Principal) is null)
        {
            context.RejectPrincipal();
            await context.HttpContext.SignOutAsync(PlatformAuth.PortalCookieScheme);
        }
    }
}