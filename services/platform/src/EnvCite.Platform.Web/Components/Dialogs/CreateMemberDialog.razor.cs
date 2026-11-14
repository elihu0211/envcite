using EnvCite.Platform.Web.Components.Bases;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Dialogs;

public sealed partial class CreateMemberDialog(IMemberAdmin members, IMailSender mail)
{
    [Parameter, EditorRequired] public string TenantId { get; set; } = string.Empty;

    private string? _email;
    private string? _displayName;
    private string _role = CustomerRoles.Viewer;

    private IMailSender Mail => mail;

    protected override async Task SubmitAsync() =>
        await CompleteAsync(
            async actor => mail.Enabled
                ? DialogOutcome.From(await members.InviteAsync(actor, TenantId, _email ?? string.Empty, _displayName, _role, CancellationToken.None), L["建立失敗。"])
                : DialogOutcome.From(await members.CreateAsync(actor, TenantId, _email ?? string.Empty, _displayName, _role, CancellationToken.None), L["建立失敗。"]),
            L["建立失敗，請稍後再試。"]);
}