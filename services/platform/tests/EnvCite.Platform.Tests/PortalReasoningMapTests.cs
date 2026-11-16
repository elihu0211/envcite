using EnvCite.Platform.Modules.Canon;

namespace EnvCite.Platform.Tests;

/// <summary>入口推論表單與結果的對映（<see cref="PortalReasoningMap"/>）：省略是未知，顯示只取入口要的計數。</summary>
public sealed class PortalReasoningMapTests
{
    private static PortalReasoningInput Input(
        string? pollutantCode = null,
        string? pollutantValue = null,
        string? pollutantUnit = null,
        string? industry = null,
        string? conductOn = null,
        string? cureDeadline = null,
        string? reinspectionOn = null,
        string? matrix = null,
        string? demerit = null,
        bool? intent = null,
        string? payment = null) =>
        new(
            conductOn, null, "  ", null, null, payment, "  ",
            pollutantCode, pollutantValue, pollutantUnit, industry,
            Intent: intent, CureDeadline: cureDeadline, ReinspectionOn: reinspectionOn,
            ScoringMatrixCode: matrix, DemeritPoints: demerit);

    [Fact]
    public void BlankTextIsOmittedAndAPollutantNeedsBothCodeAndValue()
    {
        var request = PortalReasoningMap.Request(Input(pollutantCode: "  pm ", pollutantValue: " 1 ", pollutantUnit: " ", conductOn: " 2024-01-01 "));

        Assert.Equal("2024-01-01", request.ConductOn);
        Assert.Null(request.DecidedOn);
        Assert.Null(request.ConductEndedOn);
        Assert.Null(request.ResultOn);
        var fact = Assert.Single(request.Facts!);
        Assert.Equal("pm", fact.Concept);
        Assert.Equal(["1"], fact.Numbers);
        Assert.Null(fact.Unit);
        Assert.Null(request.AdministrativePenalty!.AgentFault);
        Assert.Null(request.AdministrativePenalty.CriminalPayment);
        Assert.Null(request.Timeline);
        Assert.Null(request.ScoringInputs);
    }

    [Fact]
    public void ACodeWithoutAValueAddsNoFact() =>
        Assert.Empty(PortalReasoningMap.Request(Input(pollutantCode: "pm")).Facts!);

    [Fact]
    public void IndustryTimelineAndScoringAreAddedOnlyWhenProvided()
    {
        var request = PortalReasoningMap.Request(Input(
            industry: " A ",
            cureDeadline: " 2024-02-01 ",
            matrix: " noise ",
            demerit: " 2 ",
            intent: true,
            payment: " 10 "));

        Assert.Equal(["industry_class"], request.Facts!.Select(f => f.Concept));
        Assert.Equal(["A"], request.Facts!.Single().Concepts);
        Assert.True(request.AdministrativePenalty!.Intent);
        Assert.Equal("10", request.AdministrativePenalty.CriminalPayment);
        Assert.Equal("2024-02-01", request.Timeline!.CureDeadline);
        Assert.Null(request.Timeline.ReinspectionOn);
        var scoring = Assert.Single(request.ScoringInputs!);
        var factor = Assert.Single(scoring.Factors!);
        Assert.Equal("noise", scoring.MatrixCode);
        Assert.Equal(("demerit_points", "2"), (factor.FactorCode, factor.Value));
    }

    [Fact]
    public void DemeritPointsWithoutAMatrixAreNotSent() =>
        Assert.Null(PortalReasoningMap.Request(Input(demerit: "2")).ScoringInputs);

    [Fact]
    public void TheOutcomeKeepsCountsDefensesAndFallsBackWhenAVerdictHasNoSource()
    {
        var outcome = new OutcomeDto(
            [new StatementVerdictDto(7, VerdictDto.Violation), new StatementVerdictDto(8, VerdictDto.Undetermined)],
            [new PenaltyCandidateDto(3, 7, LegalEffectDto.Fine, 100, null, true, false, true, [], [])],
            new FineIntervalDto(100, 900),
            [new GapDto(7, "value"), new GapDto(7, "value"), new GapDto(8, "method")],
            [new GapDto(8, "method"), new GapDto(9, "site")],
            [],
            [new ProblemDto(ProblemKindDto.BadDate, null, 4), new ProblemDto(ProblemKindDto.ContradictoryFact, null, null)],
            [], [], [], [], [], [],
            new GeneralPartDto(
                [],
                [new PenaltyMitigationDto(3, MitigationDto.Youth)],
                [], [], [], [], [],
                [new TierExcuseDto(3, "PursuantToLaw")],
                [new TierExcuseDto(3, "NoFault")]),
            [new StatementSourceDto(7, "https://example.test/7")],
            [new LawIdentifierDto("TW", "pcode", "O0020001")],
            [], [], [], [],
            [new InvalidatedStatementDto(1, "iri", "釋字1", "2020-01-01", false)],
            [new DelegationBasisDto(1, true, []), new DelegationBasisDto(2, false, [])]);

        var view = PortalReasoningMap.Outcome(new ReasoningResultDto(outcome, [Earlier()]));

        Assert.Equal(
            [("https://example.test/7", "7", "Violation", 2), ("-", "8", "Undetermined", 1)],
            view.Verdicts.Select(v => (v.LawTitle, v.StatementId, v.Verdict, v.MissingCount)));
        var penalty = Assert.Single(view.Penalties!);
        Assert.Equal(("3", "Fine", 100L, null, true, false, true), (penalty.StatementId, penalty.Effect, penalty.MinAmount, penalty.MaxAmount, penalty.Conditional, penalty.PerOccasion, penalty.Discretionary));
        Assert.Equal(
            [("Tier2_Unlawfulness", "PursuantToLaw"), ("Tier3_Culpability", "NoFault"), ("Mitigation", "Youth")],
            view.ThreeTierDefenses!.Select(d => (d.Tier, d.Reason)));
        Assert.Equal(["value", "method"], view.MissingConcepts);
        Assert.Equal(["method", "site"], view.JudgementConcepts);
        Assert.Equal(["BadDate: 4", "ContradictoryFact: -"], view.Problems);
        Assert.Equal(1, view.EarlierCount);
        Assert.Equal(1, view.InvalidatedCount);
        Assert.Equal(1, view.DelegationGapCount);
        Assert.Equal(1, view.UncoveredLawCount);
        Assert.Equal(100, view.OneActMin);
        Assert.Equal(900, view.OneActMax);
        Assert.Empty(view.ScoringResults!);
    }

    private static EarlierOutcomeDto Earlier() => new("2020-01-01", new OutcomeDto(
        [], [], null, [], [], [], [], [], [], [], [], [], [],
        new GeneralPartDto([], [], [], [], [], [], []),
        [], [], [], [], [], [], [], []));
}