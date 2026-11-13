using System.Security.Claims;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.JSInterop;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Bases;

/// <summary>一個對話框送出的結果：成功帶著要交給呼叫端的值，失敗帶著要在對話框裡說明的句子。</summary>
public sealed record DialogOutcome(object? Value, string? Failure)
{
    public static DialogOutcome From<T>(AdminOutcome<T> outcome, string fallback) =>
        outcome.IsOk ? new(outcome.Value, null) : new(null, outcome.Localized() ?? fallback);
}

/// <summary>
/// 營運者後台所有對話框的基底：MudBlazor 對話框的實體、鎖住對話框（只剩確認鈕能關掉）與複製到剪貼簿
/// （被瀏覽器擋住時，非安全環境，不報錯：使用者仍可手動選取）。
/// </summary>
public abstract class DialogBase : PlatformComponentBase
{
    [CascadingParameter] protected IMudDialogInstance MudDialog { get; set; } = default!;

    [Inject] protected IJSRuntime Js { get; set; } = default!;

    protected bool Copied { get; private set; }

    /// <summary>鎖住對話框：不能用 Esc、點背景或關閉鈕關掉，之後只有確認鈕。</summary>
    protected Task LockAsync() =>
        MudDialog.SetOptionsAsync(MudDialog.Options with { BackdropClick = false, CloseOnEscapeKey = false, CloseButton = false });

    protected async Task CopyAsync(string text)
    {
        try
        {
            await Js.InvokeVoidAsync("navigator.clipboard.writeText", text);
            Copied = true;
        }
        catch (JSException)
        {
            Copied = false;
        }
    }
}

/// <summary>
/// 有欄位、要送出的對話框：對話框內的錯誤、送出期間停用（<see cref="Busy"/>）、Enter 送出，
/// 以及一條共用的送出流程 <see cref="CompleteAsync"/>（取得操作者、呼叫服務、成功就關閉並交回結果、失敗就說明；例外也說明，不讓電路掛掉）。
/// 對話框自己只剩欄位與「呼叫哪個服務」。
/// </summary>
public abstract class FormDialogBase : DialogBase
{
    /// <summary>目前的登入狀態：由根部的 <c>CascadingAuthenticationState</c> 提供，每次送出都讀最新的值。</summary>
    [CascadingParameter] private Task<AuthenticationState> AuthenticationState { get; set; } = default!;

    protected string? Error { get; set; }

    protected bool Busy { get; private set; }

    /// <summary>對話框的「送出」：由 <see cref="OnKeyUp"/>（Enter）與按鈕呼叫。</summary>
    protected abstract Task SubmitAsync();

    protected async Task OnKeyUp(KeyboardEventArgs e)
    {
        if (e.Key == "Enter" && !Busy)
            await SubmitAsync();
    }

    /// <param name="action">以目前的操作者呼叫服務，回傳 <see cref="DialogOutcome"/>（用 <see cref="DialogOutcome.From{T}"/> 轉換服務的結果）。</param>
    /// <param name="exceptionText">例外時顯示的句子（後面附上例外型別，不附訊息）。</param>
    /// <param name="close">成功時是否關閉對話框並交回結果；兩階段的對話框（簽發 API key）傳 false，自己接著顯示下一階段。</param>
    /// <returns>成功的結果；失敗、例外或正在送出為 null。</returns>
    protected async Task<DialogOutcome?> CompleteAsync(Func<ClaimsPrincipal, Task<DialogOutcome>> action, string exceptionText, bool close = true)
    {
        if (Busy)
            return null;

        (Busy, Error) = (true, null);
        try
        {
            var outcome = await action((await AuthenticationState).User);
            if (outcome.Failure is { } failure)
            {
                Error = failure;
                return null;
            }

            if (close)
                MudDialog.Close(DialogResult.Ok(outcome.Value));

            return outcome;
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            Error = $"{exceptionText} ({exception.GetType().Name})";
            return null;
        }
        finally
        {
            Busy = false;
        }
    }
}

/// <summary>
/// 只顯示一次的機密（一次性密碼）的對話框基底：開啟就鎖住，只能按確認。
/// </summary>
public abstract class SecretDialogBase : DialogBase
{
    protected override Task OnInitializedAsync() => LockAsync();
}