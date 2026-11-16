using EnvCite.Canon.V1;
using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Modules.Canon;

internal sealed class LawSearch(
    CanonService.CanonServiceClient client,
    IOptions<LawSearchOptions> relevance,
    IOptions<RelevanceFloorsOptions> floors,
    IUsageRecorder usage) : ILawSearch
{
    // 同 canon 的服務預設：函釋帶出的條文會讓回應多幾條，沒有指定 limit 時也截回這個數量。
    private const int DefaultLimit = 10;

    public Task<Outcome<IReadOnlyList<LawHit>>> SearchAsync(
        string? query, string? asOf, int? limit, CancellationToken cancellationToken) =>
        PlatformGrpcClient.CallAsync<IReadOnlyList<LawHit>>(OutcomeCodes.CanonUnavailable, async () =>
        {
            // 查詢全文是客戶案件的內容：不寫日誌、不放進計量；計量只記次數。
            var response = await client.SearchArticlesAsync(
                new SearchArticlesRequest { Query = query ?? string.Empty, AsOf = asOf ?? string.Empty, Limit = limit ?? 0, IncludeGuidance = true },
                cancellationToken: cancellationToken);
            await usage.RecordAsync(CanonEndpoints.SearchOperation, 1, cancellationToken);
            return
            [
                .. response.Hits
                    .Where(h => relevance.Value.Passes(floors.Value, query ?? string.Empty, h.LexicalScore, h.SemanticSimilarity, h.TermCoverage, h.GuidanceDocuments))
                    .Select(h => new LawHit(
                        h.Iri,
                        LawIdentifiers.ToDto(h.Identifier),
                        h.LawTitle,
                        h.Number,
                        h.Content,
                        h.LexicalScore,
                        h.GuidanceDocuments))
                    .Take(limit ?? DefaultLimit)
            ];
        });
}