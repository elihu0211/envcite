using EnvCite.Platform.Modules.Ontology;
using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 搜尋的查詢分類：問句另走較低的語意下界。分類只看查詢字串，分錯的代價是少了增益，不會少找到
/// （路由對共用規則是超集）。題庫上的量測在 <c>services/ontology/eval/query-routing-2026-11-13.md</c>。
/// </summary>
public sealed class LawSearchRoutingTests
{
    private static readonly RelevanceFloorsOptions Shared = new() { LexicalFloor = 0.5, SemanticFloor = 0.71 };

    [Theory]
    [InlineData("固定污染源", false)]
    [InlineData("露天燃燒", false)]
    [InlineData("二氧化碳捕捉", false)]
    [InlineData("液態鑲嵌式密封的定義是什麼？", true)]
    [InlineData("違反社會救助法第33條規定，會受到什麼處罰？", true)]
    [InlineData("銀行法第120條準用了哪些條文的規定", true)]
    [InlineData("這是什麼?", true)]
    [InlineData("  露天燃燒？  ", true)]
    [InlineData("各級法院辦理民事事件遠距審理及文書傳送辦法", false)]
    [InlineData("噪音管制標準", false)]
    [InlineData("空氣污染防制法", false)]
    [InlineData("空氣污染防制法？", false)]
    [InlineData("", false)]
    public void AQueryIsAQuestionWhenItAsksOrIsLongAndIsNotALawName(string query, bool expected)
    {
        Assert.Equal(expected, new LawSearchOptions().IsQuestion(query));
    }

    [Theory]
    [InlineData("露天燃燒", 0.69f, false)]
    [InlineData("露天燃燒是什麼？", 0.69f, true)]
    [InlineData("露天燃燒是什麼？", 0.67f, false)]
    [InlineData("露天燃燒", 0.72f, true)]
    public void ASemanticOnlyHitPassesAtTheFloorOfItsQueryType(string query, float similarity, bool expected)
    {
        Assert.Equal(expected, new LawSearchOptions().Passes(Shared, query, 0f, similarity, 0f, 0));
    }

    [Fact]
    public void TheQuestionFloorIsNeverStricterThanTheSharedFloor()
    {
        var options = new LawSearchOptions { QuestionSemanticFloor = 0.9 };

        Assert.True(options.Passes(Shared, "露天燃燒是什麼？", 0f, 0.72f, 0f, 0));
        Assert.False(options.Passes(Shared, "露天燃燒是什麼？", 0f, 0.70f, 0f, 0));
    }

    [Theory]
    [InlineData("固定污染源", QueryKind.Keyword)]
    [InlineData("露天燃燒", QueryKind.Keyword)]
    [InlineData("液態鑲嵌式密封的定義是什麼？", QueryKind.Question)]
    [InlineData("銀行法第120條準用了哪些條文的規定", QueryKind.Question)]
    [InlineData("噪音管制標準", QueryKind.Name)]
    [InlineData("空氣污染防制法？", QueryKind.Name)]
    [InlineData("", QueryKind.Keyword)]
    public void AQueryIsAKeywordAQuestionOrALawName(string query, QueryKind expected)
    {
        Assert.Equal(expected, new LawSearchOptions().Classify(query));
    }

    // 片段命中（字面達標、覆蓋率 0.6）：關鍵字用較嚴的門檻 0.7 不列；問句與法規名稱用 0.5 列出。
    [Theory]
    [InlineData("露天燃燒", false)]
    [InlineData("露天燃燒是什麼？", true)]
    [InlineData("空氣污染防制法", true)]
    public void AFragmentHitNeedsTheStricterCoverageOnlyForAKeyword(string query, bool expected)
    {
        Assert.Equal(expected, new LawSearchOptions().Passes(Shared, query, 0.8f, 0f, 0.6f, 0));
    }

    [Theory]
    [InlineData("露天燃燒")]
    [InlineData("露天燃燒是什麼？")]
    [InlineData("空氣污染防制法")]
    public void AWholeTermHitIsListedForEveryKindOfQuery(string query)
    {
        // 整詞命中不靠覆蓋率門檻：收緊門檻只擋片段
        Assert.True(new LawSearchOptions().Passes(Shared, query, 0.3f, 0f, 1f, 0));
        Assert.True(new LawSearchOptions().Passes(Shared, query, 0f, 0f, 0f, 2));
    }

    [Fact]
    public void QuestionsAndNamesNeverListLessThanTheSharedRuleAndAKeywordNeverListsMore()
    {
        // 基準：關鍵字門檻與共用門檻相同、問句下界與共用下界相同，即沒有路由
        var baseline = new LawSearchOptions { KeywordMinTermCoverage = 0.5, QuestionSemanticFloor = 0.71 };
        var routed = new LawSearchOptions();
        var hits = new[]
        {
            (0.8f, 0f, 1f, 0), (0.8f, 0f, 0.6f, 0), (0.8f, 0f, 0.2f, 0), (0f, 0.8f, 0f, 0), (0f, 0f, 0f, 2),
            (0.3f, 0.5f, 1f, 0), (0.3f, 0.5f, 0.5f, 0), (0f, 0.69f, 0f, 0),
        };
        foreach (var (lexical, semantic, coverage, guidance) in hits)
        {
            var before = baseline.Passes(Shared, "露天燃燒是什麼？", lexical, semantic, coverage, guidance);
            var question = routed.Passes(Shared, "露天燃燒是什麼？", lexical, semantic, coverage, guidance);
            var name = routed.Passes(Shared, "空氣污染防制法", lexical, semantic, coverage, guidance);
            var keyword = routed.Passes(Shared, "露天燃燒", lexical, semantic, coverage, guidance);

            Assert.True(!before || question);
            Assert.Equal(baseline.Passes(Shared, "空氣污染防制法", lexical, semantic, coverage, guidance), name);
            Assert.True(!keyword || baseline.Passes(Shared, "露天燃燒", lexical, semantic, coverage, guidance));
        }
    }
}