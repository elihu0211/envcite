using System.ComponentModel;
using System.Text.Json;

using ModelContextProtocol;
using ModelContextProtocol.Server;

namespace EnvCite.Platform.Modules.Ontology.Mcp;

/// <param name="Articles">相關度高的在前；查不到相關條文時是空陣列，不用不相干的條文湊數。</param>
public sealed record SearchArticlesResult(IReadOnlyList<SearchHitDto> Articles);

/// <param name="Articles">依條次排序；<c>Number</c> 照來源寫法（如 <c>5-1</c>）。</param>
public sealed record ListArticlesResult(IReadOnlyList<ArticleDto> Articles);

/// <summary>
/// 對外 REST 的資料端點，以 MCP 工具提供給 AI 代理。工具與 REST 共用同一份實作（<see cref="ILawSearch"/>、<see cref="OntologyGateway"/>），
/// 所以輸入檢查、錯誤、計量（成功才記）與「查詢文字不進任何日誌」只有一個；身分、方案與限流由 <c>/v2</c> 群組在進來之前就擋下。
/// 全部唯讀、確定性。預期的失敗（輸入錯誤、找不到、暫時不可用）以 <see cref="McpException"/> 回成工具錯誤，訊息是英文的穩定說明，不含內部細節。
/// </summary>
[McpServerToolType]
internal sealed class OntologyMcpTools(ILawSearch search, OntologyGateway gateway)
{
    private const string PointInTime = "ISO 8601 日期（YYYY-MM-DD）：依哪一天施行的條文；省略表示現行。";

    [McpServerTool(Name = "search_articles", Title = "搜尋條文", ReadOnly = true, Idempotent = true, Destructive = false, OpenWorld = false, UseStructuredContent = true)]
    [Description("以字面檢索查詢某日有效的條文，相關度高的在前；只回達到相關性下界的條文，查不到時回空陣列。"
        + "函釋提到查詢詞時，它所解釋的條文也會列出（guidanceDocuments 為提到的函釋件數），函釋只用來找到條文，不是依據，引用以條文為準。"
        + "每一筆有 uri（可反查到該條文版本）與條文全文。查詢文字是客戶案件的內容，不會被記錄。")]
    public async Task<SearchArticlesResult> SearchArticles(
        [Description("要查的原始文字，用條文裡會出現的詞，空白分隔（服務端自行分詞）。")] string query,
        [Description(PointInTime)] string? pointInTime = null,
        [Description("最多幾筆，1–50；省略為 10。")] int? limit = null,
        CancellationToken cancellationToken = default)
    {
        var hits = Succeeded(await search.SearchAsync(query, pointInTime, limit, cancellationToken));
        return new SearchArticlesResult([.. hits.Select(h => new SearchHitDto(h.Uri, h.Identifier, h.LawTitle, h.Number, h.Content, h.Score, h.GuidanceDocuments))]);
    }

    [McpServerTool(Name = "get_law", Title = "取得法規", ReadOnly = true, Idempotent = true, Destructive = false, OpenWorld = false, UseStructuredContent = true)]
    [Description("取得一部法規的標題與基本資料。法規以複合自然鍵指定，三段都必填（中央法規如 TW-CENTRAL／moj_pcode／O0020001）。"
        + "標題取自指定日有效的版本；該日沒有有效的版本就是找不到，不會退回現行版本。")]
    public async Task<LawDto> GetLaw(
        [Description("管轄，中央法規是 TW-CENTRAL。")] string jurisdiction,
        [Description("識別碼方案，全國法規資料庫是 moj_pcode。")] string idScheme,
        [Description("在地識別碼，即 pcode，如 O0020001。")] string localId,
        [Description(PointInTime)] string? pointInTime = null,
        CancellationToken cancellationToken = default) =>
        Succeeded(await gateway.GetLawAsync(jurisdiction, idScheme, localId, pointInTime, cancellationToken));

