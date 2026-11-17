using System.Security.Claims;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;

namespace EnvCite.Platform.Web.Components.Layout;

public sealed partial class PortalLayout(IPortalAccount account)
{
    [CascadingParameter]
    private Task<AuthenticationState>? AuthenticationState { get; set; }

    private ClaimsPrincipal? _user;

    private IReadOnlyList<PortalShell.RecentConversation> _recent = [];

    private IPortalAccount Account => account;

    private bool Authenticated => _user?.Identity?.IsAuthenticated == true && account.CurrentTenant is not null;

    private string? Email => _user?.FindFirst(ClaimTypes.Email)?.Value;

    private bool ShowMenu => Authenticated && CanUseMenu(_user);

    /// <summary>側欄的項目由各功能的 <see cref="Navigation.IPortalNavContributor"/> 貢獻，外殼不認識任何功能。</summary>
    private IReadOnlyList<PortalShell.NavItem> NavItems =>
        account.CurrentTenant is { } tenant && _user is not null
            ? CollectNavItems(tenant.Identifier, _user)
            : [];

    /// <summary>帳號選單裡的系統設定項目：依登入者角色由各來源決定出不出現。</summary>
    private IReadOnlyList<PortalShell.NavItem> SettingsItems =>
        account.CurrentTenant is { } tenant && _user is not null
            ? CollectSettingsItems(tenant.Identifier, _user)
            : [];

    private IReadOnlyList<PortalShell.MenuItem> MenuItems =>
        account.CurrentTenant is { } tenant ? MenuItemsOf(tenant.Identifier) : [];

    protected override async Task OnInitializedAsync()
    {
        _user = AuthenticationState is null ? null : (await AuthenticationState).User;
        if (ShowMenu && account.CurrentTenant is { } tenant)
        {
            _recent = await RecentOfAsync(_user!, tenant.Identifier);
        }
    }
}