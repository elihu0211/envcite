using EnvCite.Platform.Web.Components.Bases;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Dialogs;

public sealed partial class IssueApiKeyDialog(IApiKeyAdmin keys)
{
    [Parameter, EditorRequired] public string TenantId { get; set; } = string.Empty;

    private string? _name;
    private IssuedApiKey? _issued;

    protected override async Task SubmitAsync()
    {
        var outcome = await CompleteAsync(
            async actor => DialogOutcome.From(await keys.IssueAsync(actor, TenantId, _name ?? string.Empty, null, null, CancellationToken.None), L["簽發失敗。"]),
            L["簽發失敗，請稍後再試。"],
            close: false);
        if (outcome is not null)
        {
            _issued = (IssuedApiKey?)outcome.Value;
            // 完整的 key 只在這個階段存在：不能再被外點或 Esc 關掉。
            await LockAsync();
        }
    }
}