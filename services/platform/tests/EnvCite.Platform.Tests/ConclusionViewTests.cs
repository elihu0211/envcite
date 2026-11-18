using System.Net;

using EnvCite.Platform.Web.Components;
using EnvCite.Platform.Web.Components.Shared;
using EnvCite.Platform.Web.Components.Shared.Answer;
using EnvCite.Platform.Web.Resources;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Logging.Abstractions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口對結論區塊的呈現：用語與系統輸出的是同一組、顏色不暗示違法或合法、採用的事實與日期的假設都揭露、
/// 客戶案情與模型文字一律跳脫。
/// </summary>
public sealed class ConclusionViewTests
{
    private static readonly AnswerSource Source = new("L1", "https://example.test/law/32", "空氣污染防制法 第 32 條", "在指定清除地區內，不得從事燃燒行為。");

    private static Conclusion Item(
        int no, ConclusionVerdict verdict = ConclusionVerdict.Satisfied, string[]? codes = null,
        string[]? missing = null, string[]? assessment = null, PenaltyRange[]? penalties = null) =>
        new(no, verdict, codes ?? ["L1"], missing ?? [], assessment ?? [], penalties ?? []);

    private static ConclusionBlock Block(
        Conclusion[] items, UsedFact[]? facts = null, bool asOfAssumed = true, int omitted = 0) =>
        new(items, facts ?? [], "2026-11-18", asOfAssumed, omitted);

    private static async Task<(string Html, string Raw, List<AnswerSource> Clicked)> RenderAsync(params AnswerBlock[] blocks)
    {
        var localizer = new KeyLocalizer();
        var services = new ServiceCollection()
            .AddSingleton<IStringLocalizer<Ui>>(localizer)
            .AddSingleton(new BrandText(Options.Create(new BrandOptions()), localizer))
            .BuildServiceProvider();
        List<AnswerSource> clicked = [];

        await using var renderer = new HtmlRenderer(services, NullLoggerFactory.Instance);
        var raw = await renderer.Dispatcher.InvokeAsync(async () =>
        {
            var parameters = ParameterView.FromDictionary(new Dictionary<string, object?>
            {
                [nameof(FormattedAnswerView.Document)] = new AnswerDocument(blocks),
                [nameof(FormattedAnswerView.Sources)] = (IReadOnlyList<AnswerSource>)[Source],
                [nameof(FormattedAnswerView.OnCitationSelect)] = EventCallback.Factory.Create<AnswerSource>(
                    services, s => { clicked.Add(s); return Task.CompletedTask; }),
            });
            return (await renderer.RenderComponentAsync<FormattedAnswerView>(parameters)).ToHtmlString();
        });
        return (WebUtility.HtmlDecode(raw), raw, clicked);
    }

    [Theory]
    [InlineData(ConclusionVerdict.Satisfied)]
    [InlineData(ConclusionVerdict.NotSatisfied)]
    [InlineData(ConclusionVerdict.ConditionallySatisfied)]
    [InlineData(ConclusionVerdict.ConditionallyNotSatisfied)]
    [InlineData(ConclusionVerdict.Undetermined)]
    [InlineData(ConclusionVerdict.Excluded)]
    [InlineData(ConclusionVerdict.NotApplicable)]
    public async Task EachVerdictIsShownWithTheTermTheSystemFixed(ConclusionVerdict verdict)
    {
        var (html, _, _) = await RenderAsync(Block([Item(1, verdict)]));

        Assert.Contains($">{ConclusionTerms.Of(verdict)}</span>", html, StringComparison.Ordinal);
        Assert.Contains($"data-tone=\"{ConclusionLabels.Tone(verdict)}\"", html, StringComparison.Ordinal);
    }

    private static readonly string[] CalmTones = ["warning", "info", "neutral"];

    // 紅色讀起來像「違法」、綠色讀起來像「合法」；結論只說構成要件該不該當。
    [Fact]
    public void NoVerdictIsShownInTheToneOfIllegalityOrLegality() =>
        Assert.All(Enum.GetValues<ConclusionVerdict>(), v => Assert.Contains(ConclusionLabels.Tone(v), CalmTones));

