using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

public sealed record PortalReasoningInput(
    string? ConductOn,
    string? DecidedOn,
    string? ConductEndedOn,
    bool? CriminalOffense,
    bool? CourtConfiscated,
    string? CriminalPayment,
    string? GainedBenefit,
    string? PollutantCode = null,
    string? PollutantValue = null,
    string? PollutantUnit = null,
    string? IndustryClass = null,
    bool? Intent = null,
    bool? Negligence = null,
    bool? FactMistake = null,
    bool? SelfDefense = null,
    bool? DefenseExcessive = null,
    bool? Necessity = null,
    bool? NecessityExcessive = null,
    bool? IgnoranceUnavoidable = null,
    bool? PettyMinor = null,
    bool? SelfInduced = null,
    bool? CapacityReduced = null,
    string? ScoringMatrixCode = null,
    string? DemeritPoints = null,
    string? CureDeadline = null,
    string? ReinspectionOn = null);

public sealed record PortalReasoningVerdict(string LawTitle, string StatementId, string Verdict, int MissingCount);

public sealed record PortalPenaltyItem(
    string StatementId,
    string Effect,
    long? MinAmount,
    long? MaxAmount,
    bool Conditional,
    bool PerOccasion,
    bool Discretionary);

public sealed record PortalTierDefense(string Tier, string StatementId, string Reason);

public sealed record PortalScoringCard(
    string MatrixCode,
    string LawTitle,
    string FormulaKind,
    string EquationSummary,
    long? ClampedMin,
    long? ClampedMax,
    int LectureHoursMin,
    int LectureHoursMax,
    bool? DemeritTriggered,
    string? DemeritTotal,
    IReadOnlyList<string> MissingFactors,
    IReadOnlyList<string> JudgementFactors);

public sealed record PortalReasoningOutcome(
    IReadOnlyList<PortalReasoningVerdict> Verdicts,
    int EarlierCount,
    int InvalidatedCount,
    int DelegationGapCount,
    int UncoveredLawCount,
    IReadOnlyList<PortalPenaltyItem>? Penalties = null,
    IReadOnlyList<PortalTierDefense>? ThreeTierDefenses = null,
    IReadOnlyList<PortalScoringCard>? ScoringResults = null,
    IReadOnlyList<string>? MissingConcepts = null,
    IReadOnlyList<string>? JudgementConcepts = null,
    IReadOnlyList<string>? Problems = null,
    long? OneActMin = null,
    long? OneActMax = null);

/// <summary>客戶入口「規範推論評估」應用服務（經 <c>TenantScopes</c> 綁定租戶並檢查模組權限）。</summary>
public interface INormativeReasoningService
{
    Task<Outcome<PortalReasoningOutcome>> EvaluateAsync(
        ClaimsPrincipal user, PortalReasoningInput input, CancellationToken cancellationToken);
}