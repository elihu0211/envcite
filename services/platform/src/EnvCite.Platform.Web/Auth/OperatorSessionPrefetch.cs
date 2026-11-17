using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Components.Server.Circuits;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// circuit 開啟、第一次渲染之前，先把營運者的授權查好（<see cref="IOperatorSessions.PrefetchAsync"/>）。
/// 後台每一頁的授權都要查一次資料庫；circuit 接手預先渲染的頁面時，若第一次渲染才去查，外殼會先畫出來、
/// 頁面內容晚一趟資料庫才到，這段時間內容區是空的。查詢本身沒有省，只是移到渲染之前，讓第一批就帶著內容。
/// 入口成員的授權只讀 cookie 裡的 claim、不查資料庫，不需要這一步（<c>PrefetchAsync</c> 對沒有營運者 Id 的身分直接略過）。
/// </summary>
internal sealed class OperatorSessionPrefetch(AuthenticationStateProvider authentication, IOperatorSessions sessions) : CircuitHandler
{
    public override async Task OnCircuitOpenedAsync(Circuit circuit, CancellationToken cancellationToken)
    {
        // 只讀這一次、不訂閱變更（BL0013）：預取以這個 principal 物件為鍵，登入狀態之後若換了新物件，只是沒命中、退回照常查資料庫，不會用到舊結果。
#pragma warning disable BL0013
        var state = await authentication.GetAuthenticationStateAsync();
#pragma warning restore BL0013
        await sessions.PrefetchAsync(state.User, cancellationToken);
    }
}