using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.Ontology;

/// <param name="Identifier">法規的複合自然鍵（管轄，識別碼方案，在地識別碼）。</param>
/// <param name="Uri">由法規組成，可反查到該法規。</param>
/// <param name="DateDocument">制定（首次公布）日。</param>
public sealed record LawDto(
    LawIdentifierDto Identifier,
    string Uri,
    string Title,
    string Language,
    string? DateDocument);

/// <param name="Uri">由法規與條號組成，可反查到該條的現行版本。</param>
/// <param name="Number">條號，照來源寫法（如 <c>5-1</c>）。</param>
public sealed record ArticleDto(string Uri, string Number);

public sealed record ArticleWithSortDto(string Uri, string Number, IReadOnlyList<int> SortKey);

public sealed record StructureHeadingDto(
    string Kind,
    string HeadingNo,
    string Title,
    IReadOnlyList<int> SortKey,
    int ParentIndex);

public sealed record LawStructureDto(
    IReadOnlyList<ArticleWithSortDto> Articles,
    IReadOnlyList<StructureHeadingDto> Headings);

/// <param name="Query">要查的原始文字；服務端以建索引時的同一個分詞器分詞。</param>
/// <param name="AsOf">查詢哪一天有效的條文（<c>YYYY-MM-DD</c>）；省略表示現行。</param>
/// <param name="Limit">最多幾筆，1–50；省略為 10。</param>
public sealed record SearchRequest(string? Query, string? AsOf, int? Limit);

/// <param name="Uri">由法規與條號組成，可反查到該條。</param>
/// <param name="Identifier">所屬法規的複合自然鍵識別碼。</param>
/// <param name="LawTitle">查詢日有效版本的法規標題。</param>
/// <param name="Number">條號，照來源寫法（如 <c>5-1</c>）。</param>
/// <param name="Score">字面相關度；只在同一次回應內可比，跨查詢不可比。</param>
/// <param name="GuidanceDocuments">提到查詢詞並解釋這一條的函釋件數（0 = 不是函釋帶出的）；函釋只用來找到這一條，不是依據。</param>
public sealed record SearchHitDto(
    string Uri, LawIdentifierDto Identifier, string LawTitle, string Number, string Content, float Score, int GuidanceDocuments);

public sealed record ArticleDetailDto(
    string Uri,
    LawIdentifierDto Identifier,
    string LawTitle,
    string Number,
    string Content,
    string Language,
    string? DateDocument,
    string NormKind,
    string DocumentType = "",
    string? RepealedOn = null);

public static class OntologyEndpoints
{
    public const string GetLawOperation = "ontology.get_law";
    public const string ListArticlesOperation = "ontology.list_articles";
    public const string GetArticleByIriOperation = "ontology.get_article_by_iri";
    public const string SearchOperation = "ontology.search";

