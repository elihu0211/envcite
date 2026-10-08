using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvLaw.Platform.Web.Components.Portal;

public partial class Home
{
    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IPortalAccount Account { get; set; } = default!;

    [Inject] private ITenantDirectory Directory { get; set; } = default!;

    [Inject] private IApiKeyAdmin Keys { get; set; } = default!;

    [Inject] private IPortalTwoFactor TwoFactor { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    private TenantSummary? _tenant;
    private IReadOnlyList<ApiKeyInfo> _keys = [];
    private bool _twoFactor;

    private ClaimsPrincipal User => HttpContext.User;

    private string? DisplayName => User.FindFirst(ClaimTypes.GivenName)?.Value ?? User.FindFirst(ClaimTypes.Email)?.Value;

    private string? Email => User.FindFirst(ClaimTypes.Email)?.Value;

    private string RoleLabel => PortalLabels.Role(User.FindFirst(ClaimTypes.Role)?.Value);

    private bool CanManageMembers => User.IsInRole(CustomerRoles.Owner) || User.IsInRole(CustomerRoles.Admin);

    /// <summary>Owner、Admin、Developer 能看 API key；Billing 與 Viewer 不能（07-01 表「角色與權限」是草案，未與客戶確認）。</summary>
    private bool CanSeeKeys => User.IsInRole(CustomerRoles.Owner) || User.IsInRole(CustomerRoles.Admin) || User.IsInRole(CustomerRoles.Developer);

    protected override async Task OnInitializedAsync()
    {
        // 租戶 Id 取自這個請求解析出的租戶（authorization 已確認 cookie 的租戶就是它），不取自使用者的輸入。
        if (Account.CurrentTenant is not { } current)
        {
            return;
        }

        _twoFactor = await TwoFactor.IsEnabledAsync(User, HttpContext.RequestAborted);
        _tenant = await Directory.FindAsync(current.Id, HttpContext.RequestAborted);
        if (CanSeeKeys)
        {
            var keys = await Keys.ListAsync(current.Id, HttpContext.RequestAborted);
            _keys = keys.IsOk ? [.. keys.Value!.OrderByDescending(k => k.CreatedAt)] : [];
        }
    }

    private static string StatusTone(ApiKeyInfo key) =>
        key.RevokedAt is not null ? "neutral"
        : key.ExpiresAt is { } expires && expires <= DateTimeOffset.UtcNow ? "warning"
        : "success";

    private static string Status(ApiKeyInfo key) =>
        key.RevokedAt is not null ? "已撤銷"
        : key.ExpiresAt is { } expires && expires <= DateTimeOffset.UtcNow ? "已過期"
        : "有效";
}