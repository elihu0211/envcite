namespace EnvLaw.Platform.SharedKernel;

/// <summary>Authentication scheme and authorization policy names shared across modules.</summary>
public static class PlatformAuth
{
    /// <summary>Tenant API keys: <c>Authorization: Bearer sk_live_…</c>.</summary>
    public const string ApiKeyScheme = "ApiKey";

    /// <summary>Platform operator key: <c>X-Admin-Key</c> header.</summary>
    public const string AdminKeyScheme = "AdminKey";

    /// <summary>Requires an authenticated tenant API key.</summary>
    public const string TenantPolicy = "Tenant";

    /// <summary>Requires the platform admin key.</summary>
    public const string AdminPolicy = "Admin";

    /// <summary>Rate-limiting policy partitioned by tenant.</summary>
    public const string PerTenantRateLimit = "per-tenant";
}