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

    /// <summary>
    /// 密碼對，但這個帳號啟用了兩步驟驗證：還<b>沒有</b>簽發登入 cookie，只有一張五分鐘內有效、
    /// 只能用來輸入第二步驗證碼的暫存憑證。
    /// </summary>
    RequiresTwoFactor,

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

public sealed record MemberInfo(
    string Id, string Email, string? DisplayName, string Role, bool MustChangePassword, bool LockedOut, bool TwoFactorEnabled, bool Disabled);

/// <param name="IsYou">這一列是目前操作的人自己：自己不能停用、不能改自己的角色。</param>
/// <param name="CanManage">目前操作的人能不能管理這一位（依角色階層；不含「最後一位擁有者」這類狀態規則，那由動作自己擋）。</param>
public sealed record TeamMember(MemberInfo Member, bool IsYou, bool CanManage);

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

    /// <summary>
    /// 成員弄丟了驗證器也沒有復原碼：關掉他的兩步驟驗證、丟掉金鑰與復原碼、讓既有 session 失效。
    /// 他下次用密碼登入後可以重新註冊。這是繞過第二步的唯一人工途徑，所以必須留稽核，並且要先確認對方是本人。
    /// </summary>
    Task<AdminOutcome<MemberInfo>> ResetTwoFactorAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken);
}

/// <param name="SharedKey">給人手動輸入驗證器的金鑰（分組的 base32）。</param>
/// <param name="OtpauthUri"><c>otpauth://totp/…</c>，驗證器 App 掃的 QR 就是它；含金鑰，不可寫進日誌。</param>
public sealed record TwoFactorSetup(string SharedKey, string OtpauthUri);

/// <summary>
/// 客戶入口的兩步驟驗證（TOTP，RFC 6238）：註冊、登入的第二步、復原碼。與 <see cref="IPortalAccount"/> 一樣，
/// 在目前請求的租戶內，並留稽核。第二步驗證碼的失敗和密碼失敗<b>共用同一個鎖定計數</b>：
/// 否則知道密碼的人可以每次重新輸入密碼來歸零計數，無限次猜六位數。
/// </summary>
public interface IPortalTwoFactor
{
    /// <summary>目前登入的成員是否已啟用。</summary>
    Task<bool> IsEnabledAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>
    /// 取得（沒有就產生）還沒啟用的金鑰與 QR 的內容。重複呼叫回傳同一把，所以重新整理頁面不會讓已掃的 QR 失效。
    /// 已啟用則 <see cref="AdminStatus.Invalid"/>。
    /// </summary>
    Task<AdminOutcome<TwoFactorSetup>> BeginSetupAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>用驗證器上的碼確認註冊；成功才啟用，回傳十組復原碼（只此一次，之後只存在資料庫）。</summary>
    Task<AdminOutcome<IReadOnlyList<string>>> EnableAsync(ClaimsPrincipal user, string code, CancellationToken cancellationToken);

    /// <summary>這個成員的角色是否被要求使用兩步驟驗證（Owner、Admin，且設定要求）。被要求的人不能自己關閉。</summary>
    bool IsRequired(ClaimsPrincipal user);

    /// <summary>
    /// 自己關閉：要目前的密碼<b>與</b>驗證器的碼（只知道其中一個的人，例如借到手機或看到密碼，不能關掉）。
    /// 被要求的角色回 <see cref="AdminStatus.Invalid"/>。成功會丟掉金鑰與復原碼。
    /// </summary>
    Task<AdminOutcome<bool>> DisableAsync(ClaimsPrincipal user, string password, string code, CancellationToken cancellationToken);

    /// <summary>用驗證器的碼確認後，產生一組新的十個復原碼（舊的全部作廢），只此一次顯示。</summary>
    Task<AdminOutcome<IReadOnlyList<string>>> RegenerateRecoveryCodesAsync(ClaimsPrincipal user, string code, CancellationToken cancellationToken);

    /// <summary>目前這個請求是否帶著這個租戶、五分鐘內有效的「密碼已通過、等第二步」憑證。</summary>
    Task<bool> HasPendingSignInAsync();

    /// <param name="code">驗證器的六位數碼，或一組復原碼。</param>
    /// <param name="isRecoveryCode">true 表示 <paramref name="code"/> 是復原碼（每組只能用一次）。</param>
    /// <returns><see cref="PortalSignInStatus.Succeeded"/>、<see cref="PortalSignInStatus.MustChangePassword"/> 或 <see cref="PortalSignInStatus.Failed"/>。</returns>
    Task<PortalSignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken);
}

/// <summary>
/// 客戶自己的管理員管理成員（不經營運者）：新增、停用、改角色、重設密碼。租戶是目前請求的租戶；操作的人由已登入的
/// 身分決定，<b>角色以資料庫裡的為準、不信 cookie 裡的 claim</b>（claim 可能已過時）。階層規則：擁有者管全部；
/// 管理員只管開發者、帳務、唯讀，不能碰擁有者與管理員，也不能把任何人升到自己的層級以上；沒有人能動自己；
/// 因此組織裡一定留著一位啟用中的擁有者（構造上成立，見 <c>MemberSelfService</c>）。每個動作都留稽核，actor 是這位成員。
/// 以 scoped 註冊。
/// </summary>
public interface IMemberSelfService
{
    /// <summary>這位操作者可以指派的角色（擁有者：全部；管理員：開發者、帳務、唯讀；其他：空）。</summary>
    IReadOnlyList<string> GrantableRoles(ClaimsPrincipal actor);

    /// <returns>不是擁有者或管理員 → <see cref="AdminStatus.Forbidden"/>。</returns>
    Task<AdminOutcome<IReadOnlyList<TeamMember>>> ListAsync(ClaimsPrincipal actor, CancellationToken cancellationToken);

    /// <summary>新增成員，回傳一次性的初始密碼（操作者親手交給對方；對方第一次登入必須改）。</summary>
    Task<AdminOutcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, string email, string? displayName, string role, CancellationToken cancellationToken);

    /// <summary>停用或重新啟用。停用會讓對方所有 session 立刻失效。</summary>
    Task<AdminOutcome<MemberInfo>> SetDisabledAsync(ClaimsPrincipal actor, string userId, bool disabled, CancellationToken cancellationToken);

    Task<AdminOutcome<MemberInfo>> ChangeRoleAsync(ClaimsPrincipal actor, string userId, string role, CancellationToken cancellationToken);

    /// <summary>為忘記密碼的成員設一組新的一次性密碼；對方所有 session 失效，下次登入必須改。</summary>
    Task<AdminOutcome<IssuedPassword>> ResetPasswordAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken);
}