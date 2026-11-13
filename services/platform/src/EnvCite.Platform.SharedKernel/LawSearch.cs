namespace EnvCite.Platform.SharedKernel;

/// <param name="Jurisdiction">管轄代碼（'TW-CENTRAL' 或 22 縣市代碼）。</param>
/// <param name="IdScheme">方案代碼（'moj_pcode' 只配 'TW-CENTRAL'，'local_glrs' 只配縣市）。</param>
/// <param name="LocalId">在地識別碼，在（管轄，方案）之內唯一。</param>
public sealed record LawIdentifierDto(string Jurisdiction, string IdScheme, string LocalId);

/// <param name="Uri">由法規與條號組成，可反查到該條。</param>
/// <param name="Identifier">所屬法規的複合自然鍵識別碼。</param>
/// <param name="LawTitle">查詢日有效版本的法規標題。</param>
/// <param name="Number">條號，照來源寫法（如 <c>5-1</c>）。</param>
/// <param name="Score">字面相關度；只在同一次回應內可比，跨查詢不可比。</param>
/// <param name="GuidanceDocuments">提到查詢詞並解釋這一條的函釋件數；函釋只用來找到這一條，不是依據。</param>
public sealed record LawHit(
    string Uri, LawIdentifierDto Identifier, string LawTitle, string Number, string Content, float Score, int GuidanceDocuments = 0);

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