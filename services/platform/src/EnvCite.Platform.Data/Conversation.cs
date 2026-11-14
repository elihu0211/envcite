namespace EnvCite.Platform.Data;

/// <summary>
/// 一位成員的一串問答。租戶擁有（<c>TenantId</c> 是 Finbuckle 的 shadow property），而且只有建立它的成員看得到：
/// 同一租戶的其他成員，連 Owner 都不行（問題文字是客戶案件的內容）。
/// </summary>
public sealed class Conversation
{
    public const int TitleLength = 80;

    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string UserId { get; init; }

    /// <summary>預設是第一個問題的前 <see cref="TitleLength"/> 個字；成員可以改名。</summary>
    public required string Title { get; set; }

    /// <summary>釘選的時間；null 是沒釘選。釘選只影響側欄的排序，不影響保存期限（<see cref="UpdatedAt"/> 才是）。</summary>
    public DateTimeOffset? PinnedAt { get; set; }

    /// <summary>
    /// 封存的時間；null 是沒封存。封存的對話不在側欄與預設列表，只在「已封存」；它只管能不能在列表看見，
    /// 不影響保存期限（<see cref="UpdatedAt"/> 才是）。封存時一併取消釘選；之後在這串對話接著問，就自動取消封存。
    /// </summary>
    public DateTimeOffset? ArchivedAt { get; set; }

    public DateTimeOffset CreatedAt { get; init; }

    public DateTimeOffset UpdatedAt { get; set; }

    public List<ConversationMessage> Messages { get; init; } = [];
}

public sealed class ConversationMessage
{
    public const string UserRole = "user";

    public const string AssistantRole = "assistant";

    public Guid Id { get; init; } = Guid.CreateVersion7();

    public Guid ConversationId { get; init; }

    public required string Role { get; init; }

    public required string Content { get; init; }

    public DateTimeOffset CreatedAt { get; init; }

    /// <summary>只有問題有：問的哪一天的條文（ISO 日期）；null 是現行。</summary>
    public string? AsOf { get; init; }

    /// <summary>只有回答有：<c>answered</c>、<c>refused</c>、<c>listed</c>；舊資料是 null。接續對話時拒答不當作前文。</summary>
    public string? Outcome { get; init; }

    /// <summary>
    /// 只有回答有：引用的法源（代號、法規名、網址）。不存條文本文：全文隨時可以由網址再查。
    /// 以 jsonb 存在同一列；<see cref="Content"/> 裡的 <c>[L1]</c> 靠它換回法規名。
    /// </summary>
    public List<StoredCitation> Citations { get; init; } = [];
}

public sealed class StoredCitation
{
    public required string Code { get; init; }

    public required string Label { get; init; }

    public required string Uri { get; init; }
}