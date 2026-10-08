using System.Security.Claims;

using Microsoft.AspNetCore.Authorization;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Web.Auth;

/// <summary>Google 只證明身分；能不能進後台由這份白名單決定。</summary>
public sealed class OperatorAllowlist(IOptionsMonitor<UiOptions> options)
{
    public const string EmailVerifiedClaim = "email_verified";
    public const string HostedDomainClaim = "hd";

    public bool IsAllowed(ClaimsPrincipal user) => IsAllowed(user, options.CurrentValue);

    public static bool IsAllowed(ClaimsPrincipal user, UiOptions options)
    {
        var email = user.FindFirst(ClaimTypes.Email)?.Value;
        if (string.IsNullOrEmpty(email))
        {
            return false;
        }

        // 未驗證的 email 不可當身分憑據。缺 claim 視為未驗證。
        if (!string.Equals(user.FindFirst(EmailVerifiedClaim)?.Value, "true", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        if (!string.IsNullOrEmpty(options.AllowedHostedDomain)
            && !string.Equals(user.FindFirst(HostedDomainClaim)?.Value, options.AllowedHostedDomain, StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        return options.OperatorEmails.Contains(email, StringComparer.OrdinalIgnoreCase);
    }
}

public sealed class OperatorRequirement : IAuthorizationRequirement;

/// <summary>每次授權都重查白名單，所以從設定移除的人，下一個請求就失效，不必等 cookie 過期。</summary>
public sealed class OperatorHandler(OperatorAllowlist allowlist) : AuthorizationHandler<OperatorRequirement>
{
    protected override Task HandleRequirementAsync(AuthorizationHandlerContext context, OperatorRequirement requirement)
    {
        if (allowlist.IsAllowed(context.User))
        {
            context.Succeed(requirement);
        }

        return Task.CompletedTask;
    }
}