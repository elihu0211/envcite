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

    public required string Title { get; init; }

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
}