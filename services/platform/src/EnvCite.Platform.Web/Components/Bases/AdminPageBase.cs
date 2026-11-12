using System.Security.Claims;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Bases;

/// <summary>
/// 營運者後台的互動式頁面（Blazor Server 電路）的基底：目前的操作者、對話框、一則操作結果（<see cref="Notice"/>）與「動作期間停用按鈕」。
/// 動作一律走 <see cref="RunAsync"/>：連點只算一次、開始時清掉上一則結果、例外不吞掉也不讓電路掛掉，在頁面上說明。
/// </summary>
public abstract class AdminPageBase : PageBase
{
    /// <summary>後台對話框的寬度：窄、可關閉。</summary>
    protected static readonly DialogOptions Narrow = new() { MaxWidth = MaxWidth.Small, FullWidth = true, CloseButton = true };

    [Inject] protected AuthenticationStateProvider Auth { get; set; } = default!;

    [Inject] protected IDialogService Dialogs { get; set; } = default!;

    protected string? Notice { get; private set; }

    protected Severity NoticeSeverity { get; private set; } = Severity.Success;

    protected bool Busy { get; private set; }

    protected async Task<ClaimsPrincipal> ActorAsync() => (await Auth.GetAuthenticationStateAsync()).User;

    protected void Succeed(string message) => (Notice, NoticeSeverity) = (message, Severity.Success);

    protected void Fail(string message) => (Notice, NoticeSeverity) = (message, Severity.Error);

    protected void ClearNotice() => Notice = null;

    protected async Task RunAsync(Func<Task> action)
    {
        if (Busy)
        {
            return;
        }

        (Busy, Notice) = (true, null);
        try
        {
            await action();
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            Fail(L["操作失敗，請稍後再試。({0})", exception.GetType().Name]);
        }
        finally
        {
            Busy = false;
        }
    }
}