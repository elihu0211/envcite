using System.Security.Claims;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class AccountOverview(
    IPortalAccount account,
    ITenantDirectory directory,
    ITenantSelfService tenantSelfService,
    IApiKeyAdmin keys,
    IPortalTwoFactor twoFactor,
    ITenantModules modules)
{
    [SupplyParameterFromForm(FormName = "portal-tenant-rename")]
    private RenameForm RenameInput { get; set; } = default!;

    [SupplyParameterFromQuery(Name = "renamed")]
    public string? Renamed { get; set; }

    private TenantSummary? _tenant;
    private IReadOnlyList<ApiKeyInfo> _keys = [];
    private bool _twoFactor;
    private string? _message;

    private ITenantModules Modules => modules;

    private string? GivenName => User.FindFirst(ClaimTypes.GivenName)?.Value;

    private string? Email => User.FindFirst(ClaimTypes.Email)?.Value;

    private string RoleLabel => PortalLabels.Role(User.FindFirst(ClaimTypes.Role)?.Value);

    private bool IsOwner => User.IsInRole(CustomerRoles.Owner);

    private bool CanManageMembers => User.IsInRole(CustomerRoles.Owner) || User.IsInRole(CustomerRoles.Admin);

    /// <summary>Owner、Admin、Developer 能看 API key；Billing 與 Viewer 不能（07-01 表「角色與權限」是草案，未與客戶確認）。</summary>
    private bool CanSeeKeys => User.IsInRole(CustomerRoles.Owner) || User.IsInRole(CustomerRoles.Admin) || User.IsInRole(CustomerRoles.Developer);

    protected override async Task OnInitializedAsync()
    {
        RenameInput ??= new();

        // 租戶 Id 取自這個請求解析出的租戶（authorization 已確認 cookie 的租戶就是它），不取自使用者的輸入。
        if (account.CurrentTenant is not { } current)
            return;

        _twoFactor = await twoFactor.IsEnabledAsync(User, HttpContext.RequestAborted);
        _tenant = await directory.FindAsync(current.Id, HttpContext.RequestAborted);
        if (!HttpMethods.IsPost(HttpContext.Request.Method))
        {
            RenameInput.Name = _tenant?.Name ?? current.Name;
        }

        if (Renamed == "1")
        {
            _message = L["已更新組織名稱。"];
        }

        if (CanSeeKeys)
        {
            var listedKeys = await keys.ListAsync(current.Id, HttpContext.RequestAborted);
            _keys = listedKeys.IsOk ? [.. listedKeys.Value!.OrderByDescending(k => k.CreatedAt)] : [];
        }
    }

    private async Task RenameTenantAsync()
    {
        var renamed = await tenantSelfService.RenameAsync(User, RenameInput.Name ?? string.Empty, HttpContext.RequestAborted);
        if (renamed.IsOk)
        {
            Navigation.NavigateTo($"{PortalPaths.Home(Tenant)}/account?renamed=1");
            return;
        }

        Error = Describe(renamed, L["重新命名失敗。"]);
    }

    private sealed class RenameForm
    {
        public string? Name { get; set; }
    }

    private static string StatusTone(ApiKeyInfo key) =>
        key.RevokedAt is not null ? "neutral"
        : key.ExpiresAt is { } expires && expires <= DateTimeOffset.UtcNow ? "warning"
        : "success";

    private string Status(ApiKeyInfo key) =>
        key.RevokedAt is not null ? L["已撤銷"]
        : key.ExpiresAt is { } expires && expires <= DateTimeOffset.UtcNow ? L["已過期"]
        : L["有效"];
}