using EnvCite.Platform.Modules.Consult;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 結論區塊的組裝與說明的越界核對：結論只由系統填寫，所依條文都在本次證據內；模型的說明不能重述或改寫結論。
/// </summary>
public sealed class ConclusionAssemblyTests
{
    private const string Art5 = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_5";
    private const string Art7 = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_7";
    private const string Art32 = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_32";

    private static AnswerSource Source(string code, string iri) => new(code, iri, $"條文 {code}", "內容");

    private static StatementConclusion Statement(
        string uri, ConclusionVerdict verdict = ConclusionVerdict.Satisfied, string[]? missing = null, string[]? needsAssessment = null) =>
        new(uri, verdict, missing ?? [], needsAssessment ?? [], []);

    private static ConclusionBlock? Assemble(
        IReadOnlyList<AnswerSource> sources, params StatementConclusion[] items) =>
        ConclusionAssembly.Assemble(new CaseConclusions(items), [], sources, "2026-11-18", asOfAssumed: true);

    private static ConclusionBlock Block(params Conclusion[] items) => new(items, [], "2026-11-18", true, 0);

    private static Conclusion Item(int no, ConclusionVerdict verdict = ConclusionVerdict.Satisfied) =>
        new(no, verdict, ["L1"], [], [], []);

    [Theory]
    [InlineData(Art32, Art32, true)]
    [InlineData(Art32, Art32 + "/para_1", true)]
    [InlineData(Art32, Art32 + "/para_1/subpara_2", true)]
    [InlineData("https://envcite/tw/law/O0020001/2018-08-01/zho/art_3", Art32, false)]
    [InlineData(Art32, "https://envcite/tw/law/O0020001/2018-08-01/zho/art_3", false)]
    [InlineData(Art32, "https://envcite/tw/law/O0020001/2018-08-01/zho/art_32-1", false)]
    public void AnEvidenceArticleCoversItselfAndWhatIsUnderItNothingElse(string evidence, string statement, bool covered) =>
        Assert.Equal(covered, ConclusionAssembly.Covers(evidence, statement));

    [Fact]
    public void AConclusionCitesTheEvidenceThatCoversItsStatement()
    {
        var block = Assemble([Source("L1", Art32)], Statement(Art32 + "/para_1"));

        var item = Assert.Single(block!.Items);
        Assert.Equal((1, ConclusionVerdict.Satisfied), (item.No, item.Verdict));
        Assert.Equal(["L1"], item.CitationCodes);
        Assert.Equal(0, block.OmittedCount);
    }

    [Fact]
    public void ConclusionsOutsideTheEvidenceAndNotApplicableOnesAreCountedNotListed()
    {
        var block = Assemble(
            [Source("L1", Art32)],
            Statement(Art32 + "/para_1"),
            Statement(Art32 + "/para_2", ConclusionVerdict.NotApplicable),
            Statement(Art7 + "/para_1"));

        Assert.Single(block!.Items);
        Assert.Equal(2, block.OmittedCount);
    }

    [Fact]
    public void WithNothingInsideTheEvidenceThereIsNoBlock()
    {
        Assert.Null(Assemble([Source("L1", Art32)], Statement(Art7), Statement(Art5, ConclusionVerdict.NotApplicable)));
        Assert.Null(Assemble([Source("L1", Art32)]));
    }

    [Fact]
    public void ConclusionsFollowTheEvidenceOrderAndAreNumberedFromOne()
    {
        var block = Assemble(
            [Source("L1", Art5), Source("L2", Art7)],
            Statement(Art7 + "/para_1", ConclusionVerdict.NotSatisfied),
            Statement(Art5 + "/para_1", ConclusionVerdict.Undetermined),
            Statement(Art7 + "/para_2", ConclusionVerdict.Excluded));

        Assert.Equal(
            [(1, ConclusionVerdict.Undetermined, "L1"), (2, ConclusionVerdict.NotSatisfied, "L2"), (3, ConclusionVerdict.Excluded, "L2")],
            block!.Items.Select(i => (i.No, i.Verdict, Assert.Single(i.CitationCodes))));
    }

