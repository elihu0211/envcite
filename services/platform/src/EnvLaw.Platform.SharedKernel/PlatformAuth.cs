namespace EnvLaw.Platform.SharedKernel;

/// <summary>各模組共用的 authentication scheme 與 authorization policy 名稱。</summary>
public static class PlatformAuth
{
    /// <summary>租戶 API key：<c>Authorization: Bearer sk_live_…</c>。</summary>
    public const string ApiKeyScheme = "ApiKey";

    /// <summary>平台營運者 key：<c>X-Admin-Key</c> header。</summary>
    public const string AdminKeyScheme = "AdminKey";

    /// <summary>需要已驗證的租戶 API key。</summary>
    public const string TenantPolicy = "Tenant";

    /// <summary>需要平台 admin key。</summary>
    public const string AdminPolicy = "Admin";

    /// <summary>依租戶分區的 rate limiting policy。</summary>
    public const string PerTenantRateLimit = "per-tenant";
}