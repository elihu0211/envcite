using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

public sealed record ConversationSummary(Guid Id, string Title, DateTimeOffset UpdatedAt);

/// <param name="Role"><c>user</c> 或 <c>assistant</c>。</param>
public sealed record ConversationMessageView(string Role, string Content, DateTimeOffset CreatedAt);

public sealed record ConversationDetail(Guid Id, string Title, DateTimeOffset UpdatedAt, IReadOnlyList<ConversationMessageView> Messages);

/// <summary>
/// 客戶入口成員的問答歷史。<b>只有本人看得到</b>：每個方法都以呼叫者的入口使用者 Id 篩選（同租戶的其他成員，
/// 連 Owner 都不行），租戶則由租戶篩選保證。問題文字是客戶案件的內容，所以預設關閉
/// （<c>Platform:ConversationHistory:Enabled</c>），開啟後超過保留天數的對話由背景工作刪除。
/// 關閉時所有方法都不讀不寫。
/// </summary>
public interface IConversationHistory
{
    bool Enabled { get; }

    /// <summary>保留天數；超過的對話（以最後更新計）會被刪除。</summary>
    int RetentionDays { get; }

    /// <summary>
    /// 記一問一答。<paramref name="conversationId"/> 給了而且是呼叫者自己的對話就接在後面，否則另開一串
    /// （別人的、不存在的都當作另開，不洩漏它存不存在）。關閉時回 null。
    /// </summary>
    Task<Guid?> AppendTurnAsync(ClaimsPrincipal user, Guid? conversationId, string question, string answer, CancellationToken cancellationToken);

    /// <summary>最近更新的在前。</summary>
    Task<IReadOnlyList<ConversationSummary>> ListAsync(ClaimsPrincipal user, int limit, CancellationToken cancellationToken);

    /// <summary>不是呼叫者自己的對話（或不存在）一律 null。</summary>
    Task<ConversationDetail?> GetAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken);

    /// <summary>刪除呼叫者自己的一串對話（連同訊息），並記稽核；回傳有沒有刪到。稽核只記 Id，不記內容。</summary>
    Task<bool> DeleteAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken);
}