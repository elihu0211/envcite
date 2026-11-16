using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <param name="IsYou">這一列是目前操作的人自己：自己不能停用、不能改自己的角色。</param>
/// <param name="CanManage">目前操作的人能不能管理這一位（依角色階層；不含「最後一位擁有者」這類狀態規則，那由動作自己擋）。</param>
public sealed record TeamMember(MemberInfo Member, bool IsYou, bool CanManage);

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

    /// <returns>不是擁有者或管理員 → <see cref="OutcomeStatus.Forbidden"/>。</returns>
    Task<Outcome<IReadOnlyList<TeamMember>>> ListAsync(ClaimsPrincipal actor, CancellationToken cancellationToken);

    /// <summary>新增成員，回傳一次性的初始密碼（操作者親手交給對方；對方第一次登入必須改）。</summary>
    Task<Outcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, string email, string? displayName, string role, CancellationToken cancellationToken);

    /// <summary>邀請：建立沒有密碼的成員，寄連結讓對方自己設定密碼並驗證信箱（權限規則同 <see cref="CreateAsync"/>）。沒啟用寄信時回 <see cref="OutcomeCodes.MailNotEnabled"/>。</summary>
    Task<Outcome<MemberInvitation>> InviteAsync(
        ClaimsPrincipal actor, string email, string? displayName, string role, CancellationToken cancellationToken);

    /// <summary>重寄邀請或驗證連結（規則同 <see cref="IMemberAdmin.ResendInvitationAsync"/>；要能管理對方，且不能對自己）。</summary>
    Task<Outcome<MemberInvitation>> ResendInvitationAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken);

    /// <summary>停用或重新啟用。停用會讓對方所有 session 立刻失效。</summary>
    Task<Outcome<MemberInfo>> SetDisabledAsync(ClaimsPrincipal actor, string userId, bool disabled, CancellationToken cancellationToken);

    Task<Outcome<MemberInfo>> ChangeRoleAsync(ClaimsPrincipal actor, string userId, string role, CancellationToken cancellationToken);

    /// <summary>為忘記密碼的成員設一組新的一次性密碼；對方所有 session 失效，下次登入必須改。</summary>
    Task<Outcome<IssuedPassword>> ResetPasswordAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken);
}