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
    IReadOnlyList<PortalLawTreeNode> Children);

public sealed record PortalLawTreeResult(IReadOnlyList<PortalLawTreeNode> Roots, bool CatalogAvailable);

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
    string? GainedBenefit);

public sealed record PortalReasoningVerdict(string LawTitle, string StatementId, string Verdict, int MissingCount);

public sealed record PortalReasoningOutcome(
    IReadOnlyList<PortalReasoningVerdict> Verdicts,
    int EarlierCount,
    int InvalidatedCount,
    int DelegationGapCount,
    int UncoveredLawCount);

/// <summary>客戶入口「規範推論評估」應用服務（經 <c>TenantScopes</c> 綁定租戶並檢查模組權限）。</summary>
public interface INormativeReasoningService
{
    Task<AdminOutcome<PortalReasoningOutcome>> EvaluateAsync(
        ClaimsPrincipal user, PortalReasoningInput input, CancellationToken cancellationToken);
}