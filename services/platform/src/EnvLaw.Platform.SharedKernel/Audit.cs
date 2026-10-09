using System.Security.Claims;

namespace EnvLaw.Platform.SharedKernel;

/// <summary>
/// 一筆要記下的稽核事件：<b>誰</b>（由請求的身分推得，見 <see cref="AuditActors"/>）對<b>什麼</b>做了什麼。
/// <c>Detail</c> 只放能公開給有權查看稽核的人的欄位，<b>絕不放密鑰、雜湊或完整 API key</b>。
/// </summary>
/// <param name="Action">穩定的事件鍵，形如 <c>api_key.issued</c>。</param>
/// <param name="TenantId">事件所關於的租戶；平台層的事件為 null。</param>
public sealed record AuditEntry(
    string Action,
    string? TenantId = null,
    string? TargetType = null,
    string? TargetId = null,
    IReadOnlyDictionary<string, object?>? Detail = null);

/// <summary>
/// 獨立寫入一筆稽核事件（自己的 scope 與交易）。用於沒有其他資料變更可以同交易寫入的事件：
/// 租戶建立（租戶 store 是另一個 DbContext）、營運者登入。與資料變更同交易的事件
/// 改用 <c>PlatformDbContext.StageAudit</c>，兩者一起成功或一起失敗。
/// </summary>
public interface IAuditLog
{
    Task AppendAsync(ClaimsPrincipal actor, AuditEntry entry, CancellationToken cancellationToken);
}

/// <summary>稽核裡的「誰」：從已驗證的身分推得，不接受呼叫端自報。</summary>
public static class AuditActors
{
    public const string OperatorKey = "operator-key";
    public const string OperatorUser = "operator-user";
    public const string TenantKey = "tenant-key";
    public const string TenantUser = "tenant-user";
    public const string Anonymous = "anonymous";
    public const string System = "system";

    public static (string Type, string? Id) From(ClaimsPrincipal? user)
    {
        if (user?.Identity?.IsAuthenticated != true)
        {
            return (Anonymous, null);
        }

        // 營運者 admin key：共用的一把 key，無從分辨是誰用的，所以記 key 本身。
        if (user.IsInRole("platform-admin"))
        {
            return (OperatorKey, "admin-key");
        }

        // 程式自己做的事（例如本機開發的種子資料）：只有行程內的程式碼造得出這個角色，外部請求的身分都不會帶它。
        if (user.IsInRole(System))
        {
            return (System, null);
        }

        // 客戶入口的人：記使用者 Id（email 可以改，Id 不會）；租戶記在事件的 TenantId。
        if (user.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is { } portalUserId)
        {
            return (TenantUser, portalUserId);
        }

        if (user.FindFirst(PlatformClaimTypes.ApiKeyId)?.Value is { } apiKeyId)
        {
            return (TenantKey, apiKeyId);
        }

        // 後台的營運者：記 email（營運者帳號不能改 email，而且稽核頁要看得出是誰）。
        if (user.FindFirst(ClaimTypes.Email)?.Value is { } email)
        {
            return (OperatorUser, email);
        }

        return (Anonymous, null);
    }
}