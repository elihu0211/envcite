using EnvCite.Ontology.V2;

using Grpc.Core;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>
/// 取得法規、列出條文、對案件事實推論：對外 REST 與 MCP 工具共用這一份，所以輸入檢查、錯誤與計量（成功才記）只有一個實作。
/// 內部服務的錯誤在這裡轉成結果代碼，呼叫端只決定怎麼呈現（HTTP 狀態或工具結果）。以 scoped 註冊（計量需要目前請求的租戶）。
/// </summary>
internal sealed class OntologyGateway(
    OntologyService.OntologyServiceClient ontology,
    ReasoningService.ReasoningServiceClient reasoning,
    IOptions<OntologyOptions> options,
    IUsageRecorder usage)
{
    public async Task<AdminOutcome<(IReadOnlyList<LawSummaryDto> Laws, bool Available)>> ListLawsAsync(
        string? jurisdiction, string? asOf, CancellationToken cancellationToken) =>
        await CallAsync(OntologyBusy, async () =>
        {
            try
            {
                var response = await ontology.ListLawsAsync(
                    new ListLawsRequest { Jurisdiction = jurisdiction ?? string.Empty, AsOf = asOf ?? string.Empty },
                    deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: cancellationToken);
                IReadOnlyList<LawSummaryDto> items =
                [
                    .. response.Laws.Select(l => new LawSummaryDto(
                        LawIdentifiers.ToDto(l.Identifier),
                        l.Iri,
                        l.Title,
                        l.NormKind,
                        string.IsNullOrEmpty(l.DateDocument) ? null : l.DateDocument,
                        l.ParentIdentifier is null ? null : LawIdentifiers.ToDto(l.ParentIdentifier)))
                ];
                return (items, true);
            }
            catch (RpcException ex) when (ex.StatusCode == StatusCode.Unimplemented)
            {
                return ((IReadOnlyList<LawSummaryDto>)[], false);
            }
        });

    public async Task<AdminOutcome<LawDto>> GetLawAsync(
        string? jurisdiction, string? idScheme, string? localId, string? asOf, CancellationToken cancellationToken) =>
        await CallAsync(OntologyBusy, async () =>
        {
            var response = await ontology.GetLawAsync(
                new GetLawRequest { Identifier = IdentifierOf(jurisdiction, idScheme, localId), AsOf = asOf ?? string.Empty },
                deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(OntologyEndpoints.GetLawOperation, 1, cancellationToken);
            var law = response.Law;
            return new LawDto(
                LawIdentifiers.ToDto(law.Identifier),
                law.Iri,
                law.Title,
                law.Language,
                string.IsNullOrEmpty(law.DateDocument) ? null : law.DateDocument);
        });

    public async Task<AdminOutcome<IReadOnlyList<ArticleDto>>> ListArticlesAsync(
        string? jurisdiction, string? idScheme, string? localId, string? asOf, CancellationToken cancellationToken) =>
        await CallAsync<IReadOnlyList<ArticleDto>>(OntologyBusy, async () =>
        {
            var response = await ontology.ListArticlesAsync(
                new ListArticlesRequest { Identifier = IdentifierOf(jurisdiction, idScheme, localId), AsOf = asOf ?? string.Empty },
                deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(OntologyEndpoints.ListArticlesOperation, 1, cancellationToken);
            return [.. response.Articles.Select(a => new ArticleDto(a.Iri, a.Number))];
        });

    public async Task<AdminOutcome<LawStructureDto>> ListLawStructureAsync(
        string? jurisdiction, string? idScheme, string? localId, string? asOf, CancellationToken cancellationToken) =>
        await CallAsync(OntologyBusy, async () =>
        {
            var response = await ontology.ListArticlesAsync(
                new ListArticlesRequest { Identifier = IdentifierOf(jurisdiction, idScheme, localId), AsOf = asOf ?? string.Empty },
                deadline: DateTime.UtcNow.Add(options.Value.Timeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(OntologyEndpoints.ListArticlesOperation, 1, cancellationToken);
            return new LawStructureDto(
                [.. response.Articles.Select(a => new ArticleWithSortDto(a.Iri, a.Number, [.. a.SortKey]))],
                [.. response.Headings.Select(h => new StructureHeadingDto(
                    HeadingKindName(h.Kind),
                    h.HeadingNo,
                    h.Title,
                    [.. h.SortKey],
                    h.ParentIndex))]);
        });

    private static string HeadingKindName(HeadingKind kind) => kind switch
    {
        HeadingKind.Part => "part",
        HeadingKind.Chapter => "chapter",
        HeadingKind.Section => "section",
        HeadingKind.Subsection => "subsection",
        HeadingKind.ItemHeading => "item_heading",
        _ => "chapter",
    };

    public async Task<AdminOutcome<ReasoningResultDto>> EvaluateAsync(EvaluateReasoningRequest request, CancellationToken cancellationToken) =>
        await CallAsync(OutcomeCodes.ReasoningBusy, async () =>
        {
            var response = await reasoning.EvaluateAsync(
                ReasoningMapper.ToGrpc(request),
                deadline: DateTime.UtcNow.Add(options.Value.ReasoningTimeout), cancellationToken: cancellationToken);
            await usage.RecordAsync(ReasoningEndpoints.EvaluateOperation, 1, cancellationToken);
            return ReasoningMapper.ToDto(response);
        });

    // 內部服務容量用盡：法規查詢視為暫時不可用；推論有自己的代碼（呼叫端稍後重試）。
    private const string OntologyBusy = OutcomeCodes.OntologyUnavailable;

    private static LawIdentifier IdentifierOf(string? jurisdiction, string? idScheme, string? localId) =>
        new() { Jurisdiction = jurisdiction ?? string.Empty, IdScheme = idScheme ?? string.Empty, LocalId = localId ?? string.Empty };

    private static async Task<AdminOutcome<T>> CallAsync<T>(string exhaustedCode, Func<Task<T>> call)
    {
        try
        {
            return AdminOutcome.Ok(await call());
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.NotFound)
        {
            return AdminOutcome.NotFound<T>(OutcomeCodes.LawNotFound);
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
        {
            return AdminOutcome.Invalid<T>(OutcomeCodes.UpstreamInvalidRequest, ex.Status.Detail);
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.ResourceExhausted)
        {
            // 容量用盡：立即失敗而不是排隊，呼叫端稍後重試（唯讀、確定性，重試安全）。
            return AdminOutcome.Unavailable<T>(exhaustedCode);
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
        {
            return AdminOutcome.Unavailable<T>(OutcomeCodes.OntologyUnavailable);
        }
    }
}

internal sealed record LawSummaryDto(
    LawIdentifierDto Identifier,
    string Uri,
    string Title,
    string NormKind,
    string? DateDocument,
    LawIdentifierDto? ParentIdentifier);