using System.Security.Claims;

using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Components.Server;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// Blazor Server 的 circuit 是一條長壽命的 SignalR 連線：登入狀態在連線建立時就定了，之後的點擊不是 HTTP 請求，
/// 不會再經過授權。所以停用一位營運者或入口成員，他已經開著的分頁在 HTTP 請求上立刻被擋，但分頁裡的操作要等到下一次切頁
/// 才重新授權。這裡照微軟文件的做法（<see cref="RevalidatingServerAuthenticationStateProvider"/>）每分鐘重驗一次，
/// 帳號失效就把 circuit 的登入狀態換成匿名，頁面隨即換成「沒有權限」。
/// 兩種身分各驗各的：入口成員（有 <see cref="PlatformClaimTypes.PortalUserId"/>）走 <see cref="IPortalSessions"/>，
/// 其餘已登入者一律當營運者驗（<see cref="IOperatorSessions"/>）——不是營運者的 principal 在那裡找不到，結果是失效，不是放行。
/// </summary>
internal sealed class PlatformAuthenticationStateProvider(
    ILoggerFactory loggerFactory, IOperatorSessions operators, IPortalSessions portal)
    : RevalidatingServerAuthenticationStateProvider(loggerFactory)
{
    protected override TimeSpan RevalidationInterval => TimeSpan.FromMinutes(1);

    protected override Task<bool> ValidateAuthenticationStateAsync(AuthenticationState authenticationState, CancellationToken cancellationToken) =>
        IsStillValidAsync(authenticationState.User, cancellationToken);

    /// <summary>匿名沒有東西可重驗（登入頁、說明頁）；已登入的要帳號仍在、沒被停用、安全戳記沒變。</summary>
    internal async Task<bool> IsStillValidAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (user.Identity?.IsAuthenticated != true)
            return true;

        return user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            ? await portal.IsStillValidAsync(user, cancellationToken)
            : await operators.FindAsync(user, cancellationToken) is not null;
    }
}