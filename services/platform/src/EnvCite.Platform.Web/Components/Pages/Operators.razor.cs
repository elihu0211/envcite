using EnvCite.Platform.Web.Auth;
using EnvCite.Platform.Web.Components.Dialogs;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Pages;

public sealed partial class Operators(IOperatorAdmin admin, IMailSender mail)
{
    private IReadOnlyList<OperatorInfo>? _operators;
    private string? _selfId;
    private string? _search;

    private IMailSender Mail => mail;

    /// <summary>預先渲染查到的清單交給互動渲染，不再查一次。重新連線不還原：資料可能已經變了，要重查。</summary>
    [PersistentState(RestoreBehavior = RestoreBehavior.SkipLastSnapshot)]
    public IReadOnlyList<OperatorInfo>? Prerendered { get; set; }

    protected override async Task OnInitializedAsync()
    {
        var user = await ActorAsync();
        _selfId = user.FindFirst(PlatformClaimTypes.OperatorId)?.Value;
        if (Prerendered is not null)
        {
            (_operators, Prerendered) = (Prerendered, null);
            return;
        }

        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        Error = null;
        _operators = null;
        try
        {
            var listed = await admin.ListAsync(CancellationToken.None);
            if (!listed.IsOk)
            {
                Error = listed.Localized() ?? L["載入失敗。"];
                _operators = [];
                return;
            }

            _operators = listed.Value;
            if (!RendererInfo.IsInteractive)
            {
                Prerendered = _operators;
            }
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            Error = exception.GetType().Name;
            _operators = [];
        }
    }

    private bool IsSelf(OperatorInfo op) => string.Equals(op.Id, _selfId, StringComparison.OrdinalIgnoreCase);

    private bool Matches(OperatorInfo op) =>
        string.IsNullOrWhiteSpace(_search) || op.Email.Contains(_search, StringComparison.OrdinalIgnoreCase);

    private async Task CreateAsync()
    {
        var dialog = await Dialogs.ShowAsync<CreateOperatorDialog>(L["新增營運者"], Narrow);
        var result = await dialog.Result;
        if (result is { Canceled: false, Data: IssuedOperatorPassword issued })
        {
            await RevealAsync(issued);
            await ReloadQuietlyAsync();
        }
        else if (result is { Canceled: false, Data: OperatorInvitation invitation })
        {
            await ReloadQuietlyAsync();
            if (invitation.Mail == MailDelivery.Sent)
            {
                Succeed(L["已寄出邀請信給 {0}。", invitation.Operator.Email]);
            }
            else
            {
                Fail(L["邀請信寄送失敗，{0} 的帳號已建立。請按「重寄邀請」，或用「重設密碼」取得暫時密碼。", invitation.Operator.Email]);
            }
        }
    }

    private async Task ResendAsync(OperatorInfo op)
    {
        await RunAsync(async () =>
        {
            var sent = await admin.ResendInvitationAsync(await ActorAsync(), op.Id, CancellationToken.None);
            if (!sent.IsOk)
            {
                Fail(sent.Localized() ?? L["寄信失敗，請稍後再試。"]);
                return;
            }

            await ReloadQuietlyAsync();
            if (sent.Value!.Mail == MailDelivery.Sent)
            {
                Succeed(op.Invited ? L["已重寄邀請信給 {0}。", op.Email] : L["已寄出驗證信給 {0}。", op.Email]);
            }
            else
            {
                Fail(L["寄信失敗，請稍後再試。"]);
            }
        });
    }

    private async Task ResetPasswordAsync(OperatorInfo op)
    {
        var confirmed = await ConfirmAsync(
            L["重設密碼"],
            L["這會作廢 {0} 現在的密碼，並讓他在所有裝置上登出。新的一次性密碼只顯示一次。確定？", op.Email],
            L["重設密碼"]);
        if (!confirmed)
        {
            return;
        }

        await RunAsync(async () =>
        {
            var reset = await admin.ResetPasswordAsync(await ActorAsync(), op.Id, CancellationToken.None);
            if (!reset.IsOk)
            {
                Fail(reset.Localized() ?? L["重設失敗。"]);
                return;
            }

            await RevealAsync(reset.Value!);
            await ReloadQuietlyAsync();
        });
    }

    private async Task ResetTwoFactorAsync(OperatorInfo op)
    {
        var confirmed = await ConfirmAsync(
            L["重設驗證器"],
            L["這會關掉 {0} 的兩步驟驗證、丟掉驗證器金鑰與復原碼，並讓他在所有裝置上登出。請先確認對方是本人。確定？", op.Email],
            L["重設驗證器"]);
        if (!confirmed)
        {
            return;
        }

        await RunAsync(async () =>
        {
            var reset = await admin.ResetTwoFactorAsync(await ActorAsync(), op.Id, CancellationToken.None);
            if (!reset.IsOk)
            {
                Fail(reset.Localized() ?? L["重設失敗。"]);
                return;
            }

            await ReloadQuietlyAsync();
            Succeed(L["已重設 {0} 的驗證器。", op.Email]);
        });
    }

    private async Task DisableAsync(OperatorInfo op)
    {
        var confirmed = await ConfirmAsync(
            L["停用營運者"],
            L["{0} 的下一個請求就進不去。已經開著的頁面最多還能用一分鐘。確定停用？", op.Email],
            L["停用"]);
        if (!confirmed)
        {
            return;
        }

        await SetDisabledAsync(op, true);
    }

    private Task EnableAsync(OperatorInfo op) => SetDisabledAsync(op, false);

    private async Task SetDisabledAsync(OperatorInfo op, bool disabled)
    {
        await RunAsync(async () =>
        {
            var changed = await admin.SetDisabledAsync(await ActorAsync(), op.Id, disabled, CancellationToken.None);
            if (!changed.IsOk)
            {
                Fail(changed.Localized() ?? L["操作失敗。"]);
                return;
            }

            await ReloadQuietlyAsync();
            Succeed(disabled ? L["已停用 {0}。", op.Email] : L["已重新啟用 {0}。", op.Email]);
        });
    }

    private async Task RevealAsync(IssuedOperatorPassword issued)
    {
        var reveal = await Dialogs.ShowAsync<OperatorPasswordDialog>(L["一次性密碼"], new DialogParameters<OperatorPasswordDialog>
        {
            { x => x.Operator, issued.Operator },
            { x => x.Password, issued.Password },
            { x => x.LoginPath, UiEndpoints.LoginPath },
        }, Narrow);
        await reveal.Result;
    }

    private async Task ReloadQuietlyAsync()
    {
        var listed = await admin.ListAsync(CancellationToken.None);
        if (listed.IsOk)
        {
            _operators = listed.Value;
        }
    }
}