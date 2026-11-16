using EnvCite.Platform.Web.Components.Portal;

namespace EnvCite.Platform.Tests;

/// <summary>法規體系頁的查詢狀態（<see cref="LawCorpusRoute"/>）：參數正規化與頁內連結，不經瀏覽器。</summary>
public sealed class LawCorpusRouteTests
{
    private static readonly LawCorpusRoute Empty = new("acme", null, null, null, null, null, null, null, null, null, null, null);

    private static readonly PortalLawKey Law = new("TW", "pcode", "O0020001");

    private static PortalLawTreeNode Node(PortalLawKey key) =>
        new(key.Jurisdiction, key.IdScheme, key.LocalId, "空氣污染防制法", "statute", null, []);

    [Fact]
    public void WithoutParametersTheEnvironmentDomainAndDefaultDepthsApply()
    {
        Assert.Equal("O", Empty.SelectedCategory);
        Assert.Equal("O", Empty.CategoryPrefix);
        Assert.Equal(2, Empty.ResolvedUpDepth);
        Assert.Equal(2, Empty.ResolvedDownDepth);
        Assert.Equal(20, Empty.ResolvedChildLimit);
        Assert.False(Empty.HasSelectedLaw);
    }

    [Theory]
    [InlineData("ALL", "ALL")]
    [InlineData("all", "ALL")]
    [InlineData(" K ", "K")]
    [InlineData("k", "O")]
    [InlineData("KK", "O")]
    [InlineData("1", "O")]
    [InlineData("", "O")]
    public void AnExplicitCategoryIsOnlyHonouredWhenItIsAllOrOneUppercaseLetter(string category, string expected) =>
        Assert.Equal(expected, (Empty with { Category = category }).SelectedCategory);

    [Fact]
    public void SearchingOrFocusingALawWidensTheDefaultDomainButAnExplicitDomainWins()
    {
        Assert.Equal("ALL", (Empty with { Query = "空氣" }).SelectedCategory);
        Assert.Equal("ALL", (Empty with { Jurisdiction = "TW", IdScheme = "pcode", LocalId = "O0020001" }).SelectedCategory);
        Assert.Equal("D", (Empty with { Query = "空氣", Category = "D" }).SelectedCategory);
        Assert.Null((Empty with { Query = "空氣" }).CategoryPrefix);
    }

    [Theory]
    [InlineData(null, 2)]
    [InlineData(0, 1)]
    [InlineData(3, 3)]
    [InlineData(9, 4)]
    public void DepthsAreClamped(int? requested, int expected)
    {
        var route = Empty with { UpDepth = requested, DownDepth = requested };

        Assert.Equal(expected, route.ResolvedUpDepth);
        Assert.Equal(expected, route.ResolvedDownDepth);
    }

    [Theory]
    [InlineData(null, 20)]
    [InlineData(0, 1)]
    [InlineData(40, 40)]
    [InlineData(999, 60)]
    public void TheChildLimitIsClamped(int? requested, int expected) =>
        Assert.Equal(expected, (Empty with { ChildLimit = requested }).ResolvedChildLimit);

    [Theory]
    [InlineData(null, true, false, "graph")]
    [InlineData("structure", true, false, "structure")]
    [InlineData("STRUCTURE", false, false, "structure")]
    [InlineData("reasoning", true, false, "reasoning")]
    [InlineData("reasoning", false, false, "graph")]
    [InlineData("nonsense", true, false, "graph")]
    [InlineData("structure", true, true, "reasoning")]
    [InlineData("structure", false, true, "graph")]
    public void TheTabFallsBackToTheGraphAndTheReasoningTabNeedsTheModule(string? tab, bool hasReasoning, bool reasoningActive, string expected) =>
        Assert.Equal(expected, (Empty with { ActiveTab = tab }).ResolveTab(hasReasoning, reasoningActive));

    [Fact]
    public void LinksOmitDefaultsAndKeepTheCurrentFilters()
    {
        Assert.Equal("/portal/acme/laws?cat=O&tab=structure", Empty.TabHref("structure"));

        var route = Empty with
        {
            Query = "a b&c",
            AsOf = "2025-01-01",
            Category = "D",
            UpDepth = 3,
            DownDepth = 1,
            ChildLimit = 40,
            DocType = "rule",
        };

        Assert.Equal(
            "/portal/acme/laws?cat=D&tab=graph&q=a%20b%26c&asOf=2025-01-01&upDepth=3&downDepth=1&childLimit=40&docType=rule",
            route.TabHref("graph"));
    }

    [Fact]
    public void ChangingTheTabKeepsTheFocusedLaw()
    {
        var route = Empty with { Jurisdiction = "TW", IdScheme = "pcode", LocalId = "O0020001" };

        Assert.Equal("/portal/acme/laws?cat=ALL&tab=structure&jurisdiction=TW&idScheme=pcode&localId=O0020001", route.TabHref("structure"));
    }

    [Fact]
    public void AnIncompleteFocusIsNotWrittenIntoTheLink()
    {
        var route = Empty with { Jurisdiction = "TW", IdScheme = "pcode" };

        Assert.Equal("/portal/acme/laws?cat=O&tab=graph", route.TabHref("graph"));
    }

    [Fact]
    public void ChangingTheDomainDropsTheFocusAndTheChildTypeAndLeavesTheStructureTab()
    {
        var route = Empty with
        {
            Jurisdiction = "TW",
            IdScheme = "pcode",
            LocalId = "O0020001",
            DocType = "rule",
            UpDepth = 3,
        };

        Assert.Equal("/portal/acme/laws?cat=K&tab=graph&upDepth=3", route.CategoryHref("K", "structure"));
        Assert.Equal("/portal/acme/laws?cat=K&tab=reasoning&upDepth=3", route.CategoryHref("K", "reasoning"));
    }

    [Fact]
    public void ATreeNodeLinkFocusesTheNodeResetsTheChildOptionsAndLeavesTheReasoningTab()
    {
        var route = Empty with { ChildLimit = 40, DocType = "rule" };

        Assert.Equal(
            "/portal/acme/laws?cat=O&tab=graph&jurisdiction=TW&idScheme=pcode&localId=O0020001",
            route.NodeHref(Node(Law), "reasoning"));
        Assert.Equal(
            "/portal/acme/laws?cat=O&tab=structure&jurisdiction=TW&idScheme=pcode&localId=O0020001",
            route.NodeHref(Node(Law), "structure"));
    }

    [Fact]
    public void AFocusLinkOverridesOnlyWhatItIsGiven()
    {
        var route = Empty with { UpDepth = 3, ChildLimit = 40, DocType = "rule" };

        Assert.Equal(
            "/portal/acme/laws?cat=O&tab=graph&jurisdiction=TW&idScheme=pcode&localId=O0020001&upDepth=3&childLimit=40&docType=rule",
            route.FocusHref(Law));
        Assert.Equal(
            "/portal/acme/laws?cat=O&tab=structure&jurisdiction=TW&idScheme=pcode&localId=O0020001&upDepth=3&childLimit=60&docType=rule",
            route.FocusHref(Law, tab: "structure", childLimit: 60));
        Assert.DoesNotContain("docType", route.FocusHref(Law, docType: string.Empty), StringComparison.Ordinal);
        Assert.Contains("docType=letter", route.FocusHref(Law, docType: "letter"), StringComparison.Ordinal);
    }

    [Fact]
    public void TheAskLinkEncodesTheQuestion() =>
        Assert.Equal("/portal/acme?q=%E8%AB%8B%E8%AA%AA%E6%98%8E%3F", Empty.AskHref("請說明?"));
}