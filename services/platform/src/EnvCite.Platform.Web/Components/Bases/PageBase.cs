using System.Security.Claims;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Bases;

/// <summary>
/// 頁面的基底：導覽與一則頁面層級的錯誤（<see cref="Error"/>，由 <c>&lt;FormAlert&gt;</c> 顯示）。
/// 結果代碼轉成使用者語言的文字只有一條路：<see cref="Describe{T}"/>（代碼取自資源檔，沒有代碼時退回英文說明，再退回呼叫端給的句子）。
/// 載入資料的頁面用 <see cref="LoadingAsync"/> 包住查詢，狀態交給 <c>&lt;LoadedView&gt;</c> 畫（失敗、載入中、查無、有資料）。
/// </summary>
public abstract class PageBase : NavigableComponentBase
{
    protected string? Error { get; set; }

    /// <summary>正在載入資料。<see cref="LoadingAsync"/> 開始時為 true、結束時為 false；已有現成資料（預先渲染的快照）時由頁面自己設為 false。</summary>
    protected bool Loading { get; set; }

    /// <summary>
    /// 載入資料的流程：開始時清掉上一次的錯誤並標示載入中，查詢失敗不讓頁面或電路掛掉，只把例外型別放進 <see cref="Error"/>
    /// （訊息可能帶內部細節，不顯示）。取消不算失敗，照常往外丟。
    /// </summary>
    protected async Task LoadingAsync(Func<Task> load)
    {
        (Loading, Error) = (true, null);
        try
        {
            await load();
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            Error = exception.GetType().Name;
        }
        finally
        {
            Loading = false;
        }
    }

    protected static string Describe<T>(AdminOutcome<T> outcome, string fallback) => outcome.Localized() ?? fallback;
}

/// <summary>
/// 靜態渲染的頁面（沒有 circuit）的基底：取得這個請求的 <see cref="HttpContext"/>。登入、啟用、改密碼與客戶入口都是。
/// 互動式的後台頁面沒有 HttpContext，用 <see cref="AdminPageBase"/>。
/// </summary>
public abstract class StaticPageBase : PageBase
{
    [CascadingParameter] protected HttpContext HttpContext { get; set; } = default!;

    protected ClaimsPrincipal User => HttpContext.User;
}

/// <summary>
/// 有一張表單的靜態頁面：表單的內容（<see cref="Input"/>）由框架從 POST 填進來，第一次請求（GET）時建一份空的。
/// 衍生頁面若覆寫 <c>OnInitialized</c>，要先呼叫 <c>base.OnInitialized()</c>。
/// 一頁有多張表單的（成員、兩步驟驗證設定），第一張用 <see cref="Input"/>，其餘自己宣告；表單之間靠 <c>FormName</c> 分辨。
/// </summary>
public abstract class FormPageBase<TForm> : StaticPageBase
    where TForm : class, new()
{
    [SupplyParameterFromForm] protected TForm Input { get; set; } = default!;

    protected override void OnInitialized() => Input ??= new();
}

/// <summary>客戶入口的頁面：路徑上的租戶（<c>/portal/{Tenant}/…</c>）。</summary>
public abstract class PortalPageBase : StaticPageBase
{
    [Parameter] public string Tenant { get; set; } = string.Empty;
}

/// <summary>客戶入口、有一張表單的頁面。</summary>
public abstract class PortalFormPageBase<TForm> : FormPageBase<TForm>
    where TForm : class, new()
{
    [Parameter] public string Tenant { get; set; } = string.Empty;
}

/// <summary>
/// 客戶入口的**互動式**頁面（Interactive Server，工作區）：路徑上的租戶，與已驗證的使用者。
/// 沒有 <c>HttpContext</c>（circuit 裡的頁面切換不是 HTTP 請求）：身分一律取自 <see cref="UserAsync"/>，
/// 租戶只在路由用來決定「這一頁屬於哪個租戶」，所有領域動作的租戶都取自身分（見 <c>IAskService</c>）。
/// </summary>
public abstract class WorkspacePageBase : PageBase
{
    [Parameter] public string Tenant { get; set; } = string.Empty;

    [CascadingParameter] private Task<AuthenticationState>? AuthenticationState { get; set; }

    protected async Task<ClaimsPrincipal> UserAsync() =>
        AuthenticationState is null ? new ClaimsPrincipal(new ClaimsIdentity()) : (await AuthenticationState).User;
}