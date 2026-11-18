using EnvCite.Platform.Modules.Canon;

using V1 = EnvCite.Canon.V1;

namespace EnvCite.Platform.Tests;

/// <summary>canon 的抽取與推論結果對映成回答管線用的型別：結論只取裁處時之法律、每則陳述一則、連同它所讀條文的網址。</summary>
public sealed class CaseReasonerTests
{
    private const string Art32 = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_32/para_1";
    private const string Art33 = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_33";

    private static OutcomeDto Dto(V1.Outcome outcome) => ReasoningMapper.ToDto(new V1.EvaluateResponse { AtDecision = outcome }).AtDecision;

    [Fact]
    public void EachStatementWithASourceBecomesOneConclusionAndTheRestIsLeftOut()
    {
        var outcome = new V1.Outcome
        {
            Verdicts =
            {
                new V1.StatementVerdict { StatementId = 7, Verdict = V1.Verdict.Violation },
                new V1.StatementVerdict { StatementId = 8, Verdict = V1.Verdict.Undetermined },
                new V1.StatementVerdict { StatementId = 9, Verdict = V1.Verdict.Compliant },
            },
            Sources =
            {
                new V1.StatementSource { StatementId = 7, VersionIri = Art32 },
                new V1.StatementSource { StatementId = 8, VersionIri = Art33 },
            },
            Missing =
            {
                new V1.Gap { StatementId = 8, Concept = "burned_area", Name = "燃燒面積" },
                new V1.Gap { StatementId = 8, Concept = "burned_area", Name = "燃燒面積" },
                new V1.Gap { StatementId = 8, Concept = "open_burning", Name = "露天燃燒" },
                new V1.Gap { StatementId = 7, Concept = "liability_basis" },
            },
            NeedsAssessment = { new V1.Gap { StatementId = 8, Concept = "serious_harm", Name = "情節重大" } },
            Penalties =
            {
                new V1.PenaltyCandidate
                {
                    StatementId = 20, Punishes = 7, Effect = V1.LegalEffect.Fine, MinAmount = 60000, MaxAmount = 1000000, Conditional = true,
                },
                new V1.PenaltyCandidate { StatementId = 21, Punishes = 7, Effect = V1.LegalEffect.Order, Discretionary = true },
                new V1.PenaltyCandidate { StatementId = 22, Punishes = 99, Effect = V1.LegalEffect.Warning },
            },
        };

        var conclusions = CaseReasoner.Conclusions(Dto(outcome));

        Assert.Equal(2, conclusions.Items.Count);
        var violation = conclusions.Items[0];
        Assert.Equal((Art32, ConclusionVerdict.Satisfied), (violation.SourceUri, violation.Verdict));
        Assert.Equal(["liability_basis"], violation.MissingFacts); // 沒有名稱的概念退回用代碼
        Assert.Empty(violation.NeedsAssessment);
        Assert.Equal(
            [new PenaltyRange("Fine", 60000, 1000000, Conditional: true, Discretionary: false), new PenaltyRange("Order", null, null, Conditional: false, Discretionary: true)],
            violation.Penalties);

        var undetermined = conclusions.Items[1];
        Assert.Equal((Art33, ConclusionVerdict.Undetermined), (undetermined.SourceUri, undetermined.Verdict));
        Assert.Equal(["燃燒面積", "露天燃燒"], undetermined.MissingFacts);
        Assert.Equal(["情節重大"], undetermined.NeedsAssessment);
        Assert.Empty(undetermined.Penalties);
    }

    // 空字串是 proto3 的「沒有」：對外是 null，不是空名稱。
    [Fact]
    public void AGapWithoutANameHasNoNameOnTheRestContract()
    {
        var outcome = Dto(new V1.Outcome
        {
            Missing = { new V1.Gap { StatementId = 7, Concept = "liability_basis" }, new V1.Gap { StatementId = 7, Concept = "cod", Name = "化學需氧量" } },
        });

        Assert.Equal([new GapDto(7, "liability_basis"), new GapDto(7, "cod", "化學需氧量")], outcome.Missing);
        Assert.Null(outcome.Missing[0].Name);
    }

    [Fact]
    public void AnOutcomeWithoutVerdictsHasNoConclusions() =>
        Assert.Empty(CaseReasoner.Conclusions(Dto(new V1.Outcome())).Items);

    [Theory]
    [InlineData(VerdictDto.Violation, ConclusionVerdict.Satisfied)]
    [InlineData(VerdictDto.Compliant, ConclusionVerdict.NotSatisfied)]
    [InlineData(VerdictDto.ConditionalViolation, ConclusionVerdict.ConditionallySatisfied)]
    [InlineData(VerdictDto.ConditionalCompliant, ConclusionVerdict.ConditionallyNotSatisfied)]
    [InlineData(VerdictDto.Undetermined, ConclusionVerdict.Undetermined)]
    [InlineData(VerdictDto.Excluded, ConclusionVerdict.Excluded)]
    [InlineData(VerdictDto.NotApplicable, ConclusionVerdict.NotApplicable)]
    public void EachVerdictMapsToItsLegalTerm(VerdictDto verdict, ConclusionVerdict expected) =>
        Assert.Equal(expected, CaseReasoner.Map(verdict));

    [Fact]
    public void NoVerdictIsLeftWithoutATerm() =>
        Assert.All(Enum.GetValues<VerdictDto>(), v => Assert.True(Enum.IsDefined(CaseReasoner.Map(v))));

    [Fact]
    public void ABooleanFactAndANumericFactKeepTheirValueAndTheirQuote()
    {
        var flag = CaseReasoner.Map(new V1.ExtractedFact
        {
            Concept = "open_burning",
            Name = "露天燃燒",
            Value = new V1.FactValue { Flag = false },
            Quote = "沒有燃燒",
        });
        var number = CaseReasoner.Map(new V1.ExtractedFact
        {
            Concept = "burned_area",
            Name = "燃燒面積",
            Value = new V1.FactValue { Number = new V1.Decimal { Value = "12.5" } },
            Quote = "十二點五公頃",
        });

        Assert.Equal(new CaseFact("open_burning", "露天燃燒", false, null, "沒有燃燒"), flag);
        Assert.Equal(new CaseFact("burned_area", "燃燒面積", null, "12.5", "十二點五公頃"), number);
    }

    [Fact]
    public void AFactWithoutAUsableValueIsNotMapped()
    {
        Assert.Null(CaseReasoner.Map(new V1.ExtractedFact { Concept = "x", Name = "x", Quote = "x" }));
        Assert.Null(CaseReasoner.Map(new V1.ExtractedFact { Concept = "x", Name = "x", Value = new V1.FactValue(), Quote = "x" }));
        Assert.Null(CaseReasoner.Map(new V1.ExtractedFact { Concept = "x", Name = "x", Value = new V1.FactValue { Concept = "y" }, Quote = "x" }));
    }
}