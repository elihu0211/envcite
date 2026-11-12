namespace EnvCite.Platform.Data;

/// <summary>
/// 身分來源推送的群組。<see cref="Role"/> 是客戶的擁有者設定的對映：有對映的群組，其成員的角色依它決定；沒對映的群組只是記錄，
/// 不影響任何人。群組只有「受目錄同步管理」的成員（見 <see cref="DirectoryGroupMember"/>）。<c>TenantId</c> 是 Finbuckle 的 shadow property。
/// </summary>
public sealed class DirectoryGroup
{
    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string DisplayName { get; set; }

    /// <summary>名稱的比對鍵（大寫、去前後空白）：同一租戶內唯一，身分來源改名時跟著改。</summary>
    public required string NormalizedName { get; set; }

    public string? ExternalId { get; set; }

    /// <summary>對映的角色（<c>DirectoryRoles.Assignable</c> 之一）；沒對映為 null。</summary>
    public string? Role { get; set; }

    public DateTimeOffset UpdatedAt { get; set; }

    public List<DirectoryGroupMember> Members { get; } = [];

    public static string Normalize(string displayName) => displayName.Trim().ToUpperInvariant();
}

/// <summary>群組的一位成員（<c>ApplicationUser.Id</c>）。<c>TenantId</c> 是 Finbuckle 的 shadow property。</summary>
public sealed class DirectoryGroupMember
{
    public Guid GroupId { get; init; }

    public required string UserId { get; init; }
}