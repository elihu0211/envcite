using EnvCite.Platform.Web.Auth;

namespace EnvCite.Platform.Web.Components.Shared;

public sealed partial class CommandPalette(ITenantDirectory directory)
{
    private const int MaxTenants = 8;

    private sealed record PageLink(string Href, string Label);

    private IReadOnlyList<TenantSummary> _all = [];
    private string? _query;

    private async Task LoadAsync()
    {
        try
        {
            _all = await directory.ListAsync(CancellationToken.None);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            _all = []; // 租戶載入失敗時，頁面仍然可以搜尋。
        }
    }

    private bool HasQuery => !string.IsNullOrWhiteSpace(_query);

    private bool Match(string? text) => !HasQuery || (text?.Contains(_query!.Trim(), StringComparison.OrdinalIgnoreCase) ?? false);

    private IReadOnlyList<PageLink> Pages =>
        [.. new PageLink[]
        {
            new("/ui", L["總覽"]), new("/ui/tenants", L["租戶"]), new("/ui/operators", L["營運者"]), new("/ui/audit", L["稽核紀錄"]),
            new(UiEndpoints.PasswordPath, L["改密碼"]), new(UiEndpoints.SecurityPath, L["兩步驟驗證"]),
        }.Where(p => Match(p.Label))];

    private IReadOnlyList<TenantSummary> Tenants =>
        [.. _all.Where(t => Match(t.Name) || Match(t.Identifier)).OrderBy(t => t.Identifier, StringComparer.Ordinal).Take(MaxTenants)];

    private static string TenantHref(TenantSummary tenant) => $"/ui/tenants/{Uri.EscapeDataString(tenant.Id)}";
}