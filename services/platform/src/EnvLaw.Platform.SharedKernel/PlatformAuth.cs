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

    /// <summary>營運者後台的登入 cookie。</summary>
    public const string UiCookieScheme = "UiCookie";

    /// <summary>營運者後台的 Google 單點登入（只負責證明身分，授權由白名單決定）。</summary>
    public const string UiGoogleScheme = "UiGoogle";

    /// <summary>需要已登入、且在營運者白名單內的後台使用者。</summary>
    public const string UiPolicy = "UiOperator";

    /// <summary>依租戶分區的 rate limiting policy。</summary>
    public const string PerTenantRateLimit = "per-tenant";
}