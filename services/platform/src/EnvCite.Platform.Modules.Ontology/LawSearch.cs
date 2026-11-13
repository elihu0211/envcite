using EnvCite.Ontology.V2;

using Grpc.Core;

namespace EnvCite.Platform.Modules.Ontology;

internal sealed class LawSearch(
    OntologyService.OntologyServiceClient client,
    IOptions<OntologyOptions> options,
    IOptions<LawSearchOptions> relevance,
    IUsageRecorder usage) : ILawSearch
{
    // 同 ontology 的服務預設：函釋帶出的條文會讓回應多幾條，沒有指定 limit 時也截回這個數量。
    private const int DefaultLimit = 10;

    public async Task<AdminOutcome<IReadOnlyList<LawHit>>> SearchAsync(
        string? query, string? asOf, int? limit, CancellationToken cancellationToken)
    {
        try
        {
            // 查詢全文是客戶案件的內容：不寫日誌、不放進計量；計量只記次數。
            var response = await client.SearchArticlesAsync(
                new SearchArticlesRequest { Query = query ?? string.Empty, AsOf = asOf ?? string.Empty, Limit = limit ?? 0, IncludeGuidance = true },
                deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(OntologyEndpoints.SearchOperation, 1, cancellationToken);
            return AdminOutcome.Ok<IReadOnlyList<LawHit>>(
                [.. response.Hits.Where(h => relevance.Value.Passes(h.LexicalScore, h.SemanticSimilarity, h.TermCoverage, h.GuidanceDocuments)).Select(h => new LawHit(
                    h.Iri,
                    LawIdentifiers.ToDto(h.Identifier),
                    h.LawTitle,
                    h.Number,
                    h.Content,
                    h.LexicalScore,
                    h.GuidanceDocuments)).Take(limit ?? DefaultLimit)]);
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
        {
            return AdminOutcome.Invalid<IReadOnlyList<LawHit>>(OutcomeCodes.UpstreamInvalidRequest, ex.Status.Detail);
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable
            or StatusCode.DeadlineExceeded
            or StatusCode.ResourceExhausted)
        {
            return AdminOutcome.Unavailable<IReadOnlyList<LawHit>>(OutcomeCodes.OntologyUnavailable);
        }
    }
}