    [Fact]
    public void TheMissingFactsAndTheAssessmentRequirementsTravelWithTheirConclusion()
    {
        var block = Assemble(
            [Source("L1", Art32)],
            Statement(Art32, ConclusionVerdict.Undetermined, missing: ["burning"], needsAssessment: ["serious_harm"]));

        var item = Assert.Single(block!.Items);
        Assert.Equal(["burning"], item.MissingFacts);
        Assert.Equal(["serious_harm"], item.NeedsAssessment);
    }

    [Fact]
    public void TheFactsTheDateAndWhetherItWasAssumedAreDisclosedOnTheBlock()
    {
        IReadOnlyList<CaseFact> facts = [new("burning", "露天燃燒", true, null, "露天燃燒"), new("area", "面積", null, "12", "十二公頃")];

        var block = ConclusionAssembly.Assemble(
            new CaseConclusions([Statement(Art32)]), facts, [Source("L1", Art32)], "2020-03-01", asOfAssumed: false);

        Assert.Equal(("2020-03-01", false), (block!.AsOf, block.AsOfAssumed));
        Assert.Equal(
            [new UsedFact("burning", "露天燃燒", true, null, "露天燃燒"), new UsedFact("area", "面積", null, "12", "十二公頃")], block.Facts);
    }

    [Fact]
    public void ThePromptShowsTheBasisAndTheGapsButNoVerdictTerm()
    {
        var block = Assemble(
            [Source("L1", Art32)],
            Statement(Art32, ConclusionVerdict.Undetermined, missing: ["burning"], needsAssessment: ["serious_harm"]))!;

        var section = ConclusionAssembly.PromptSection(block);

        Assert.Contains("結論 1：依據 [L1]", section, StringComparison.Ordinal);
        Assert.Contains("欠缺事實 burning", section, StringComparison.Ordinal);
        Assert.Contains("有待主管機關判斷的要件 serious_harm", section, StringComparison.Ordinal);
        Assert.All(ConclusionTerms.Forbidden, word => Assert.DoesNotContain(word, section, StringComparison.Ordinal));
    }

    [Theory]
    [InlineData("依第三十二條規定，指定清除地區內不得從事燃燒行為。[L1]")]
    [InlineData("違反第三十二條規定者，處罰鍰。")]
    [InlineData("違反本法規定者，依第五條處罰。")]
    [InlineData("結論 1 依據的條文規定了禁止的行為。[L1]")]
    [InlineData("結論1 的依據見 [L1]。")]
    public void AnExplanationThatOnlyExplainsTheBasisDoesNotOverstep(string text) =>
        Assert.False(ConclusionAssembly.Oversteps([text], Block(Item(1))));

    [Theory]
    [InlineData("本案該當構成要件。")]
    [InlineData("這個行為不該當。")]
    [InlineData("結論附條件該當，但書待查。")]
    [InlineData("事實不明，無法判斷。")]
    [InlineData("本文被但書排除。")]
    [InlineData("本案構成違反。")]
    [InlineData("你已違反，應處罰鍰。")]
    [InlineData("這是違法行為。")]
    [InlineData("本案應罰。")]
    [InlineData("結論 2 也成立。")]
    public void AnExplanationThatRestatesRewritesOrInventsAConclusionOversteps(string text) =>
        Assert.True(ConclusionAssembly.Oversteps([text], Block(Item(1))));

    [Fact]
    public void OneOverstepInAnyPieceOfTextIsEnough() =>
        Assert.True(ConclusionAssembly.Oversteps(["依據條文說明。", "接著說明。", "所以該當。"], Block(Item(1))));

    [Fact]
    public void TextOfCoversEveryTextBearingBlock()
    {
        var texts = ConclusionAssembly.TextOf(new CalloutBlock(CalloutTone.Caution, "標題", "內文", ["L1"])).ToList();
        Assert.Equal(["標題", "內文"], texts);

        Assert.Equal(["小標", "段落"], ConclusionAssembly.TextOf(new ProseBlock("小標", "段落", ["L1"])));
        Assert.Equal(["段落"], ConclusionAssembly.TextOf(new ProseBlock(null, "段落", ["L1"])));
        Assert.Equal(["欄一", "欄二", "格一", "格二"], ConclusionAssembly.TextOf(
            new ComparisonTableBlock(null, ["欄一", "欄二"], [new ComparisonRow(["格一", "格二"], ["L1"])])));
    }
}