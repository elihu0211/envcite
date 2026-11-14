using EnvCite.Platform.Web.Components.Bases;

namespace EnvCite.Platform.Web.Components.Dialogs;

public sealed partial class CreateOperatorDialog(IOperatorAdmin operators, IMailSender mail)
{
    private string? _email;
    private string? _displayName;

    private IMailSender Mail => mail;

    protected override async Task SubmitAsync() =>
        await CompleteAsync(
            async actor => mail.Enabled
                // 有寄信就邀請（對方自己設密碼）；沒有就發暫時密碼，由你親手交付。
                ? DialogOutcome.From(await operators.InviteAsync(actor, _email ?? string.Empty, _displayName, CancellationToken.None), L["建立失敗。"])
                : DialogOutcome.From(await operators.CreateAsync(actor, _email ?? string.Empty, _displayName, CancellationToken.None), L["建立失敗。"]),
            L["建立失敗，請稍後再試。"]);
}