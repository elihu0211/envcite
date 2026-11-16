using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <param name="EmailConfirmed">信箱已透過連結驗證。沒驗證的信箱不能用於復原。</param>
/// <param name="Invited">已邀請、還沒設定密碼（尚未開啟邀請連結）。</param>
/// <param name="Managed">由目錄同步管理：角色與停用由身分來源決定，介面與管理動作鎖住（見 <see cref="DirectoryRoles.ManagedBy"/>）。</param>
public sealed record MemberInfo(
    string Id, string Email, string? DisplayName, string Role, bool MustChangePassword, bool LockedOut, bool TwoFactorEnabled, bool Disabled,
    bool EmailConfirmed = false, bool Invited = false, bool Managed = false);

/// <summary>邀請（或重寄）的結果：<paramref name="Mail"/> 是邀請信的寄送結果。信裡只有連結，沒有密碼。</summary>
public sealed record MemberInvitation(MemberInfo Member, MailDelivery Mail);

/// <summary>初始密碼只在建立或重設當下回傳一次；資料庫只存雜湊，之後無法取回。</summary>
public sealed record IssuedPassword(MemberInfo Member, string Password);

/// <summary>
/// 營運者管理租戶的成員。啟用寄信（<c>Platform:Mail</c>）時，新增成員是<b>邀請</b>：帳號沒有密碼，信裡是單次、限時的連結，
/// 客戶開啟後自己設定密碼並同時驗證信箱，沒有人經手密碼。沒啟用寄信時，由營運者建立帳號並把一次性的初始密碼
/// 交給客戶（客戶第一次登入必須改掉）。每個動作都留稽核。
/// </summary>
public interface IMemberAdmin
{
    Task<Outcome<IReadOnlyList<MemberInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken);

    Task<Outcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, string tenantId, string email, string? displayName, string role,
        CancellationToken cancellationToken);

    /// <summary>邀請：建立沒有密碼的成員，寄連結讓對方自己設定密碼並驗證信箱。沒啟用寄信時回 <see cref="OutcomeCodes.MailNotEnabled"/>，改用 <see cref="CreateAsync"/>。</summary>
    Task<Outcome<MemberInvitation>> InviteAsync(
        ClaimsPrincipal actor, string tenantId, string email, string? displayName, string role,
        CancellationToken cancellationToken);

    /// <summary>重寄：還沒設密碼的重發邀請連結（先前的連結失效）；已有密碼但信箱沒驗證的寄驗證連結；已啟用且已驗證則 <see cref="OutcomeCodes.AccountAlreadyActive"/>。</summary>
    Task<Outcome<MemberInvitation>> ResendInvitationAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken);

    /// <summary>設一組新的一次性密碼、解除鎖定、讓既有 session 失效，並要求下次登入改密碼。</summary>
    Task<Outcome<IssuedPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken);

    /// <summary>
    /// 停用或重新啟用。客戶自己的管理員做得到時不需要營運者；但最後一位擁有者離開、或兩位擁有者同時互相停用，
    /// 客戶就沒有人能做了，這時由營運者做。停用會讓對方所有 session 立刻失效。
    /// </summary>
    Task<Outcome<MemberInfo>> SetDisabledAsync(
        ClaimsPrincipal actor, string tenantId, string userId, bool disabled, CancellationToken cancellationToken);

    /// <summary>
    /// 成員弄丟了驗證器也沒有復原碼：關掉他的兩步驟驗證、丟掉金鑰與復原碼、讓既有 session 失效。
    /// 他下次用密碼登入後可以重新註冊。這是繞過第二步的唯一人工途徑，所以必須留稽核，並且要先確認對方是本人。
    /// </summary>
    Task<Outcome<MemberInfo>> ResetTwoFactorAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken);
}