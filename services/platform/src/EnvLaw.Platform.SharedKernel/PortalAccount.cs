using System.Security.Claims;

namespace EnvLaw.Platform.SharedKernel;

/// <summary>客戶入口的角色。授權只讀 role claim，不依賴 Identity 的內部類別（見 07-01 §6）。</summary>
public static class CustomerRoles
{
    public const string Owner = "TenantOwner";
    public const string Admin = "TenantAdmin";
    public const string Developer = "Developer";
    public const string Billing = "Billing";
    public const string Viewer = "Viewer";

    public static IReadOnlyList<string> All { get; } = [Owner, Admin, Developer, Billing, Viewer];
}

/// <summary>客戶入口的路徑慣例。租戶在路徑裡：<c>/portal/{租戶識別碼}/…</c>，登入前就能解析租戶。</summary>
public static class PortalPaths
{
    public const string Prefix = "/portal";

    public static string Login(string tenantIdentifier) => $"{Prefix}/{Uri.EscapeDataString(tenantIdentifier)}/login";

    public static string Home(string tenantIdentifier) => $"{Prefix}/{Uri.EscapeDataString(tenantIdentifier)}";

    public static string Password(string tenantIdentifier) => $"{Prefix}/{Uri.EscapeDataString(tenantIdentifier)}/password";

    /// <summary>自請求路徑取出租戶識別碼；不是入口路徑則 null。</summary>
    public static string? TenantFrom(Microsoft.AspNetCore.Http.PathString path)
    {
        if (!path.StartsWithSegments(Prefix, out var remaining))
        {
            return null;
        }

        var segments = remaining.Value?.Split('/', StringSplitOptions.RemoveEmptyEntries);
        return segments is { Length: > 0 } ? Uri.UnescapeDataString(segments[0]) : null;
    }
}

public sealed record PortalTenant(string Id, string Identifier, string? Name);

public enum PortalSignInStatus
{
    Succeeded,

    /// <summary>密碼對，但這個密碼是營運者經手的，必須先改。已簽發 cookie，只能進改密碼頁。</summary>
    MustChangePassword,

    /// <summary>帳號、密碼不對，或帳號被暫時鎖定。刻意不區分：區分了就是在告訴攻擊者哪些 email 有帳號。</summary>
    Failed,
}

/// <summary>
/// 客戶入口的帳密登入。租戶是目前請求的租戶（登入前由路徑解析）；所有動作都在這個租戶內。
/// 以 scoped 註冊：登入要寫 cookie，需要目前的請求。
/// </summary>
public interface IPortalAccount
{
    /// <summary>這個請求（由路徑）解析出的租戶；路徑上的租戶不存在則 null，頁面據此回 404。</summary>
    PortalTenant? CurrentTenant { get; }

    Task<PortalSignInStatus> SignInAsync(string email, string password, CancellationToken cancellationToken);

    Task SignOutAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>驗證目前密碼後改密碼；成功會換新 cookie（舊的 session 失效），並清除「必須改密碼」。</summary>
    Task<AdminOutcome<bool>> ChangePasswordAsync(
        ClaimsPrincipal user, string currentPassword, string newPassword, CancellationToken cancellationToken);
}

public sealed record MemberInfo(string Id, string Email, string? DisplayName, string Role, bool MustChangePassword, bool LockedOut);

/// <summary>初始密碼只在建立或重設當下回傳一次；資料庫只存雜湊，之後無法取回。</summary>
public sealed record IssuedPassword(MemberInfo Member, string Password);

/// <summary>
/// 營運者管理租戶的成員。邀請與寄信的基礎設施還不存在，所以現階段由營運者建立帳號並把一次性的初始密碼
/// 交給客戶（客戶第一次登入必須改掉）；這是過渡做法，不是終態（07-01 §5）。每個動作都留稽核。
/// </summary>
public interface IMemberAdmin
{
    Task<AdminOutcome<IReadOnlyList<MemberInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken);

    Task<AdminOutcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, string tenantId, string email, string? displayName, string role,
        CancellationToken cancellationToken);

    /// <summary>設一組新的一次性密碼、解除鎖定、讓既有 session 失效，並要求下次登入改密碼。</summary>
    Task<AdminOutcome<IssuedPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken);
}