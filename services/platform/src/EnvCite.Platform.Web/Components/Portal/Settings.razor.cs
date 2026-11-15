using System.Security.Claims;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class Settings(IPortalProfile profile, IPortalAccount account, IPortalTwoFactor twoFactor)
{
    [SupplyParameterFromForm(FormName = "portal-profile-name")]
    private ProfileForm ProfileInput { get; set; } = default!;

    [SupplyParameterFromQuery(Name = "saved")]
    public string? Saved { get; set; }

    [SupplyParameterFromQuery(Name = "avatar")]
    public string? AvatarStatus { get; set; }

    private PortalProfileInfo? _profile;
    private bool _twoFactor;
    private string? _message;

    private string TenantDisplayName =>
        account.CurrentTenant is { } current ? current.Name ?? current.Identifier : Tenant;

    private string? Email => _profile?.Email ?? HttpContext.User.FindFirst(ClaimTypes.Email)?.Value;

    private string RoleLabel => PortalLabels.Role(_profile?.Role ?? HttpContext.User.FindFirst(ClaimTypes.Role)?.Value);

    private string? AvatarUrl =>
        _profile is { AvatarVersion: > 0 }
        && HttpContext.User.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is { } userId
            ? PortalPaths.Avatar(Tenant, userId, _profile.AvatarVersion)
            : null;

    private string AvatarInitial
    {
        get
        {
            var source = _profile?.DisplayName ?? Email;
            return string.IsNullOrEmpty(source)
                ? string.Empty
                : char.ConvertFromUtf32(char.ConvertToUtf32(source, 0)).ToUpperInvariant();
        }
    }

    protected override async Task OnInitializedAsync()
    {
        ProfileInput ??= new();
        _profile = await profile.GetAsync(HttpContext.User, HttpContext.RequestAborted);
        _twoFactor = _profile?.TwoFactorEnabled ?? await twoFactor.IsEnabledAsync(HttpContext.User, HttpContext.RequestAborted);

        if (!HttpMethods.IsPost(HttpContext.Request.Method))
        {
            ProfileInput.DisplayName = _profile?.DisplayName;
        }

        if (Saved == "1")
        {
            _message = L["已更新顯示名稱。"];
        }

        switch (AvatarStatus)
        {
            case "saved":
                _message = L["已更新大頭貼。"];
                break;
            case "removed":
                _message = L["已移除大頭貼。"];
                break;
            case "too_large":
                Error = L["圖片檔案不可超過 5 MB。"];
                break;
            case "invalid":
                Error = L["請上傳有效的 PNG、JPEG 或 WebP 圖片。"];
                break;
        }
    }

    private async Task SaveNameAsync()
    {
        var updated = await profile.UpdateDisplayNameAsync(HttpContext.User, ProfileInput.DisplayName, HttpContext.RequestAborted);
        if (updated.IsOk)
        {
            // PRG：重簽 cookie 後重新導向，側欄左下角的新名稱與提示訊息同一次生效。
            Navigation.NavigateTo($"{PortalPaths.Home(Tenant)}/settings?saved=1");
            return;
        }

        Error = Describe(updated, L["儲存失敗。"]);
    }

    private sealed class ProfileForm
    {
        public string? DisplayName { get; set; }
    }
}