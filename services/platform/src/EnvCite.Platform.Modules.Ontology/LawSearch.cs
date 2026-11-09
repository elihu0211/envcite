using EnvCite.Ontology.V2;
using EnvCite.Platform.SharedKernel;

using Grpc.Core;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Ontology;

internal sealed class LawSearch(
    OntologyService.OntologyServiceClient client, IOptions<OntologyOptions> options, IUsageRecorder usage) : ILawSearch
{
    public async Task<AdminOutcome<IReadOnlyList<LawHit>>> SearchAsync(
        string? query, string? asOf, int? limit, CancellationToken cancellationToken)
    {
        try
        {
            // 查詢全文是客戶案件的內容：不寫日誌、不放進計量；計量只記次數。
            var response = await client.SearchArticlesAsync(
                new SearchArticlesRequest { Query = query ?? string.Empty, AsOf = asOf ?? string.Empty, Limit = limit ?? 0 },
                deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(OntologyEndpoints.SearchOperation, 1, cancellationToken);
            return AdminOutcome.Ok<IReadOnlyList<LawHit>>(
                [.. response.Hits.Select(h => new LawHit(
                    h.Iri,
                    LawIdentifiers.ToDto(h.Identifier),
                    h.LawTitle,
                    h.Number,
                    h.Content,
                    h.LexicalScore))]);
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
        {
            return AdminOutcome.Invalid<IReadOnlyList<LawHit>>(OutcomeCodes.UpstreamInvalidRequest, ex.Status.Detail);
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
        {
            return AdminOutcome.Unavailable<IReadOnlyList<LawHit>>(OutcomeCodes.OntologyUnavailable);
        }
    }
}