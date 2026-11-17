using System.Security.Claims;

using EnvCite.Platform.Web.Components.Layout;
using EnvCite.Platform.Web.Components.Layout.Navigation;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口導覽的項目由各功能自己貢獻（<see cref="IPortalNavContributor"/>），外殼不認識任何功能：
/// 每個來源只管自己放在側欄或帳號選單、出不出現，排序與分群由 <see cref="PortalNavigation.Collect"/> 一條路。
/// 側欄只放業務模塊；租戶的系統設定（成員、目錄同步、帳號與組織）放帳號選單，由角色決定誰看得到。
/// 不經主機，測的是規則本身；整頁輸出由 <c>PortalShellTests</c> 守住。
/// </summary>
public sealed class PortalNavigationTests
{
    private const string Home = "/portal/acme";

    private sealed class FakeDirectory(bool available) : IDirectoryAvailability
    {
        public bool Available => available;

        public TimeSpan StaleAfter => TimeSpan.FromDays(1);
    }

    private static ClaimsPrincipal In(string role) => new(new ClaimsIdentity([new Claim(ClaimTypes.Role, role)], "test"));

    private static IReadOnlyList<PortalShell.NavItem> Collect(string role, PortalNavSlot slot, bool directory = true)
    {
        var localizer = new KeyLocalizer();
        // 故意不照 Order 排：Collect 必須自己排。
        IPortalNavContributor[] contributors =
        [
            new AccountNav(localizer),
            new DirectoryNav(localizer, new FakeDirectory(directory)),
            new MembersNav(localizer),
            new QueryNav(localizer),
        ];
        return PortalNavigation.Collect(contributors, new PortalNavContext(Home, In(role)), slot);
    }

    private static IReadOnlyList<PortalShell.NavItem> Sidebar(string role) => Collect(role, PortalNavSlot.Sidebar);

    private static IReadOnlyList<PortalShell.NavItem> Settings(string role, bool directory = true) => Collect(role, PortalNavSlot.AccountMenu, directory);

    [Theory]
    [InlineData(CustomerRoles.Owner)]
    [InlineData(CustomerRoles.Admin)]
    [InlineData(CustomerRoles.Developer)]
    [InlineData(CustomerRoles.Billing)]
    [InlineData(CustomerRoles.Viewer)]
    public void TheSidebarHoldsOnlyBusinessModulesWhateverTheRole(string role)
    {
        var items = Sidebar(role);

        Assert.Equal([Home], items.Select(i => i.Href));
        Assert.Equal(["服務"], items.Select(i => i.Group).Distinct());
    }

    [Fact]
    public void TheOwnerSeesEveryTenantSettingInOrderUnderOneGroup()
    {
        var items = Settings(CustomerRoles.Owner);

        Assert.Equal([$"{Home}/members", $"{Home}/directory", $"{Home}/account"], items.Select(i => i.Href));
        Assert.Equal(["系統設定"], items.Select(i => i.Group).Distinct());
    }

    [Theory]
    [InlineData(CustomerRoles.Admin, true)]
    [InlineData(CustomerRoles.Developer, false)]
    [InlineData(CustomerRoles.Billing, false)]
    [InlineData(CustomerRoles.Viewer, false)]
    public void MembersAreListedOnlyForOwnersAndAdmins(string role, bool listed) =>
        Assert.Equal(listed, Settings(role).Any(i => i.Href == $"{Home}/members"));

    [Theory]
    [InlineData(CustomerRoles.Admin)]
    [InlineData(CustomerRoles.Developer)]
    [InlineData(CustomerRoles.Billing)]
    [InlineData(CustomerRoles.Viewer)]
    public void DirectorySyncIsListedOnlyForOwners(string role) =>
        Assert.DoesNotContain(Settings(role), i => i.Href == $"{Home}/directory");

    [Theory]
    [InlineData(CustomerRoles.Developer)]
    [InlineData(CustomerRoles.Billing)]
    [InlineData(CustomerRoles.Viewer)]
    public void RolesWithoutManagementOnlySeeTheirOwnAccountOverview(string role) =>
        Assert.Equal([$"{Home}/account"], Settings(role).Select(i => i.Href));

    [Fact]
    public void FeaturesThatAreSwitchedOffAreNotListedEvenForTheOwner()
    {
        var items = Settings(CustomerRoles.Owner, directory: false);

        Assert.Equal([$"{Home}/members", $"{Home}/account"], items.Select(i => i.Href));
    }

    [Fact]
    public void OnlyTheHomeEntryMatchesExactly()
    {
        var items = Sidebar(CustomerRoles.Owner);

        Assert.Equal([Home], items.Where(i => i.Exact).Select(i => i.Href));
    }

    // 新增功能 = 實作介面並註冊：外殼與其他來源都不必改；放側欄還是帳號選單由它自己宣告。
    [Fact]
    public void ANewContributorJoinsItsOwnSlotWithoutTouchingTheOthers()
    {
        var localizer = new KeyLocalizer();
        IPortalNavContributor[] contributors =
            [new QueryNav(localizer), new Extra(PortalNavSlot.Sidebar, 25), new Extra(PortalNavSlot.AccountMenu, 45), new AccountNav(localizer)];
        var context = new PortalNavContext(Home, In(CustomerRoles.Viewer));

        Assert.Equal([Home, $"{Home}/extra"], PortalNavigation.Collect(contributors, context, PortalNavSlot.Sidebar).Select(i => i.Href));
        Assert.Equal([$"{Home}/extra", $"{Home}/account"], PortalNavigation.Collect(contributors, context, PortalNavSlot.AccountMenu).Select(i => i.Href));
    }

    private sealed class Extra(PortalNavSlot slot, int order) : IPortalNavContributor
    {
        public PortalNavSlot Slot => slot;

        public int Order => order;

        public IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) => [new($"{context.Home}/extra", "額外", "服務")];
    }
}