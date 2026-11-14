using EnvCite.Platform.Web.Presentation;

namespace EnvCite.Platform.Tests;

public sealed class LegalArticleFormatterTests
{
    [Fact]
    public void ParseSplitsParagraphsSubparagraphsItemsSubItemsAndProvisos()
    {
        const string sample = """
            中央主管機關對於排放廢（污）水於地面水體之事業，應徵收水污染防治費。
            但已依規定完成改善者，不在此限。
            一、第一項徵收之水污染防治費：
            （一）地面水體污染整治與水質監測。
            （二）飲用水水源水質保護區水質改善。
            1. 淨水場上游之水質監測站。
            """;

        var nodes = LegalArticleFormatter.Parse(sample);

        Assert.Equal(6, nodes.Count);

        Assert.Equal(ArticleClauseKind.Paragraph, nodes[0].Kind);
        Assert.Equal(0, nodes[0].Level);
        Assert.False(nodes[0].StartsProviso);

        Assert.Equal(ArticleClauseKind.Paragraph, nodes[1].Kind);
        Assert.True(nodes[1].StartsProviso);

        Assert.Equal(ArticleClauseKind.Subparagraph, nodes[2].Kind);
        Assert.Equal(1, nodes[2].Level);
        Assert.Equal("一、", nodes[2].Marker);
        Assert.Equal("第一項徵收之水污染防治費：", nodes[2].Body);

        Assert.Equal(ArticleClauseKind.Item, nodes[3].Kind);
        Assert.Equal(2, nodes[3].Level);
        Assert.Equal("（一）", nodes[3].Marker);

        Assert.Equal(ArticleClauseKind.Item, nodes[4].Kind);
        Assert.Equal(2, nodes[4].Level);
        Assert.Equal("（二）", nodes[4].Marker);

        Assert.Equal(ArticleClauseKind.SubItem, nodes[5].Kind);
        Assert.Equal(3, nodes[5].Level);
        Assert.Equal("1.", nodes[5].Marker);
    }
}