using System.Security.Claims;

using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// 互動式 circuit 的連線（<c>/_blazor</c>）要認哪個人。一個瀏覽器可以同時帶著營運者與入口成員兩張 cookie，
/// 而連線的網址不帶「這條 circuit 是為哪一頁開的」，所以連線層兩張都認，放進同一個 principal 的兩個 identity；
/// circuit 啟動時再依<b>第一個網址</b>挑出其中一個（<see cref="Narrow"/>），之後所有授權與重驗都只看見那一個。
/// 兩個 identity 不能留在同一個 principal 裡讓授權去讀：兩邊共用 <c>NameIdentifier</c>、<c>Email</c>、安全戳記與
/// 「待改密碼」「待註冊驗證器」的 claim 型別，混在一起，一邊的 claim 會被另一邊的檢查讀到。
/// </summary>
internal static class CircuitIdentities
{
    /// <summary>連線層：入口成員的 identity 併進原本認出的人（營運者或匿名）。原本是匿名就直接用入口的。</summary>
    public static ClaimsPrincipal Combine(ClaimsPrincipal current, ClaimsPrincipal portal) =>
        current.Identity?.IsAuthenticated == true
            ? new ClaimsPrincipal(current.Identities.Concat(portal.Identities))
            : portal;

    /// <summary>
    /// circuit 啟動時挑一個：入口的網址（<see cref="IsPortalPath"/>）只給入口成員的 identity，其餘一律不含它（等於連線層加入前的行為）。
    /// 要的那一邊沒有人就是匿名，不退而求其次：帶著營運者的身分進入口頁，或反過來，都是被授權擋下、換不到另一邊的權限。
    /// </summary>
    public static ClaimsPrincipal Narrow(ClaimsPrincipal connection, bool portalSurface)
    {
        var portal = connection.Identities.Where(IsPortal).ToList();
        if (portalSurface)
            return portal.Count > 0 ? new ClaimsPrincipal(portal[0]) : Anonymous();

        if (portal.Count == 0)
            return connection;

        var rest = connection.Identities.Where(identity => !IsPortal(identity)).ToList();
        return rest.Count > 0 ? new ClaimsPrincipal(rest) : Anonymous();
    }

    /// <summary>絕對網址（或以 <c>/</c> 開頭的路徑）是不是客戶入口底下的頁面；查詢字串與片段不算。</summary>
    public static bool IsPortalPath(string uri)
    {
        // 以 / 開頭的先當路徑：在 Unix 上 Uri 會把它當成 file:// 網址，查詢字串也不會照預期分開。
        var path = uri.StartsWith('/') ? uri : Uri.TryCreate(uri, UriKind.Absolute, out var absolute) ? absolute.AbsolutePath : null;
        if (path is null)
            return false;

        var end = path.AsSpan().IndexOfAny('?', '#');
        return new PathString(end < 0 ? path : path[..end]).StartsWithSegments(PortalPaths.Prefix);
    }

    private static bool IsPortal(ClaimsIdentity identity) =>
        identity.IsAuthenticated && identity.HasClaim(claim => claim.Type == PlatformClaimTypes.PortalUserId);

    private static ClaimsPrincipal Anonymous() => new(new ClaimsIdentity());
}