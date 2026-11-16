using EnvCite.Platform.Web.Components.Portal;

namespace EnvCite.Platform.Tests;

/// <summary>法規樹的純運算（<see cref="LawTree"/>）：關鍵字過濾、焦點所在的分支、圖譜焦點。</summary>
public sealed class LawTreeTests
{
    private static PortalLawTreeNode Node(string localId, string title, params PortalLawTreeNode[] children) =>
        new("TW", "pcode", localId, title, "statute", null, children);

    // 空氣污染防制法
    // ├─ 施行細則
    // └─ 排放標準
    //    └─ 檢測辦法
    // 水污染防治法
    private static readonly PortalLawTreeNode[] Roots =
    [
        Node("A1", "空氣污染防制法", Node("A1-1", "施行細則"), Node("A1-2", "排放標準", Node("A1-2-1", "檢測辦法"))),
        Node("W1", "水污染防治法"),
    ];

    private static string[] Titles(IEnumerable<PortalLawTreeNode> nodes) => [.. nodes.Select(n => n.Title)];

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void ABlankQueryReturnsTheSameTree(string? query) =>
        Assert.Same(Roots, LawTree.Filter(Roots, query));

    [Fact]
    public void AMatchOnAParentKeepsItsWholeSubtree()
    {
        var result = LawTree.Filter(Roots, "空氣污染");

        var parent = Assert.Single(result);
        Assert.Equal(["施行細則", "排放標準"], Titles(parent.Children));
        Assert.Equal(["檢測辦法"], Titles(parent.Children[1].Children));
    }

    [Fact]
    public void AMatchOnADescendantKeepsOnlyThePathToIt()
    {
        var result = LawTree.Filter(Roots, "檢測");

        var parent = Assert.Single(result);
        Assert.Equal("空氣污染防制法", parent.Title);
        var middle = Assert.Single(parent.Children);
        Assert.Equal("排放標準", middle.Title);
        Assert.Equal(["檢測辦法"], Titles(middle.Children));
    }

    [Fact]
    public void TheFilterMatchesTheCodeIgnoringCaseAndSurroundingSpaces()
    {
        var result = LawTree.Filter(Roots, "  a1-2-1 ");

        var parent = Assert.Single(result);
        Assert.Equal("A1", parent.LocalId);
    }

    [Fact]
    public void NoMatchYieldsAnEmptyTree() =>
        Assert.Empty(LawTree.Filter(Roots, "不存在的法規"));

    [Fact]
    public void ActiveBranchIsTheKeysFromTheRootDownToTheTarget()
    {
        var keys = LawTree.ActiveBranch(Roots, LawTree.Key("TW", "pcode", "A1-2-1"));

        Assert.Equal(
            [LawTree.Key("TW", "pcode", "A1"), LawTree.Key("TW", "pcode", "A1-2"), LawTree.Key("TW", "pcode", "A1-2-1")],
            keys.Order(StringComparer.Ordinal));
    }

    [Fact]
    public void ActiveBranchIsEmptyWhenTheTargetIsNotInTheTree() =>
        Assert.Empty(LawTree.ActiveBranch(Roots, LawTree.Key("TW", "pcode", "missing")));

    private static PortalLawGraphNode GraphNode(PortalLawKey key) =>
        new(key, "iri", "title", "statute", "law", null, 1, 0, 0, 0, 0, 0);

    private static PortalLawGraphResult Graph(PortalLawKey? focus, params PortalLawKey[] nodes) =>
        new(true, focus, [.. nodes.Select(GraphNode)], [], [], [], []);

    [Fact]
    public void FindFocusReturnsTheGraphNodeOfTheFocus()
    {
        var focus = new PortalLawKey("TW", "pcode", "A1");

        var found = LawTree.FindFocus(Graph(focus, new PortalLawKey("TW", "pcode", "W1"), focus));

        Assert.Equal(focus, found?.Key);
    }

    [Fact]
    public void FindFocusIsNullWithoutAFocusOrWhenTheNodeIsNotInTheGraph()
    {
        var other = new PortalLawKey("TW", "pcode", "W1");

        Assert.Null(LawTree.FindFocus(Graph(null, other)));
        Assert.Null(LawTree.FindFocus(Graph(new PortalLawKey("TW", "pcode", "A1"), other)));
    }
}