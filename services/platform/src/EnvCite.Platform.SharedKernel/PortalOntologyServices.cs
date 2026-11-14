using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

public sealed record PortalLawArticle(string Uri, string Number);

public sealed record PortalStructureNode(
    string Kind,
    string HeadingNo,
    string Title,
    IReadOnlyList<PortalStructureNode> Children,
    IReadOnlyList<PortalLawArticle> Articles);

public sealed record PortalLawTreeNode(
    string Jurisdiction,
    string IdScheme,
    string LocalId,
    string Title,
    string NormKind,
    string? DateDocument,
    IReadOnlyList<PortalLawTreeNode> Children,
    string DocumentType = "",
    int ChildCount = 0,
    int ParentCount = 0,
    int GuidanceCount = 0);

public sealed record PortalLawTreeResult(IReadOnlyList<PortalLawTreeNode> Roots, bool CatalogAvailable);

public sealed record PortalLawKey(string Jurisdiction, string IdScheme, string LocalId);

public sealed record PortalLawGraphNode(
    PortalLawKey Key,
    string Uri,
    string Title,
    string NormKind,
    string DocumentType,
    string? DateDocument,
    int HierarchyTier,
    int TotalChildren,
    int TruncatedChildren,
    int ArticleCount,
    int InvalidationCount,
    int GuidanceCount);

public sealed record PortalLawGraphEdge(
    PortalLawKey Source,
    PortalLawKey Target,
    string Kind,
    IReadOnlyList<string> AnchorArticles,
    bool AllRooted);

public sealed record PortalArticleViolationLink(
    string PenaltyArticle,
    string PenaltyPath,
    string Kind,
    string TargetArticle,
    string TargetPath);

public sealed record PortalGuidanceLink(
    string ArticleNumber,
    string Path,
    string DocumentNo,
    string Issuer,
    string IssuedOn,
    string Subject,
    string SourceUrl);

public sealed record PortalChildGroupCount(string DocumentType, int Count);

public sealed record PortalLawGraphQuery(
    PortalLawKey? Focus,
    string? AsOf = null,
    string? CategoryPrefix = null,
    int UpstreamDepth = 2,
    int DownstreamDepth = 2,
    int ChildLimit = 20,
    string? ChildDocumentType = null);

public sealed record PortalLawGraphResult(
    bool Available,
    PortalLawKey? Focus,
    IReadOnlyList<PortalLawGraphNode> Nodes,
    IReadOnlyList<PortalLawGraphEdge> Edges,
    IReadOnlyList<PortalArticleViolationLink> ViolationLinks,
    IReadOnlyList<PortalGuidanceLink> GuidanceLinks,
    IReadOnlyList<PortalChildGroupCount> ChildGroups);

public sealed record PortalLawCorpusResult(
    string Title,
    string Language,
    string Uri,
    string? DateDocument,
    IReadOnlyList<PortalLawArticle> Articles,
    IReadOnlyList<PortalStructureNode> Structure);

/// <summary>客戶入口「法規沿革與條文瀏覽」應用服務（經 <c>TenantScopes</c> 綁定租戶並檢查模組權限）。</summary>
public interface ILawCorpusService
{
    Task<AdminOutcome<PortalLawTreeResult>> TreeAsync(
        ClaimsPrincipal user, string? jurisdiction, string? asOf, CancellationToken cancellationToken);

    Task<AdminOutcome<PortalLawTreeResult>> TreeByCategoryAsync(
        ClaimsPrincipal user, string? jurisdiction, string? categoryPrefix, string? asOf, CancellationToken cancellationToken);

    Task<AdminOutcome<PortalLawGraphResult>> GraphAsync(
        ClaimsPrincipal user, PortalLawGraphQuery query, CancellationToken cancellationToken);

    Task<AdminOutcome<PortalLawCorpusResult>> BrowseAsync(
        ClaimsPrincipal user, string? jurisdiction, string? idScheme, string? localId, string? asOf, CancellationToken cancellationToken);
}

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
    Task<AdminOutcome<PortalReasoningOutcome>> EvaluateAsync(
        ClaimsPrincipal user, PortalReasoningInput input, CancellationToken cancellationToken);
}