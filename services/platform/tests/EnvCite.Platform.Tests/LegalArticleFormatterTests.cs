using System.Net;

using EnvCite.Platform.Web.Components;
using EnvCite.Platform.Web.Components.Shared;
using EnvCite.Platform.Web.Presentation;
using EnvCite.Platform.Web.Resources;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.JSInterop;

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

    [Fact]
    public void ParseBlocksConvertsNoiseStandardBoxTableWithMergedMultiRowHeaders()
    {
        const string sample = """
            娛樂場所、營業場所噪音管制標準值如下：
            ┌─────┬─────────────┬───────────┐
            │      頻率│ 20 Hz 至 200 Hz        │ 20 Hz 至 20 kHz       │
            │    時段  │                       │                       │
            │  音量    ├───┬───┬───┼───┬───┬───┤
            │管制區    │日間│晚間│夜間│日間│晚間│夜間│
            ├─────┼───┼───┼───┼───┼───┼───┤
            │第一類│ 32 │ 32 │ 27 │ 55 │ 50 │ 40 │
            ├─────┼───┼───┼───┼───┼───┼───┤
            │第二類│ 37 │ 32 │ 27 │ 57 │ 52 │ 47 │
            ├─────┼───┼───┼───┼───┼───┼───┤
            │第三類│ 37 │ 37 │ 32 │ 67 │ 57 │ 52 │
            ├─────┼───┼───┼───┼───┼───┼───┤
            │第四類│ 40 │ 40 │ 35 │ 80 │ 70 │ 65 │
            └─────┴───┴───┴───┴───┴───┴───┘
            """;

        var blocks = LegalArticleFormatter.ParseBlocks(sample);

        Assert.Equal(2, blocks.Count);
        var lead = Assert.IsType<ArticleClauseBlock>(blocks[0]);
        Assert.Equal(1, lead.ParagraphOrdinal);
        Assert.True(lead.ShowParagraphBadge);
        Assert.Equal("娛樂場所、營業場所噪音管制標準值如下：", lead.Clause.Body);

        var table = Assert.IsType<ArticleTableBlock>(blocks[1]);
        Assert.Equal(4, table.HeaderRows.Count);
        Assert.Equal(["頻率", "20 Hz 至 200 Hz", "20 Hz 至 20 kHz"], [.. table.HeaderRows[0].Select(c => c.Text)]);
        Assert.Equal([1, 3, 3], [.. table.HeaderRows[0].Select(c => c.Colspan)]);
        Assert.Equal([1, 3, 3], [.. table.HeaderRows[0].Select(c => c.Rowspan)]);
        Assert.Equal(["時段"], [.. table.HeaderRows[1].Select(c => c.Text)]);
        Assert.Equal(["音量"], [.. table.HeaderRows[2].Select(c => c.Text)]);
        Assert.Equal(["管制區", "日間", "晚間", "夜間", "日間", "晚間", "夜間"], [.. table.HeaderRows[3].Select(c => c.Text)]);

        Assert.Equal(4, table.BodyRows.Count);
        Assert.Equal(["第一類", "32", "32", "27", "55", "50", "40"], [.. table.BodyRows[0].Select(c => c.Text)]);
        Assert.Equal(["第四類", "40", "40", "35", "80", "70", "65"], [.. table.BodyRows[3].Select(c => c.Text)]);
    }

    [Fact]
    public void ParseBlocksHandlesAlignedGridAndTwoLevelGroupHeaders()
    {
        const string sample = """
            ┌─────┬────────────┬───────────┐
            │      頻率│20 Hz 至 200 Hz         │20 Hz 至 20 kHz       │
            ├─────┼──┬──┬──┼──┬──┬──┤
            │管制區│日間│晚間│夜間│日間│晚間│夜間│
            ├─────┼──┼──┼──┼──┼──┼──┤
            │第一類│ 39 │ 39 │ 36 │ 50 │ 45 │ 40 │
            └─────┴──┴──┴──┴──┴──┴──┘
            """;

        var blocks = LegalArticleFormatter.ParseBlocks(sample);

        var table = Assert.IsType<ArticleTableBlock>(Assert.Single(blocks));
        Assert.Equal(2, table.HeaderRows.Count);
        Assert.Equal([1, 3, 3], [.. table.HeaderRows[0].Select(c => c.Colspan)]);
        Assert.Equal(["頻率", "20 Hz 至 200 Hz", "20 Hz 至 20 kHz"], [.. table.HeaderRows[0].Select(c => c.Text)]);
        Assert.Equal(["管制區", "日間", "晚間", "夜間", "日間", "晚間", "夜間"], [.. table.HeaderRows[1].Select(c => c.Text)]);
        Assert.Equal(["第一類", "39", "39", "36", "50", "45", "40"], [.. table.BodyRows[0].Select(c => c.Text)]);
    }

    [Fact]
    public void ParseBlocksMergesDiagonalWrappedSingleHeaderCells()
    {
        const string sample = """
            ┌──────┬───────┬───────┐
            │      審查費│申請核發合格證│申請沿用、延伸│
            │            │明審查費（單位│或修改合格證明│
            │汽車種類    │：新臺幣）    │審查費        │
            ├──────┼───────┼───────┤
            │汽油引擎汽車│  二千元／次  │  一千元／次  │
            └──────┴───────┴───────┘
            """;

        var blocks = LegalArticleFormatter.ParseBlocks(sample);
        var table = Assert.IsType<ArticleTableBlock>(Assert.Single(blocks));
        var header = Assert.Single(table.HeaderRows);
        Assert.Equal("審查費 汽車種類", header[0].Text);
        Assert.Equal("申請核發合格證明審查費（單位：新臺幣）", header[1].Text);
        Assert.Equal("申請沿用、延伸或修改合格證明審查費", header[2].Text);
        Assert.Equal(["汽油引擎汽車", "二千元／次", "一千元／次"], [.. table.BodyRows[0].Select(c => c.Text)]);
    }

    [Fact]
    public void ParseBlocksUnwrapsSingleColumnDecorativeFrameToClauses()
    {
        const string sample = """
            ┌────────────────────┐
            │　一、本要點依廢棄物清理法規定訂定之。　│
            ├────────────────────┤
            │　二、適用範圍如下：　│
            └────────────────────┘
            """;

        var blocks = LegalArticleFormatter.ParseBlocks(sample);

        Assert.Equal(2, blocks.Count);
        var first = Assert.IsType<ArticleClauseBlock>(blocks[0]);
        Assert.Equal(ArticleClauseKind.Subparagraph, first.Clause.Kind);
        Assert.Equal("一、", first.Clause.Marker);
        Assert.Equal("本要點依廢棄物清理法規定訂定之。", first.Clause.Body);
    }

    [Fact]
    public void ParseBlocksNumbersMultipleStatutoryParagraphsAndMarksNotes()
    {
        const string sample = """
            噪音管制區內之下列場所、工程及設施，所發出之聲音不得超出噪音管制標準：
            一、工廠（場）。
            二、娛樂場所。
            前項各款噪音管制之音量及測定之標準，由中央主管機關定之。
            備註：單位為分貝。
            """;

        var blocks = LegalArticleFormatter.ParseBlocks(sample);

        Assert.Equal(5, blocks.Count);
        var p1 = Assert.IsType<ArticleClauseBlock>(blocks[0]);
        Assert.Equal(1, p1.ParagraphOrdinal);
        Assert.True(p1.ShowParagraphBadge);

        var p2 = Assert.IsType<ArticleClauseBlock>(blocks[3]);
        Assert.Equal(2, p2.ParagraphOrdinal);
        Assert.True(p2.ShowParagraphBadge);

        var note = Assert.IsType<ArticleClauseBlock>(blocks[4]);
        Assert.Null(note.ParagraphOrdinal);
        Assert.True(note.IsNote);
    }

    [Fact]
    public void LegalCitationFormatProducesOfficialCitationsAndMojUrls()
    {
        Assert.Equal("第 5 條", LegalCitationFormat.FormatArticleNumber("5"));
        Assert.Equal("第 5 條之 1", LegalCitationFormat.FormatArticleNumber("5-1"));

        Assert.Equal("《噪音管制標準》第五條", LegalCitationFormat.FormatOfficialCitation("噪音管制標準", "5"));
        Assert.Equal("《空氣污染防制法》第三十二條", LegalCitationFormat.FormatOfficialCitation("空氣污染防制法", "32"));
        Assert.Equal("《水污染防治法》第七條之一", LegalCitationFormat.FormatOfficialCitation("水污染防治法", "7-1"));
        Assert.Equal("《廢棄物清理法》第一百零八條", LegalCitationFormat.FormatOfficialCitation("廢棄物清理法", "108"));

        var (sourceLabel, sourceUrl) = LegalCitationFormat.BuildOfficialSource(
            new PortalLawKey("TW-CENTRAL", "moj_pcode", "O0030006"), "5");
        Assert.Equal("全國法規資料庫", sourceLabel);
        Assert.Equal("https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=O0030006&flno=5", sourceUrl);
    }

    [Fact]
    public async Task CitationDrawerRendersBreadcrumbProvenanceCardAndMergedTableHtml()
    {
        const string boxContent = """
            娛樂場所、營業場所噪音管制標準值如下：
            ┌─────┬─────────────┬───────────┐
            │      頻率│ 20 Hz 至 200 Hz        │ 20 Hz 至 20 kHz       │
            │    時段  │                       │                       │
            │  音量    ├───┬───┬───┼───┬───┬───┤
            │管制區    │日間│晚間│夜間│日間│晚間│夜間│
            ├─────┼───┼───┼───┼───┼───┼───┤
            │第一類│ 32 │ 32 │ 27 │ 55 │ 50 │ 40 │
            └─────┴───┴───┴───┴───┴───┴───┘
            """;

        var article = new PortalArticleDetail(
            "https://envcite/tw/order/O0030006/2013-08-05/zho/art_5",
            new PortalLawKey("TW-CENTRAL", "moj_pcode", "O0030006"),
            "噪音管制標準",
            "5",
            boxContent,
            "zho",
            "2013-08-05",
            "regulation",
            "標準",
            null);

        var localizer = new EchoLocalizer();
        var services = new ServiceCollection()
            .AddSingleton<IStringLocalizer<Ui>>(localizer)
            .AddSingleton<BrandText>(new BrandText(Options.Create(new BrandOptions()), localizer))
            .AddSingleton<IJSRuntime>(new StubJsRuntime())
            .BuildServiceProvider();

        await using var renderer = new HtmlRenderer(services, NullLoggerFactory.Instance);
        var rawMarkup = await renderer.Dispatcher.InvokeAsync(async () =>
        {
            var parameters = ParameterView.FromDictionary(new Dictionary<string, object?>
            {
                [nameof(CitationDrawer.IsOpen)] = true,
                [nameof(CitationDrawer.Code)] = "L1",
                [nameof(CitationDrawer.Article)] = article,
            });
            var output = await renderer.RenderComponentAsync<CitationDrawer>(parameters);
            return output.ToHtmlString();
        });
        var markup = WebUtility.HtmlDecode(rawMarkup);
        Assert.Contains("噪音管制標準 第 5 條", markup, StringComparison.Ordinal);
        Assert.Contains("複製公文引述", markup, StringComparison.Ordinal);
        Assert.Contains("《噪音管制標準》第五條", markup, StringComparison.Ordinal);
        Assert.Contains("文件資訊", markup, StringComparison.Ordinal);
        Assert.Contains("現行有效", markup, StringComparison.Ordinal);
        Assert.Contains("命令・標準", markup, StringComparison.Ordinal);
        Assert.Contains("https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=O0030006&flno=5", markup, StringComparison.Ordinal);
        Assert.Contains("colspan=\"3\"", markup, StringComparison.Ordinal);
        Assert.Contains("rowspan=\"3\"", markup, StringComparison.Ordinal);
        Assert.Contains("第 1 項", markup, StringComparison.Ordinal);
        Assert.Contains("第一類", markup, StringComparison.Ordinal);
    }

    private sealed class EchoLocalizer : IStringLocalizer<Ui>
    {
        public LocalizedString this[string name] => new(name, name, false);
        public LocalizedString this[string name, params object[] arguments] =>
            new(name, string.Format(System.Globalization.CultureInfo.InvariantCulture, name, arguments), false);
        public IEnumerable<LocalizedString> GetAllStrings(bool includeParentCultures) => [];
    }

    private sealed class StubJsRuntime : IJSRuntime
    {
        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, object?[]? args) =>
            ValueTask.FromResult(default(TValue)!);

        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, CancellationToken cancellationToken, object?[]? args) =>
            ValueTask.FromResult(default(TValue)!);
    }
}