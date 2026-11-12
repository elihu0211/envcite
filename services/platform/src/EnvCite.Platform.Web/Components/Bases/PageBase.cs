using System.Security.Claims;

using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Bases;

/// <summary>
/// 頁面的基底：導覽與一則頁面層級的錯誤（<see cref="Error"/>，由 <c>&lt;FormAlert&gt;</c> 顯示）。
/// 結果代碼轉成使用者語言的文字只有一條路：<see cref="Describe{T}"/>（代碼取自資源檔，沒有代碼時退回英文說明，再退回呼叫端給的句子）。
/// </summary>
public abstract class PageBase : PlatformComponentBase
{
    [Inject] protected NavigationManager Navigation { get; set; } = default!;

    protected string? Error { get; set; }

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