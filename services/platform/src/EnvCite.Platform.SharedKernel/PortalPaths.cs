namespace EnvCite.Platform.SharedKernel;

/// <summary>客戶入口的路徑慣例。租戶在路徑裡：<c>/portal/{租戶識別碼}/…</c>，登入前就能解析租戶。</summary>
public static class PortalPaths
{
    public const string Prefix = "/portal";

    public static string Login(string tenantIdentifier) => $"{Prefix}/{Uri.EscapeDataString(tenantIdentifier)}/login";

    public static string Home(string tenantIdentifier) => $"{Prefix}/{Uri.EscapeDataString(tenantIdentifier)}";

    /// <summary>一串對話（問答歷史）的頁面：可以接著問。</summary>
    public static string Conversation(string tenantIdentifier, Guid id) => $"{Home(tenantIdentifier)}/c/{id}";

    /// <summary>以 Google 登入的起點（POST）：把使用者送去 Google。</summary>
    public static string GoogleChallenge(string tenantIdentifier) => $"{Login(tenantIdentifier)}/google";

    /// <summary>Google 登入的第二段（GET）：從 Google 回來、經固定回呼收下身分之後，在租戶的路徑下完成登入。</summary>
    public static string GoogleCallback(string tenantIdentifier) => $"{Login(tenantIdentifier)}/google/callback";

    public static string Password(string tenantIdentifier) => $"{Prefix}/{Uri.EscapeDataString(tenantIdentifier)}/password";

    /// <summary>邀請／驗證連結的頁面；查詢字串帶 <c>userId</c> 與 <c>code</c>。</summary>
    public static string Activate(string tenantIdentifier) => $"{Prefix}/{Uri.EscapeDataString(tenantIdentifier)}/activate";

    /// <summary>入口成員大頭貼的快取 URL。</summary>
    public static string Avatar(string tenantIdentifier, string userId, int version) =>
        $"{Home(tenantIdentifier)}/avatar/{Uri.EscapeDataString(userId)}?v={version}";

    /// <summary>自請求路徑取出租戶識別碼；不是入口路徑則 null。</summary>
    public static string? TenantFrom(Microsoft.AspNetCore.Http.PathString path)
    {
        if (!path.StartsWithSegments(Prefix, out var remaining))
            return null;

        var segments = remaining.Value?.Split('/', StringSplitOptions.RemoveEmptyEntries);
        return segments is { Length: > 0 } ? Uri.UnescapeDataString(segments[0]) : null;
    }
}

/// <summary>Google 登入的固定部分。</summary>
public static class PortalGoogle
{
    /// <summary>
    /// Google 用戶端登記的回呼位址（各環境一個）。刻意在 <c>/portal</c> 之外：它沒有租戶，放進去會被當成一個叫 signin-google 的租戶。
    /// </summary>
    public const string CallbackPath = "/signin-google";

    /// <summary>起點把租戶識別碼放進（受保護的）驗證屬性的鍵；第二段比對它與路徑上的租戶，擋下被挪用到別的租戶的流程。</summary>
    public const string TenantItem = "envcite.tenant";
}

/// <summary>是否開啟 Google 登入（<c>Platform:Portal:Google</c>）：登入頁據此顯示按鈕，端點關閉時回 404。</summary>
public interface IGoogleSignInAvailability
{
    bool Enabled { get; }
}