using EnvCite.Platform.Web.Components.Bases;

namespace EnvCite.Platform.Web.Components.Dialogs;

public sealed partial class CreateTenantDialog(ITenantProvisioner tenants)
{
    private string? _name;
    private string? _identifier;

    protected override async Task SubmitAsync() =>
        await CompleteAsync(
            async actor => DialogOutcome.From(await tenants.CreateAsync(actor, _identifier?.Trim() ?? string.Empty, _name ?? string.Empty, CancellationToken.None), L["建立失敗。"]),
            L["建立失敗，請稍後再試。"]);
}