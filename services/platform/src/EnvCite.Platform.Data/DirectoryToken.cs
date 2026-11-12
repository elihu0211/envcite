namespace EnvCite.Platform.Data;

/// <summary>
/// 租戶的目錄同步 token：客戶的身分來源（IdP）以它呼叫 SCIM 端點。只儲存 secret 的 SHA-256；完整 token 僅在簽發時顯示一次。
/// 與 <see cref="ApiKey"/> 是兩種憑證（前綴不同，互不通用）。<c>TenantId</c> 是 Finbuckle 的 shadow property。
/// </summary>
public sealed class DirectoryToken
{
    /// <summary><see cref="Prefix"/> 的長度（hex 字元數）。</summary>
    public const int PrefixLength = 12;

    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string Name { get; init; }

    /// <summary>嵌在 token 中、可公開的查找 handle（<c>scim_{Prefix}_{secret}</c>）。</summary>
    public required string Prefix { get; init; }

    public required byte[] SecretHash { get; init; }

    public DateTimeOffset CreatedAt { get; init; }

    public DateTimeOffset? ExpiresAt { get; init; }

    public DateTimeOffset? RevokedAt { get; set; }

    public DateTimeOffset? LastUsedAt { get; set; }

    public bool IsActive(DateTimeOffset now) => RevokedAt is null && (ExpiresAt is null || ExpiresAt > now);
}