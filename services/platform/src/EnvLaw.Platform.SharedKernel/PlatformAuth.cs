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

    /// <summary>資料端點（本體、接地）：除了 <see cref="TenantPolicy"/>，租戶還要有方案；沒有方案回 402。</summary>
    public const string PaidPolicy = "Paid";

    /// <summary>需要平台 admin key。</summary>
    public const string AdminPolicy = "Admin";

    /// <summary>營運者後台的登入 cookie。</summary>
    public const string UiCookieScheme = "UiCookie";

    /// <summary>營運者後台的 Google 單點登入（只負責證明身分，授權由白名單決定）。</summary>
    public const string UiGoogleScheme = "UiGoogle";

    /// <summary>需要已登入、且在營運者白名單內的後台使用者。</summary>
    public const string UiPolicy = "UiOperator";

    /// <summary>
    /// 客戶入口的登入 cookie。名稱就是 ASP.NET Core Identity 預設的 application scheme：<c>SignInManager</c>
    /// 簽發 cookie 時用它，不必另外改。與營運者的 <see cref="UiCookieScheme"/> 是不同 scheme，互不通用。
    /// </summary>
    public const string PortalCookieScheme = "Identity.Application";

    /// <summary>客戶入口的有效 session（已登入、cookie 的租戶就是目前路徑的租戶）；改密碼頁用這個。</summary>
    public const string PortalSessionPolicy = "PortalSession";

    /// <summary>在 <see cref="PortalSessionPolicy"/> 之上，還要已改過營運者經手的初始密碼；入口其餘頁面用這個。</summary>
    public const string PortalPolicy = "Portal";

    /// <summary>客戶入口登入與改密碼的 rate limiting policy：依來源位址分區，只限制 POST。</summary>
    public const string PortalSignInRateLimit = "portal-sign-in";

    /// <summary>依租戶分區的 rate limiting policy。</summary>
    public const string PerTenantRateLimit = "per-tenant";
}