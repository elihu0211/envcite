using System.Security.Claims;

namespace EnvCite.Platform.Modules.Canon;

/// <summary>入口這次請求的呼叫者。不是入口成員（含未登入、營運者）時沒有租戶。</summary>
internal static class PortalCaller
{
    public static string? Tenant(ClaimsPrincipal user) =>
        user.Identity?.IsAuthenticated == true && user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            ? user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is { Length: > 0 } tenant ? tenant : null
            : null;
}