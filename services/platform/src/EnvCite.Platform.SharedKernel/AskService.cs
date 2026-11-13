using System.Security.Claims;

using Microsoft.Extensions.AI;

namespace EnvCite.Platform.SharedKernel;

/// <summary>入口的問答給出什麼：只列條文（<see cref="Search"/>），或有出處的回答（<see cref="Answer"/>）。</summary>
public enum AskMode
{
    Search,
    Answer,
}

public enum AskStatus
{
    Ok,

    /// <summary>租戶沒有方案（完全付費制），或身分不是入口成員。不呼叫任何服務、不計量。</summary>
    NoPlan,

    /// <summary>輸入錯誤：問題是空的、日期不是有效的日期。</summary>
    Invalid,

    /// <summary>consult、模型或本體暫時不可用。</summary>
    Unavailable,
}

/// <summary>
/// 這位成員在自己的租戶能用什麼。<see cref="AiAnswer"/> 要三個條件都成立：租戶有方案、租戶同意（問題會送到外部的模型服務）、
/// 部署者設定了模型端點。
/// </summary>
public sealed record AskCapabilities(bool HasPlan, bool AiAnswer)
{
    public AskMode Mode => AiAnswer ? AskMode.Answer : AskMode.Search;
}

/// <summary>
/// 一次提問的結果。<see cref="Mode"/> 是這次實際走的路；<see cref="Status"/> 為 <see cref="AskStatus.Ok"/> 時，
/// <see cref="Answer"/>（回答模式）或 <see cref="Hits"/>（搜尋模式）有值。拒答是成功的結果，在 <see cref="GroundedAnswer.Refused"/>。
/// </summary>
public sealed record AskResult(AskMode Mode, AskStatus Status, GroundedAnswer? Answer = null, IReadOnlyList<LawHit>? Hits = null);

/// <summary>
/// 入口問答的應用服務：方案與同意的檢查、回答或搜尋、問答歷史，在**呼叫者的租戶**裡執行。
/// 租戶只取自已驗證的入口成員身分（<see cref="PlatformClaimTypes.TenantIdentifier"/>），每個操作自己開租戶 scope，
/// 所以靜態頁與互動式 circuit（沒有 ambient tenant）用同一份，頁面只管呈現。
/// 不是入口成員的身分一律當作沒有方案（fail closed）。問題文字不進日誌與指標。
/// </summary>
public interface IAskService
{
    Task<AskCapabilities> CapabilitiesAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <param name="question">呼叫端已去頭尾空白的問題；空白視為 <see cref="AskStatus.Invalid"/>。</param>
    /// <param name="asOf">ISO 8601 日期（<c>YYYY-MM-DD</c>）；省略 = 現行。</param>
    /// <param name="history">
    /// 這串對話前面的幾輪（User 是問題、Assistant 是答案，由舊到新），第一次提問給空的。只有回答模式用它：
    /// 追問先被改寫成獨立的問句再取證據，前文是理解問題用的，不是證據。搜尋模式忽略。長度由服務端裁。
    /// </param>
    /// <param name="progress">回答模式每進到下一步回報一次（搜尋模式沒有步驟）；可為 null。</param>
    Task<AskResult> AskAsync(
        ClaimsPrincipal user, string? question, string? asOf, IReadOnlyList<ChatMessage> history, IProgress<AnswerProgress>? progress, CancellationToken cancellationToken);

    /// <summary>
    /// 這位成員的對話：釘選的在前，其後是最近更新的（新的在前，最多 <paramref name="limit"/> 筆）。
    /// 歷史沒開啟、或身分不是入口成員時是空的。側欄用：不讀對話內容，只有標題與時間。
    /// </summary>
    Task<IReadOnlyList<ConversationSummary>> RecentAsync(ClaimsPrincipal user, int limit, CancellationToken cancellationToken);

    /// <summary>
    /// 開啟這位成員自己的一串對話（含每一則問與答）。不是自己的、不存在的、已過期的、歷史沒開啟的、身分不是入口成員的，
    /// 一律回 null——不洩漏它存不存在。
    /// </summary>
    Task<ConversationDetail?> ConversationAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken);

    /// <summary>
    /// 把一問一答記進這位成員的歷史（只有本人看得到）。歷史沒開啟、或身分不是入口成員時回 null；
    /// 規則見 <see cref="IConversationHistory"/>。
    /// </summary>
    Task<Guid?> RecordAsync(ClaimsPrincipal user, Guid? conversationId, ConversationTurn turn, CancellationToken cancellationToken);
}