    /// <summary>包在內部 gRPC 服務外的對外 REST facade；成功的呼叫會被計量。</summary>
    public static RouteGroupBuilder MapOntologyEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/laws/{jurisdiction}/{idScheme}/{localId}", async (string jurisdiction, string idScheme, string localId, string? asOf,
            OntologyGateway gateway, CancellationToken ct) =>
            Present(await gateway.GetLawAsync(jurisdiction, idScheme, localId, asOf, ct)))
            .RequireModule(ModuleKeys.LawCorpus)
            .WithName("GetLaw")
            .WithTags("法規")
            .WithSummary("取得法規")
            .WithDescription("法規以複合自然鍵 `jurisdiction`／`idScheme`／`localId` 指定，三段都必填（中央法規如 `TW-CENTRAL/moj_pcode/O0020001`）；"
                + "值不是合法的管轄或方案回 400，標題取自 `asOf` 當日有效的版本，省略 `asOf`（`YYYY-MM-DD`）表示現行。"
                + "該日沒有有效的版本回 404，不會退回現行版本。成功計量為一次 `ontology.get_law`。")
            .Produces<LawDto>()
            .ProducesValidationProblem()
            .Produces(StatusCodes.Status404NotFound)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        v1.MapGet("/laws/{jurisdiction}/{idScheme}/{localId}/articles", async (string jurisdiction, string idScheme, string localId, string? asOf,
            OntologyGateway gateway, CancellationToken ct) =>
            Present(await gateway.ListArticlesAsync(jurisdiction, idScheme, localId, asOf, ct)))
            .RequireModule(ModuleKeys.LawCorpus)
            .WithName("ListArticles")
            .WithTags("法規")
            .WithSummary("列出法規某日有效的各條")
            .WithDescription("法規的指定方式同「取得法規」。依條次排序；`number` 照來源寫法（如 `5-1`）。省略 `asOf`（`YYYY-MM-DD`）表示現行；該日沒有有效的版本回 404，不會退回現行版本。"
                + "成功計量為一次 `ontology.list_articles`。")
            .Produces<IEnumerable<ArticleDto>>()
            .ProducesValidationProblem()
            .Produces(StatusCodes.Status404NotFound)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        v1.MapGet("/articles/by-iri", async (string iri, OntologyGateway gateway, CancellationToken ct) =>
            Present(await gateway.GetArticleByIriAsync(iri, ct)))
            .WithName("GetArticleByIri")
            .WithTags("法規")
            .WithSummary("依條文索引（IRI）取得單一條文全文")
            .WithDescription("以引用出處中的條文版本標準 `iri` 查詢單一條文全文與中繼資料；`iri` 為空回 400，查無回 404。")
            .Produces<ArticleDetailDto>()
            .ProducesValidationProblem()
            .Produces(StatusCodes.Status404NotFound)
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        // POST 而不是 GET：查詢全文是客戶案件的內容，放在網址裡會被請求日誌與追蹤記下來。
        v1.MapPost("/search", async (SearchRequest request, ILawSearch search, CancellationToken ct) =>
            {
                var result = await search.SearchAsync(request.Query, request.AsOf, request.Limit, ct);
                return result.Status switch
                {
                    AdminStatus.Ok => Results.Ok(result.Value!.Select(h =>
                        new SearchHitDto(h.Uri, h.Identifier, h.LawTitle, h.Number, h.Content, h.Score, h.GuidanceDocuments))),
                    AdminStatus.Invalid => result.ValidationProblem(),
                    _ => result.Problem(StatusCodes.Status503ServiceUnavailable, "Ontology service unavailable."),
                };
            })
            .RequireModule(ModuleKeys.LawSearch)
            .WithName("SearchArticles")
            .WithTags("法規")
            .WithSummary("搜尋條文")
            .WithDescription(
                "以字面檢索查詢某日有效的條文，相關度高的在前；只回達到相關性下界的條文，查不到相關條文時回空陣列，不用不相干的條文湊數。函釋的主旨或內文提到查詢詞時，它所解釋的條文也會列出（`guidanceDocuments` 為提到的函釋件數），函釋只用來找到條文，不是依據。`query` 必填，為原始文字（服務端自行分詞）；"
                + "`asOf` 為 ISO 8601 日期（`YYYY-MM-DD`），省略表示現行；`limit` 為 1–50，省略為 10。"
                + "用 POST 是為了讓查詢文字不出現在網址裡；查詢文字不寫入日誌。成功計量為一次 `ontology.search`（0 筆命中也計一次）。")
            .Produces<IEnumerable<SearchHitDto>>()
            .ProducesValidationProblem()
            .ProducesProblem(StatusCodes.Status503ServiceUnavailable);

        return v1;
    }

    /// <summary>結果轉成 HTTP：找不到是 404（沒有本文），輸入錯誤是 400，內部服務暫時不可用是 503。</summary>
    internal static IResult Present<T>(AdminOutcome<T> outcome) => outcome.Status switch
    {
        AdminStatus.Ok => Results.Ok(outcome.Value),
        AdminStatus.NotFound => Results.NotFound(),
        AdminStatus.Invalid => outcome.ValidationProblem(),
        _ => outcome.Problem(StatusCodes.Status503ServiceUnavailable, "Ontology service unavailable."),
    };
}