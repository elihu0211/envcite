using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 入口成員的登入還有效嗎：帳號還在、沒被停用、安全戳記沒變（改密碼與營運者重設都會換戳記）。
/// 與入口 cookie 每個請求做的檢查是同一份實作；差別只在呼叫者：HTTP 請求有租戶解析，
/// 互動式 circuit 的重驗沒有，所以這裡自己依 principal 的租戶 claim 開租戶 scope 檢查。
/// 沒有租戶 claim、租戶不存在、帳號在別的租戶，一律無效（fail closed）。
/// </summary>
public interface IPortalSessions
{
    Task<bool> IsStillValidAsync(ClaimsPrincipal user, CancellationToken cancellationToken);
}