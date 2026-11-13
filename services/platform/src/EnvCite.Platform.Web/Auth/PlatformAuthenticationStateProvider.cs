using System.Security.Claims;

using Microsoft.AspNetCore.Components;
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
/// 啟動時的身分也在這裡定：連線層可能同時認出營運者與入口成員（兩張 cookie 都在瀏覽器裡），這條 circuit 是為哪一頁開的，
/// 看它的第一個網址（<see cref="CircuitIdentities.Narrow"/>）；之後切頁不換身分，所以兩邊的頁面之間不放會在 circuit 裡切頁的連結
/// （後台只顯示入口網址的文字與複製鈕，入口的其餘頁面是靜態頁，切過去是整頁載入）。
/// </summary>
internal sealed class PlatformAuthenticationStateProvider(
    ILoggerFactory loggerFactory, IOperatorSessions operators, IPortalSessions portal, NavigationManager navigation)
    : RevalidatingServerAuthenticationStateProvider(loggerFactory), IHostEnvironmentAuthenticationStateProvider
{
    protected override TimeSpan RevalidationInterval => TimeSpan.FromMinutes(1);

    /// <summary>框架在 circuit 啟動與伺服器端渲染時，把連線的使用者交進來；這裡先依第一個網址挑出要用的那一個身分。</summary>
    public new void SetAuthenticationState(Task<AuthenticationState> authenticationStateTask) =>
        base.SetAuthenticationState(NarrowAsync(authenticationStateTask));

    private async Task<AuthenticationState> NarrowAsync(Task<AuthenticationState> connection)
    {
        var state = await connection;
        return new AuthenticationState(CircuitIdentities.Narrow(state.User, StartsOnPortalPage()));
    }

    /// <summary>
    /// circuit 的第一個網址是不是入口頁。網址還沒設定好（框架的順序不保證，或沒有導覽的主機）就當不是：維持連線層
    /// 加入入口身分之前的行為——只有營運者，不會因為讀不到網址而把入口成員放進後台。
    /// </summary>
    private bool StartsOnPortalPage()
    {
        try
        {
            return CircuitIdentities.IsPortalPath(navigation.Uri);
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }

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