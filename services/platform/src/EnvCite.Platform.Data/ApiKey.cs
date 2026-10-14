namespace EnvCite.Platform.Data;

/// <summary>
/// 租戶擁有的 API key。只儲存 secret 的 SHA-256 雜湊；完整 key 僅在建立時顯示一次。
/// <c>TenantId</c> 是 Finbuckle 的 shadow property（見 <see cref="PlatformDbContext"/>）。
/// </summary>
public sealed class ApiKey
{
    /// <summary><see cref="Prefix"/> 的長度（hex 字元數）；欄位寬度與 key 格式共用此值。</summary>
    public const int PrefixLength = 12;

    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string Name { get; init; }

    /// <summary>嵌在 key 中、可公開的查找 handle（<c>sk_live_{Prefix}_{secret}</c>）。</summary>
    public required string Prefix { get; init; }

    public required byte[] SecretHash { get; init; }

    public string[] Scopes { get; init; } = [];

    public DateTimeOffset CreatedAt { get; init; }

    public DateTimeOffset? ExpiresAt { get; init; }

    public DateTimeOffset? RevokedAt { get; set; }

    public bool IsActive(DateTimeOffset now) => RevokedAt is null && (ExpiresAt is null || ExpiresAt > now);
}