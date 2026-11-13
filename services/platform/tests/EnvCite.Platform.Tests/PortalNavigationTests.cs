using System.Security.Claims;

using EnvCite.Platform.Web.Components.Layout;
using EnvCite.Platform.Web.Components.Layout.Navigation;
using EnvCite.Platform.Web.Resources;

using Microsoft.Extensions.Localization;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口側欄的項目由各功能自己貢獻（<see cref="IPortalNavContributor"/>），外殼不認識任何功能：
/// 每個來源只管自己出不出現，排序與分群由 <see cref="PortalNavigation.Collect"/> 一條路。
/// 不經主機，測的是規則本身；整頁輸出由 <c>PortalShellTests</c> 守住。
/// </summary>
public sealed class PortalNavigationTests
{
    private const string Home = "/portal/acme";

    private sealed class KeyLocalizer : IStringLocalizer<Ui>
    {
        public LocalizedString this[string name] => new(name, name);

        public LocalizedString this[string name, params object[] arguments] => new(name, string.Format(System.Globalization.CultureInfo.InvariantCulture, name, arguments));

        public IEnumerable<LocalizedString> GetAllStrings(bool includeParentCultures) => [];
    }

    private sealed class FakeHistory(bool enabled) : IConversationHistoryAvailability
    {
        public bool Enabled => enabled;

        public int RetentionDays => 30;
    }

    private sealed class FakeDirectory(bool available) : IDirectoryAvailability
    {
        public bool Available => available;

        public TimeSpan StaleAfter => TimeSpan.FromDays(1);
    }

    private static ClaimsPrincipal In(string role) => new(new ClaimsIdentity([new Claim(ClaimTypes.Role, role)], "test"));

    private static IReadOnlyList<PortalShell.NavItem> Sidebar(string role, bool history = true, bool directory = true)
    {
        var localizer = new KeyLocalizer();
        // 故意不照 Order 排：Collect 必須自己排。
        IPortalNavContributor[] contributors =
        [
            new AccountNav(localizer),
            new DirectoryNav(localizer, new FakeDirectory(directory)),
            new MembersNav(localizer),
            new HistoryNav(localizer, new FakeHistory(history)),
            new QueryNav(localizer),
        ];
        return PortalNavigation.Collect(contributors, new PortalNavContext(Home, In(role)));
    }

    [Fact]
    public void TheOwnerSeesEverythingInOrderAndTheGroupsKeepTheirOrder()
    {
        var items = Sidebar(CustomerRoles.Owner);

        Assert.Equal([Home, $"{Home}/history", $"{Home}/members", $"{Home}/directory", $"{Home}/account"], items.Select(i => i.Href));
        Assert.Equal(["服務", "組織"], items.Select(i => i.Group).Distinct());
    }

    [Theory]
    [InlineData(CustomerRoles.Admin, true)]
    [InlineData(CustomerRoles.Developer, false)]
    [InlineData(CustomerRoles.Billing, false)]
    [InlineData(CustomerRoles.Viewer, false)]
    public void MembersAreListedOnlyForOwnersAndAdmins(string role, bool listed) =>
        Assert.Equal(listed, Sidebar(role).Any(i => i.Href == $"{Home}/members"));

    [Theory]
    [InlineData(CustomerRoles.Admin)]
    [InlineData(CustomerRoles.Developer)]
    public void DirectorySyncIsListedOnlyForOwners(string role) =>
        Assert.DoesNotContain(Sidebar(role), i => i.Href == $"{Home}/directory");

    [Fact]
    public void FeaturesThatAreSwitchedOffAreNotListedEvenForTheOwner()
    {
        var items = Sidebar(CustomerRoles.Owner, history: false, directory: false);

        Assert.Equal([Home, $"{Home}/members", $"{Home}/account"], items.Select(i => i.Href));
    }

    [Fact]
    public void OnlyTheHomeEntryMatchesExactly()
    {
        var items = Sidebar(CustomerRoles.Owner);

        Assert.Equal([Home], items.Where(i => i.Exact).Select(i => i.Href));
    }

    // 新增功能 = 實作介面並註冊：外殼與其他來源都不必改。
    [Fact]
    public void ANewContributorJoinsTheSidebarWithoutTouchingTheOthers()
    {
        var localizer = new KeyLocalizer();
        IPortalNavContributor[] contributors = [new QueryNav(localizer), new Extra(), new AccountNav(localizer)];

        var items = PortalNavigation.Collect(contributors, new PortalNavContext(Home, In(CustomerRoles.Viewer)));

        Assert.Equal([Home, $"{Home}/extra", $"{Home}/account"], items.Select(i => i.Href));
    }

    private sealed class Extra : IPortalNavContributor
    {
        public int Order => 25;

        public IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) => [new($"{context.Home}/extra", "額外", "服務")];
    }
}