    [Fact]
    public async Task TheHeadingAndTheDisclaimerAreTheSystemsFixedWording()
    {
        var (html, _, _) = await RenderAsync(Block([Item(1)]));

        Assert.Contains(ConclusionTerms.Heading, html, StringComparison.Ordinal);
        Assert.Contains(ConclusionTerms.Disclaimer, html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task EachConclusionShowsItsNumberItsBasisAndWhatIsMissing()
    {
        var (html, _, clicked) = await RenderAsync(Block(
        [
            Item(1, ConclusionVerdict.Undetermined, missing: ["化學需氧量"], assessment: ["情節重大"]),
            Item(2, ConclusionVerdict.Satisfied, codes: ["L1", "L9"]),
        ]));

        Assert.Contains("結論 1", html, StringComparison.Ordinal);
        Assert.Contains("結論 2", html, StringComparison.Ordinal);
        Assert.Contains("欠缺的事實", html, StringComparison.Ordinal);
        Assert.Contains("<span class=\"ep-ans-concept\">化學需氧量</span>", html, StringComparison.Ordinal);
        Assert.Contains("有待主管機關判斷的要件", html, StringComparison.Ordinal);
        Assert.Contains("<span class=\"ep-ans-concept\">情節重大</span>", html, StringComparison.Ordinal);
        Assert.Contains("<button type=\"button\" class=\"ep-cite-inline\"", html, StringComparison.Ordinal);
        Assert.Contains("<span class=\"ep-cite-inline ep-cite-static\">L9</span>", html, StringComparison.Ordinal);
        Assert.Empty(clicked);
    }

    [Fact]
    public async Task PenaltiesShowTheStatutoryRangeAndWhetherTheyAreConditionalOrDiscretionary()
    {
        var (html, _, _) = await RenderAsync(Block(
        [
            Item(1, penalties:
            [
                new PenaltyRange("Fine", 60000, 1000000, Conditional: true, Discretionary: false),
                new PenaltyRange("Fine", 100, null, Conditional: false, Discretionary: false),
                new PenaltyRange("Fine", null, 500, Conditional: false, Discretionary: false),
                new PenaltyRange("Order", null, null, Conditional: false, Discretionary: true),
                new PenaltyRange("SomethingNew", null, null, Conditional: false, Discretionary: false),
            ]),
        ]));

        Assert.Contains("候選罰則（額度是法定範圍，不是建議值）", html, StringComparison.Ordinal);
        Assert.Contains("<li>罰鍰 · 新臺幣 60,000 元至 1,000,000 元 · 附條件</li>", html, StringComparison.Ordinal);
        Assert.Contains("<li>罰鍰 · 新臺幣 100 元以上</li>", html, StringComparison.Ordinal);
        Assert.Contains("<li>罰鍰 · 新臺幣 500 元以下</li>", html, StringComparison.Ordinal);
        Assert.Contains("<li>命令（非裁罰性） · 得裁量</li>", html, StringComparison.Ordinal);
        Assert.Contains("<li>SomethingNew</li>", html, StringComparison.Ordinal); // 認不得的效果原樣顯示，不吞掉
    }

    [Fact]
    public async Task TheFactsUsedAndTheirQuotesAreDisclosedWithTheDateAndWhetherItWasAssumed()
    {
        var (html, _, _) = await RenderAsync(Block(
            [Item(1)],
            facts: [new UsedFact("open_burning", "露天燃燒", true, null, "露天燃燒"), new UsedFact("burned_area", "燃燒面積", null, "12", "十二公頃"), new UsedFact("x", "否認的事實", false, null, "沒有")],
            omitted: 2));

        Assert.Contains("<details class=\"ep-ans-conclusion-facts\">", html, StringComparison.Ordinal);
        Assert.Contains("所採用的事實（3 項）", html, StringComparison.Ordinal);
        Assert.Contains("<strong>露天燃燒</strong>：是", html, StringComparison.Ordinal);
        Assert.Contains("<strong>燃燒面積</strong>：12", html, StringComparison.Ordinal);
        Assert.Contains("<strong>否認的事實</strong>：否", html, StringComparison.Ordinal);
        Assert.Contains("「十二公頃」", html, StringComparison.Ordinal);
        Assert.Contains("推論日期 2026-11-18（問句沒有明說日期，以今日為準）", html, StringComparison.Ordinal);
        Assert.Contains("另有 2 則結論所依的條文不在本次引用內，未列出。", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ADateTheUserChoseIsNotCalledAnAssumptionAndNothingOmittedIsNotMentioned()
    {
        var (html, _, _) = await RenderAsync(Block([Item(1)], asOfAssumed: false));

        Assert.Contains("推論日期 2026-11-18", html, StringComparison.Ordinal);
        Assert.DoesNotContain("以今日為準", html, StringComparison.Ordinal);
        Assert.DoesNotContain("未列出", html, StringComparison.Ordinal);
        Assert.DoesNotContain("<details", html, StringComparison.Ordinal); // 沒有事實就沒有揭露區
    }

    [Fact]
    public async Task TheConclusionIsDrawnBeforeTheModelsExplanationAndApartFromIt()
    {
        var (html, _, _) = await RenderAsync(
            Block([Item(1)]),
            new ProseBlock(null, "結論 1 依據的條文禁止燃燒。", ["L1"]));

        Assert.True(html.IndexOf("class=\"ep-ans-conclusion\"", StringComparison.Ordinal) < html.IndexOf("class=\"ep-ans-prose\"", StringComparison.Ordinal));
    }

    // 問句與抽出的事實是客戶輸入，概念代碼來自推理機：全部當文字，不是標記。
    [Fact]
    public async Task CustomerTextAndCodesAreEscaped()
    {
        var (_, raw, _) = await RenderAsync(Block(
            [Item(1, missing: ["<b>x</b>"], penalties: [new PenaltyRange("<i>y</i>", null, null, false, false)])],
            facts: [new UsedFact("c", "<script>alert(1)</script>", true, null, "<img src=x onerror=alert(2)>")]));

        Assert.DoesNotContain("<script>", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("<img", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("<b>x</b>", raw, StringComparison.Ordinal);
        Assert.DoesNotContain("<i>y</i>", raw, StringComparison.Ordinal);
    }

    [Fact]
    public void TheEnglishLabelsNeverClaimIllegalityEither()
    {
        var english = System.Xml.Linq.XDocument.Load(UiResourcePath()).Root!.Elements("data")
            .ToDictionary(e => (string)e.Attribute("name")!, e => (string)e.Element("value")!, StringComparer.Ordinal);

        Assert.All(
            Enum.GetValues<ConclusionVerdict>().Select(v => english[ConclusionTerms.Of(v)]),
            text => Assert.DoesNotMatch("(?i)illegal|unlawful|violat|punish", text));
    }

    private static string UiResourcePath([System.Runtime.CompilerServices.CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Resources", "Ui.en.resx"));
}