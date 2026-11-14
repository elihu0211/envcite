using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <param name="Pinned">釘選的對話在側欄排最上面，不受「最近」的筆數限制。</param>
public sealed record ConversationSummary(Guid Id, string Title, DateTimeOffset UpdatedAt, bool Pinned = false);

/// <summary>
/// 答案引用的一筆法源。代號（<c>L1</c>…）只在那一輪的證據集裡有意義，所以連同法規名與條文網址一起存：
/// 日後接續這串對話時，前文裡的代號能換回法規名，而不必存條文本文。
/// </summary>
public sealed record ConversationCitation(string Code, string Label, string Uri);

/// <summary>回合的結果種類（存進歷史的值）。</summary>
public static class ConversationOutcome
{
    /// <summary>有出處的回答。</summary>
    public const string Answered = "answered";

    /// <summary>拒答：<see cref="ConversationTurn.Answer"/> 是給使用者看的說明，不是答案，接續對話時不當作前文。</summary>
    public const string Refused = "refused";

    /// <summary>只列條文（沒有 AI 回答）。</summary>
    public const string Listed = "listed";
}

/// <summary>要記進歷史的一問一答。<paramref name="AsOf"/> 是問的哪一天的條文（ISO 日期；null 是現行）。</summary>
public sealed record ConversationTurn(
    string Question, string Answer, string Outcome, IReadOnlyList<ConversationCitation> Citations, string? AsOf = null);

/// <param name="Role"><c>user</c> 或 <c>assistant</c>。</param>
/// <param name="Citations">只有回答的訊息有；舊資料（引用行寫在文字裡）是空的。</param>
/// <param name="AsOf">只有問題有：問的哪一天的條文；null 是現行。</param>
/// <param name="Outcome"><see cref="ConversationOutcome"/>；只有回答的訊息有，舊資料是 null。</param>
public sealed record ConversationMessageView(
    string Role,
    string Content,
    DateTimeOffset CreatedAt,
    IReadOnlyList<ConversationCitation>? Citations = null,
    string? AsOf = null,
    string? Outcome = null);

public sealed record ConversationDetail(
    Guid Id, string Title, DateTimeOffset UpdatedAt, IReadOnlyList<ConversationMessageView> Messages, bool Pinned = false);

/// <summary>
/// 問答歷史有沒有開啟（<c>Platform:ConversationHistory:Enabled</c>）與保留幾天。singleton，只讀設定；
/// 與 <see cref="IConversationHistory"/> 分開，同 <see cref="IAnswerAvailability"/> 的理由：後者是 scoped（要資料庫與租戶），
/// 為了一個旗標去解析它，互動式 circuit 會把那個 <c>DbContext</c> 抓住直到斷線。
/// </summary>
public interface IConversationHistoryAvailability
{
    bool Enabled { get; }

    /// <summary>保留天數；超過的對話（以最後更新計）會被刪除。釘選的也一樣：釘選只管排序，不延長保存。</summary>
    int RetentionDays { get; }
}

/// <summary>
/// 客戶入口成員的問答歷史。<b>只有本人看得到</b>：每個方法都以呼叫者的入口使用者 Id 篩選（同租戶的其他成員，
/// 連 Owner 都不行），租戶則由租戶篩選保證。問題文字是客戶案件的內容，所以預設關閉
/// （見 <see cref="IConversationHistoryAvailability"/>），開啟後超過保留天數的對話由背景工作刪除。
/// 關閉時所有方法都不讀不寫。
/// </summary>
public interface IConversationHistory
{
    /// <summary>
    /// 記一問一答。<paramref name="conversationId"/> 給了而且是呼叫者自己的對話就接在後面，否則另開一串
    /// （別人的、不存在的都當作另開，不洩漏它存不存在）。關閉時回 null。
    /// </summary>
    Task<Guid?> AppendTurnAsync(ClaimsPrincipal user, Guid? conversationId, ConversationTurn turn, CancellationToken cancellationToken);

    /// <summary>
    /// 釘選的在前（釘得晚的在前，最多 <see cref="MaxPinned"/> 筆），其後是最近更新的在前（最多 <paramref name="limit"/> 筆）。
    /// 已封存的不列（見 <see cref="ListArchivedAsync"/>）。
    /// <paramref name="search"/> 只比對標題（不分大小寫、不是萬用字元），不比對內容：內容是客戶案情，不建索引。
    /// </summary>
    Task<IReadOnlyList<ConversationSummary>> ListAsync(ClaimsPrincipal user, int limit, string? search, CancellationToken cancellationToken);

    /// <summary>已封存的對話，封存得晚的在前（最多 <paramref name="limit"/> 筆）；<paramref name="search"/> 同 <see cref="ListAsync"/>。</summary>
    Task<IReadOnlyList<ConversationSummary>> ListArchivedAsync(ClaimsPrincipal user, int limit, string? search, CancellationToken cancellationToken);

    /// <summary>不是呼叫者自己的對話（或不存在）一律 null。</summary>
    Task<ConversationDetail?> GetAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken);

    /// <summary>
    /// 改標題。標題去頭尾空白、控制字元換成空白、截到 <c>Conversation.TitleLength</c> 個字；空白的標題不接受（回 false）。
    /// 不動 <c>UpdatedAt</c>：改名不是新的活動，不該改變排序或延長保存。回傳有沒有改到（別人的、不存在的都是 false）。
    /// </summary>
    Task<bool> RenameAsync(ClaimsPrincipal user, Guid id, string title, CancellationToken cancellationToken);

    /// <summary>釘選或取消釘選自己的對話；同樣不動 <c>UpdatedAt</c>。回傳有沒有改到。</summary>
    Task<bool> PinAsync(ClaimsPrincipal user, Guid id, bool pinned, CancellationToken cancellationToken);

    /// <summary>
    /// 封存或取消封存自己的對話。封存的從側欄與預設列表消失、只在已封存的列表；封存時一併取消釘選。
    /// 同樣不動 <c>UpdatedAt</c>：封存不是新的活動，不延長保存。回傳有沒有改到。
    /// 在封存的對話接著問（<see cref="AppendTurnAsync"/>）會自動取消封存。
    /// </summary>
    Task<bool> ArchiveAsync(ClaimsPrincipal user, Guid id, bool archived, CancellationToken cancellationToken);

    /// <summary>
    /// 把自己的一串對話整串複製成新的一串（分叉）：訊息、引用與問的日期原樣複製，之後兩串各自發展。
    /// 新的一串以 <paramref name="title"/>（同改名的正規化）為標題、不釘選、不封存，<c>UpdatedAt</c> 是現在：
    /// 這是新的活動，保存期限從現在起算。回傳新對話的 Id；不是自己的、不存在、標題空白、關閉時都是 null。
    /// </summary>
    Task<Guid?> ForkAsync(ClaimsPrincipal user, Guid id, string title, CancellationToken cancellationToken);

    /// <summary>刪除呼叫者自己的一串對話（連同訊息），並記稽核；回傳有沒有刪到。稽核只記 Id，不記內容。</summary>
    Task<bool> DeleteAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken);

    /// <summary>側欄與列表最多列幾筆釘選。</summary>
    const int MaxPinned = 50;
}