using EnvCite.Platform.Modules.Canon;

namespace EnvCite.Platform.Tests;

/// <summary>法規目錄的形狀（<see cref="LawCatalog"/>）：母法樹與編章節歸條，不經服務。</summary>
public sealed class LawCatalogTests
{
    private static LawSummaryDto Law(string localId, string? parentLocalId = null) =>
        new(
            new LawIdentifierDto("TW", "pcode", localId),
            "uri:" + localId,
            localId,
            "statute",
            null,
            parentLocalId is null ? null : new LawIdentifierDto("TW", "pcode", parentLocalId),
            ChildCount: 3);

    [Fact]
    public void ALawWhoseParentIsNotInTheListIsARootAndChildrenKeepInputOrder()
    {
        var tree = LawCatalog.Tree(
        [
            Law("child-b", "parent"),
            Law("parent", "missing"),
            Law("child-a", "parent"),
            Law("other"),
        ]);

        Assert.Equal(["parent", "other"], tree.Select(n => n.LocalId));
        Assert.Equal(["child-b", "child-a"], tree[0].Children.Select(n => n.LocalId));
        Assert.Equal(3, tree[0].ChildCount);
        Assert.Empty(tree[1].Children);
    }

    [Fact]
    public void AnEmptyLawListIsAnEmptyTree() =>
        Assert.Empty(LawCatalog.Tree([]));

    private static StructureHeadingDto Heading(string no, IReadOnlyList<int> sort, int parent) =>
        new("章", no, no, sort, parent);

    private static ArticleWithSortDto Article(string number, params int[] sort) =>
        new("uri:" + number, number, sort);

    [Fact]
    public void WithoutHeadingsTheArticlesStayOutOfTheStructure() =>
        Assert.Empty(LawCatalog.Structure([], [Article("1", 1)]));

    [Fact]
    public void ArticlesBelongToTheNearestHeadingNotAfterThemAndNestByParent()
    {
        var structure = LawCatalog.Structure(
            [
                Heading("編", [1], -1),
                Heading("章", [1, 1], 0),
            ],
            [
                Article("before", 0),
                Article("in-part", 1, 0),
                Article("in-chapter", 1, 1, 1),
            ]);

        var part = Assert.Single(structure);
        Assert.Equal(["in-part"], part.Articles.Select(a => a.Number));
        var chapter = Assert.Single(part.Children);
        Assert.Equal(["in-chapter"], chapter.Articles.Select(a => a.Number));
    }

    [Fact]
    public void AHeadingThatPointsAtItselfIsARoot()
    {
        var structure = LawCatalog.Structure([Heading("自己", [1], 0)], []);

        Assert.Equal("自己", Assert.Single(structure).HeadingNo);
    }
}