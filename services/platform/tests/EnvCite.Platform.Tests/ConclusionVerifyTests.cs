using System.Text.Json;

using EnvCite.Platform.Modules.Consult;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 結論區塊進入已核驗的回答：結論在最前面、由系統填寫；模型的說明越界就整段丟掉，結論仍有出處地留著；
/// 文件序列化與版本讓滾動更新期間的舊版程式讀不壞。
/// </summary>
public sealed class ConclusionVerifyTests
{
    private static readonly AnswerSource L1 = new("L1", "https://envcite/tw/law/O0020001/2018-08-01/zho/art_32", "空氣污染防制法 第 32 條", "在指定清除地區內，不得從事燃燒行為。");

    private static readonly AnswerSource L2 = new("L2", "https://envcite/tw/law/O0020001/2018-08-01/zho/art_33", "空氣污染防制法 第 33 條", "另一條。");

    private static readonly ConclusionBlock Conclusions = new(
        [new Conclusion(1, ConclusionVerdict.Satisfied, ["L1"], [], [], [])],
        [new UsedFact("burning", "露天燃燒", true, null, "露天燃燒")], "2026-11-18", true, 0);

    private static ModelAnswerOutput Explanation(string text, params string[] codes) =>
        new(InsufficientEvidence: false, [new ProseBlock(null, text, codes)]);

    [Fact]
    public void TheConclusionLeadsAndTheExplanationFollows()
    {
        var answer = GroundedAnswerer.Verify(Explanation("結論 1 依據第三十二條，禁止在指定清除地區燃燒。", "L1", "L2"), [L1, L2], Conclusions);

        Assert.False(answer.Refused);
        Assert.Equal(Conclusions, answer.Document!.Blocks[0]);
        Assert.IsType<ProseBlock>(Assert.Single(answer.Document.Blocks.Skip(1)));
        Assert.Equal(["L1", "L2"], answer.Sources.Select(s => s.Code));
    }

    [Fact]
    public void AnExplanationThatRestatesTheConclusionIsDroppedAndTheConclusionStaysWithItsSource()
    {
        var answer = Verified(Explanation("本案該當構成要件。", "L1", "L2"));

        Assert.False(answer.Refused);
        Assert.Equal([Conclusions], answer.Document!.Blocks);
        Assert.Equal(["L1"], answer.Sources.Select(s => s.Code));
    }

    [Fact]
    public void AnExplanationThatCitesAnUnknownSourceIsDroppedToo()
    {
        var answer = Verified(Explanation("依據第三十二條。", "L9"));

        Assert.Equal([Conclusions], answer.Document!.Blocks);
    }

    [Fact]
    public void ARefusingOrEmptyModelStillLeavesTheConclusion()
    {
        Assert.Equal([Conclusions], Verified(new ModelAnswerOutput(InsufficientEvidence: true, [])).Document!.Blocks);
        Assert.Equal([Conclusions], Verified(new ModelAnswerOutput(InsufficientEvidence: false, [])).Document!.Blocks);
        Assert.Equal([Conclusions], Verified(null).Document!.Blocks);
    }

    [Fact]
    public void AConclusionBlockWrittenByTheModelIsDiscarded()
    {
        var forged = new ConclusionBlock([new Conclusion(1, ConclusionVerdict.NotSatisfied, ["L1"], [], [], [])], [], "2000-01-01", false, 0);
        var output = new ModelAnswerOutput(false, [forged, new ProseBlock(null, "依據第三十二條。", ["L1"])]);

        var answer = Verified(output);

        Assert.Equal(Conclusions, Assert.Single(answer.Document!.Blocks.OfType<ConclusionBlock>()));
        Assert.Equal(2, answer.Document.Blocks.Count);
    }

    [Fact]
    public void WithoutConclusionsTheVerificationIsTheOrdinaryOne()
    {
        var output = Explanation("結論 1 該當。", "L1");

        Assert.Equal(
            JsonSerializer.Serialize(GroundedAnswerer.Verify(output, [L1]), AnswerJson.Options),
            JsonSerializer.Serialize(GroundedAnswerer.Verify(output, [L1], null), AnswerJson.Options));
        Assert.Single(GroundedAnswerer.Verify(output, [L1]).Document!.Blocks);

        var refused = GroundedAnswerer.Verify(Explanation("沒有出處的話。"), [L1]);
        Assert.True(refused.Refused);
    }

    [Fact]
    public void TheConclusionBlockSurvivesJsonWithItsKindAndTheDocumentVersionTellsOldReadersToLeaveIt()
    {
        var document = new AnswerDocument([Conclusions, new ProseBlock(null, "說明", ["L1"])]);

        var json = JsonSerializer.Serialize(document, AnswerJson.Options);
        var back = JsonSerializer.Deserialize<AnswerDocument>(json, AnswerJson.Options)!;

        Assert.Contains("\"kind\":\"conclusion\"", json, StringComparison.Ordinal);
        Assert.Equal(json, JsonSerializer.Serialize(back, AnswerJson.Options));
        var conclusion = Assert.IsType<ConclusionBlock>(back.Blocks[0]);
        Assert.Equal(ConclusionVerdict.Satisfied, Assert.Single(conclusion.Items).Verdict);
        Assert.Equal(2, AnswerJson.SchemaVersionOf(document));
        Assert.Equal(1, AnswerJson.SchemaVersionOf(new AnswerDocument([new ProseBlock(null, "說明", ["L1"])])));
    }

    [Theory]
    [InlineData(null, false)]
    [InlineData((short)0, false)]
    [InlineData((short)1, true)]
    [InlineData((short)2, true)]
    [InlineData((short)3, false)]
    public void OnlyTheKnownSchemaVersionsAreReadable(short? version, bool readable) =>
        Assert.Equal(readable, AnswerJson.IsReadable(version));

    [Fact]
    public void TheCopyTextHasTheHeadingEachConclusionWithItsCitationAndTheDisclaimer()
    {
        var text = AnswerDocumentFormatter.ToCopyText(new AnswerDocument([Conclusions]));

        Assert.Equal($"{ConclusionTerms.Heading}\n結論 1：該當 [L1]\n{ConclusionTerms.Disclaimer}", text);
        Assert.Equal(["L1"], AnswerDocumentFormatter.CollectCitationCodes(new AnswerDocument([Conclusions])));
    }

    [Theory]
    [InlineData(ConclusionVerdict.Satisfied, "該當")]
    [InlineData(ConclusionVerdict.NotSatisfied, "不該當")]
    [InlineData(ConclusionVerdict.ConditionallySatisfied, "附條件該當")]
    [InlineData(ConclusionVerdict.ConditionallyNotSatisfied, "附條件不該當")]
    [InlineData(ConclusionVerdict.Undetermined, "事實不明")]
    [InlineData(ConclusionVerdict.Excluded, "但書排除")]
    [InlineData(ConclusionVerdict.NotApplicable, "不適用")]
    public void EachVerdictHasItsFixedTerm(ConclusionVerdict verdict, string term) =>
        Assert.Equal(term, ConclusionTerms.Of(verdict));

    private static GroundedAnswer Verified(ModelAnswerOutput? output) =>
        GroundedAnswerer.Verify(output, [L1, L2], Conclusions);
}