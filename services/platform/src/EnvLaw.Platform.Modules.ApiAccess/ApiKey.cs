namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>
/// A tenant-owned API key. Only the SHA-256 hash of the secret is stored; the full key is shown once at creation.
/// <c>TenantId</c> is a Finbuckle shadow property (see <see cref="ApiAccessDbContext"/>).
/// </summary>
public sealed class ApiKey
{
    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string Name { get; init; }

    /// <summary>Public lookup handle embedded in the key (<c>sk_live_{Prefix}_{secret}</c>).</summary>
    public required string Prefix { get; init; }

    public required byte[] SecretHash { get; init; }

    public string[] Scopes { get; init; } = [];

    public DateTimeOffset CreatedAt { get; init; }

    public DateTimeOffset? ExpiresAt { get; init; }

    public DateTimeOffset? RevokedAt { get; set; }

    public bool IsActive(DateTimeOffset now) => RevokedAt is null && (ExpiresAt is null || ExpiresAt > now);
}