    [McpServerTool(Name = "list_articles", Title = "列出法規各條", ReadOnly = true, Idempotent = true, Destructive = false, OpenWorld = false, UseStructuredContent = true)]
    [Description("列出一部法規在某日有效的各條（條文網址與條號），依條次排序。法規的指定方式同 get_law。該日沒有有效的版本就是找不到，不會退回現行版本。")]
    public async Task<ListArticlesResult> ListArticles(
        [Description("管轄，中央法規是 TW-CENTRAL。")] string jurisdiction,
        [Description("識別碼方案，全國法規資料庫是 moj_pcode。")] string idScheme,
        [Description("在地識別碼，即 pcode，如 O0020001。")] string localId,
        [Description(PointInTime)] string? pointInTime = null,
        CancellationToken cancellationToken = default) =>
        new(Succeeded(await gateway.ListArticlesAsync(jurisdiction, idScheme, localId, pointInTime, cancellationToken)));

    [McpServerTool(Name = "evaluate_reasoning", Title = "對案件事實推論", ReadOnly = true, Idempotent = true, Destructive = false, OpenWorld = false, UseStructuredContent = true)]
    [Description("依行為日與裁處日當天有效、已通過驗證的規範陳述，對案件事實推論，回傳候選結論與依據；系統不裁量、不判決。"
        + "沒有通過驗證的陳述就沒有結論：verdicts 為空不表示合規。uncoveredLawIds 列出語料沒有當時版本的法規，其規定缺席不代表當時沒有規定。"
        + "administrativePenalty 省略的欄位是「未知」，不是「否」。atDecision 是裁處時之法律（行政罰法第 5 條的預設），"
        + "earlier 是行為日到裁處日之間陳述組與裁處時不同的法律，「最有利於受處罰者」由呼叫端依第 5 條但書選擇。案件事實是客戶案件的內容，不會被記錄。")]
    public async Task<ReasoningResultDto> EvaluateReasoning(
        [Description("案件事實與行為日、裁處日；結構與 REST 的 POST /v2/reasoning/evaluate 相同。")] EvaluateReasoningRequest request,
        CancellationToken cancellationToken = default) =>
        Succeeded(await gateway.EvaluateAsync(request, cancellationToken));

    /// <summary>預期的失敗轉成工具錯誤（訊息是英文的穩定說明）；McpException 以外的例外會被框架換成通用訊息，不洩漏內部細節。</summary>
    private static T Succeeded<T>(AdminOutcome<T> outcome) =>
        outcome.IsOk ? outcome.Value! : throw new McpException(outcome.Message ?? "Request failed.");
}

/// <summary>
/// 一部法規現行的樣子，當成資源讓用戶端附進對話（不必讓模型先決定要呼叫哪個工具）：標題、基本資料與現行各條的清單。
/// 讀一次等於 get_law 加 list_articles，各計量一次。
/// </summary>
[McpServerResourceType]
internal sealed class OntologyMcpResources(OntologyGateway gateway)
{
    [McpServerResource(UriTemplate = "envcite://laws/{jurisdiction}/{idScheme}/{localId}", Name = "law", Title = "法規與現行各條", MimeType = "application/json")]
    [Description("一部法規現行的標題、基本資料與各條清單（條文網址與條號）。法規以管轄／識別碼方案／在地識別碼指定，如 envcite://laws/TW-CENTRAL/moj_pcode/O0020001。")]
    public async Task<string> Law(string jurisdiction, string idScheme, string localId, CancellationToken cancellationToken)
    {
        var law = await gateway.GetLawAsync(jurisdiction, idScheme, localId, null, cancellationToken);
        if (!law.IsOk)
            throw new McpException(law.Message ?? "Request failed.");

        var articles = await gateway.ListArticlesAsync(jurisdiction, idScheme, localId, null, cancellationToken);
        if (!articles.IsOk)
            throw new McpException(articles.Message ?? "Request failed.");

        return JsonSerializer.Serialize(new { law = law.Value, articles = articles.Value }, JsonSerializerOptions.Web);
    }
}

/// <summary>把法規資料的工具與資源加進 MCP 伺服器（由主機組合）。</summary>
public static class OntologyMcp
{
    public static IMcpServerBuilder WithOntologyTools(this IMcpServerBuilder builder) =>
        builder.WithTools<OntologyMcpTools>().WithResources<OntologyMcpResources>();
}