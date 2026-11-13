using System.Security.Claims;

using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Components.Server;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// Blazor Server 的 circuit 是一條長壽命的 SignalR 連線：登入狀態在連線建立時就定了，之後的點擊不是 HTTP 請求，
/// 不會再經過授權。所以停用一位營運者，他已經開著的分頁在 HTTP 請求上立刻被擋，但分頁裡的操作要等到下一次切頁
/// 才重新授權。這裡照微軟文件的做法（<see cref="RevalidatingServerAuthenticationStateProvider"/>）每分鐘重驗一次，
/// 帳號失效就把 circuit 的登入狀態換成匿名，頁面隨即換成「沒有權限」。
/// </summary>
internal sealed class OperatorAuthenticationStateProvider(ILoggerFactory loggerFactory, IOperatorSessions sessions)
    : RevalidatingServerAuthenticationStateProvider(loggerFactory)
{
    protected override TimeSpan RevalidationInterval => TimeSpan.FromMinutes(1);

    protected override Task<bool> ValidateAuthenticationStateAsync(AuthenticationState authenticationState, CancellationToken cancellationToken) =>
        IsStillValidAsync(authenticationState.User, cancellationToken);

    /// <summary>匿名沒有東西可重驗（登入頁、說明頁）；已登入的要帳號仍在、沒被停用、安全戳記沒變。</summary>
    internal async Task<bool> IsStillValidAsync(ClaimsPrincipal user, CancellationToken cancellationToken) =>
        user.Identity?.IsAuthenticated != true || await sessions.FindAsync(user, cancellationToken) is not null;
}