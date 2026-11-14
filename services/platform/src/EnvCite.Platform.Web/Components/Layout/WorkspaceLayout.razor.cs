using System.Security.Claims;

using EnvCite.Platform.Web.Components.Portal.Workspace;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;

namespace EnvCite.Platform.Web.Components.Layout;

public sealed partial class WorkspaceLayout(ITenantDirectory directory, WorkspaceState workspace)
{
    [CascadingParameter]
    private Task<AuthenticationState>? AuthenticationState { get; set; }

    private ClaimsPrincipal? _user;
    private string? _tenantIdentifier;
    private string? _tenantName;
    private string? _home;
    private bool _showMenu;
    private IReadOnlyList<PortalShell.MenuItem> _menuItems = [];
    private IReadOnlyList<PortalShell.NavItem> _navItems = [];
    private IReadOnlyList<PortalShell.RecentGroup> _recent = [];
    private bool _subscribed;
    private int _recentRead;

    /// <summary>預先渲染查到的租戶名稱與最近對話，交給互動渲染的第一次繪製：不必先畫沒有名稱、沒有最近對話的外殼，等資料庫回來再補。
    /// 重新連線不還原：資料可能已經變了，要重查。</summary>
    public sealed record Prerender(string TenantName, IReadOnlyList<PortalShell.RecentGroup> Recent);

    [PersistentState(RestoreBehavior = RestoreBehavior.SkipLastSnapshot)]
    public Prerender? Prerendered { get; set; }

    protected override async Task OnParametersSetAsync()
    {
        // 登入狀態被重驗換成匿名時（帳號停用、戳記變了），這裡也要跟著變：只留頂欄。
        _user = AuthenticationState is null ? null : (await AuthenticationState).User;
        var identifier = _user?.Identity?.IsAuthenticated == true && _user.FindFirst(PlatformClaimTypes.PortalUserId) is not null
            ? _user.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value
            : null;
        if (identifier is null)
        {
            (_tenantIdentifier, _tenantName, _home, _showMenu, _menuItems, _navItems, _recent) = (null, null, null, false, [], [], []);
            return;
        }

        // 外殼的結構（有沒有側欄與選單、側欄有哪些項目）只取決於身分，同步就算得出來，先算完：Blazor 在第一個 await 讓出時
        // 會用當下的狀態畫一次，若這時還沒填好，第一次繪製就只有頂欄、沒有側欄，等資料庫回來再把側欄長出來
        // ——換到工作區時整個版面先縮成一條、再長回來。
        var tenantChanged = identifier != _tenantIdentifier;
        if (tenantChanged)
        {
            _tenantIdentifier = identifier;
            _home = PortalPaths.Home(identifier);
            _menuItems = MenuItemsOf(identifier);
            _tenantName = Prerendered?.TenantName ?? identifier;
        }

        _showMenu = CanUseMenu(_user);
        _navItems = _showMenu ? CollectNavItems(identifier, _user!) : [];

        if (!_subscribed)
        {
            workspace.RecentChanged += OnRecentChanged;
            _subscribed = true;
        }

        if (Prerendered is { } carried)
        {
            // 互動渲染接手預先渲染的結果：名稱與最近對話已經有了，不再查一次。
            Prerendered = null;
            if (_recentRead == 0) // 已經有人重新讀過了，預先渲染時查到的舊清單就不要再蓋上去
            {
                _recent = _showMenu ? carried.Recent : [];
            }

            return;
        }

        if (tenantChanged)
        {
            _tenantName = (await directory.FindByIdentifierAsync(identifier, CancellationToken.None))?.Name ?? identifier;
        }

        _recent = _showMenu ? await LatestRecentAsync(_user!, identifier) : [];

        if (!RendererInfo.IsInteractive)
        {
            Prerendered = new Prerender(_tenantName!, _recent);
        }
    }

    /// <summary>問完一題、對話被記進歷史：重新讀最近對話。事件來自頁面的事件處理，要回到這個元件的同步內容再重畫。</summary>
    private void OnRecentChanged() => _ = InvokeAsync(async () =>
    {
        if (_user is not null && _tenantIdentifier is not null && _showMenu)
        {
            _recent = await LatestRecentAsync(_user, _tenantIdentifier);
            StateHasChanged();
        }
    });

    /// <summary>
    /// 讀最近對話。版面初始化、換頁與「問完一題」的通知各自發一次讀取，可能反序完成：只採用最新發出的那一次，
    /// 晚到的舊結果（例如問之前讀的空清單）不蓋掉新的，否則側欄會停在舊的狀態。
    /// </summary>
    private async Task<IReadOnlyList<PortalShell.RecentGroup>> LatestRecentAsync(ClaimsPrincipal user, string identifier)
    {
        var read = ++_recentRead;
        var recent = await RecentOfAsync(user, identifier);
        return read == _recentRead ? recent : _recent;
    }

    public void Dispose() => workspace.RecentChanged -= OnRecentChanged;
}