using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

public sealed record PortalArticleDetail(
    string Uri,
    PortalLawKey Identifier,
    string LawTitle,
    string Number,
    string Content,
    string Language,
    string? DateDocument,
    string NormKind,
    string DocumentType = "",
    string? RepealedOn = null);

/// <summary>依條文索引（標準 IRI）查詢單一條文全文，供問答引用抽屜按需載入。</summary>
public interface ICitationArticleService
{
    Task<AdminOutcome<PortalArticleDetail>> GetByIriAsync(
        ClaimsPrincipal user, string? iri, CancellationToken cancellationToken);
}