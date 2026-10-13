namespace EnvLaw.Platform.SharedKernel;

/// <param name="Uri">由法規的 pcode 與條號組成，可反查到該條。</param>
/// <param name="LawId">所屬法規的 pcode。</param>
/// <param name="LawTitle">查詢日有效版本的法規標題。</param>
/// <param name="Number">條號，照來源寫法（如 <c>5-1</c>）。</param>
/// <param name="Score">字面相關度；只在同一次回應內可比，跨查詢不可比。</param>
public sealed record LawHit(string Uri, string LawId, string LawTitle, string Number, string Content, float Score);

/// <summary>
/// 條文搜尋：某一天有效的條文，字面檢索。對外的 <c>POST /v2/search</c> 與客戶入口的搜尋頁共用這一份，
/// 所以驗證、錯誤、計量（<c>ontology.search</c>，成功才記）與「查詢文字不進任何日誌」只有一個實作。
/// 以 scoped 註冊（計量需要目前請求的租戶）。呼叫端負責確認租戶有方案（完全付費制）。
/// </summary>
public interface ILawSearch
{
    /// <param name="query">原始文字；空白視為輸入錯誤。</param>
    /// <param name="asOf">ISO 8601 日期（<c>YYYY-MM-DD</c>）；省略 = 現行。</param>
    /// <param name="limit">1–50；省略 = 服務預設（10）。</param>
    /// <returns>
    /// <see cref="AdminStatus.Invalid"/>：輸入錯誤（<c>Message</c> 說明原因）；
    /// <see cref="AdminStatus.Unavailable"/>：內部服務暫時不可用。
    /// </returns>
    Task<AdminOutcome<IReadOnlyList<LawHit>>> SearchAsync(string? query, string? asOf, int? limit, CancellationToken cancellationToken);
}