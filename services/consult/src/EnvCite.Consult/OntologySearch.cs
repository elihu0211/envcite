using EnvCite.Ontology.V2;
using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Options;

namespace EnvCite.Consult;

/// <summary>
/// 一筆檢索到的條文。分數只在同一次檢索之內可比較。<paramref name="GuidanceDocuments"/> 是在查詢日有效、
/// 提到查詢詞並解釋這一條的函釋件數，不是分數：沒有函釋指向它時為 0。
/// </summary>
internal sealed record Article(
    string Iri,
    string Label,
    string Text,
    double LexicalScore,
    double? SemanticSimilarity = null,
    LawIdentifier? LawIdentifier = null,
    int GuidanceDocuments = 0);

/// <summary>
/// 管線所依賴的 port：法規的讀取途徑，本服務中法律事實的唯一來源。它是介面而非 client——管線的形狀
/// （哪個階段可以決定什麼）正是本服務存在所要固定下來的東西，必須在執行中的本體服務存在之前就能測試；
/// 測試中的 fake 是另一種實作，並不次一等。
/// </summary>
internal interface IOntologySearch
{
    Task<IReadOnlyList<Article>> SearchAsync(string query, string asOf, CancellationToken cancellationToken);
}

/// <summary>envcite-ontology 的連線設定（<c>Ontology</c> 區段；叢集內為 <c>dns:///envcite-ontology:50051</c>）。</summary>
internal sealed class OntologyOptions : InternalGrpcClientOptions, ISectionOptions
{
    public static string SectionName => "Ontology";
}

/// <summary>
/// 以 envcite-ontology 的 <c>SearchArticles</c> 實作的 <see cref="IOntologySearch"/>。期限是自己的上限
/// （<see cref="InternalGrpcClientOptions.Timeout"/>）與呼叫端剩下的期限取小者：後者由用戶端工廠的
/// call context propagation 帶上（見 Program.cs），上游早已逾時，下游就不白做工。
/// </summary>
internal sealed class OntologySearch(OntologyService.OntologyServiceClient client, IOptions<OntologyOptions> options) : IOntologySearch
{
    public const int Limit = 10;

    /// <summary>
    /// 對本體的 <c>SearchArticles</c> 要什麼，只在這裡決定：檢索選項都加在這一個建構點（例如之後的精排 <c>Rerank</c>），
    /// 不在呼叫的地方零散加旗標，所以一次請求帶了哪些選項，看這個方法就知道，測試也只需要守它。
    /// </summary>
    public static SearchArticlesRequest BuildRequest(string query, string asOf) => new()
    {
        Query = query,
        AsOf = asOf,
        Limit = Limit,
        // 使用者查的詞常是函釋的用語（函釋寫「露天燃燒」、條文寫「燃燒」），兩臂都找不到；函釋只用來找條文，不進證據。
        IncludeGuidance = true,
    };

    public async Task<IReadOnlyList<Article>> SearchAsync(string query, string asOf, CancellationToken cancellationToken)
    {
        var response = await client.SearchArticlesAsync(
            BuildRequest(query, asOf),
            deadline: DateTime.UtcNow.Add(options.Value.Timeout),
            cancellationToken: cancellationToken);
        return [.. response.Hits.Select(hit => new Article(
            hit.Iri,
            $"{hit.LawTitle} {ArticleLabel(hit.Number)}",
            hit.Content,
            // 相關性下界以字面分數或語意相似度校定；分數的定義一旦改變，下界就必須重新推導。
            hit.LexicalScore,
            hit.HasSemanticSimilarity ? hit.SemanticSimilarity : null,
            hit.Identifier,
            hit.GuidanceDocuments))];
    }

    /// <summary>「20」→ 第 20 條；「5-1」→ 第 5 條之 1。</summary>
    public static string ArticleLabel(string number) =>
        number.Split('-', 2) is [var main, var sub] ? $"第 {main} 條之 {sub}" : $"第 {number} 條";
}