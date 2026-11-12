using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>營運者帳號。營運者不屬於任何租戶，登入名稱就是 email；每位都必須使用兩步驟驗證。</summary>
/// <param name="EmailConfirmed">信箱已透過連結驗證。沒驗證的信箱不能用於復原。</param>
/// <param name="Invited">已邀請、還沒設定密碼（尚未開啟邀請連結）。</param>
public sealed record OperatorInfo(
    string Id, string Email, string? DisplayName, bool MustChangePassword, bool LockedOut, bool TwoFactorEnabled, bool Disabled,
    bool EmailConfirmed = false, bool Invited = false);

/// <summary>邀請（或重寄）的結果：<paramref name="Mail"/> 是邀請信的寄送結果。信裡只有連結，沒有密碼。</summary>
public sealed record OperatorInvitation(OperatorInfo Operator, MailDelivery Mail);

/// <summary>初始密碼只在建立或重設當下回傳一次；資料庫只存雜湊，之後無法取回。</summary>
public sealed record IssuedOperatorPassword(OperatorInfo Operator, string Password);

/// <summary>
/// 後台的帳密登入與改密碼（營運者版的 <see cref="IPortalAccount"/>，但沒有租戶）。營運者只有少數幾位，
/// 所以稽核寫入失敗就讓動作失敗（fail closed）。
/// </summary>
public interface IOperatorAccount : IAccountLifecycle;

/// <summary>後台的兩步驟驗證。營運者版的 <see cref="IPortalTwoFactor"/>，差別是<b>每位營運者都必須使用</b>，所以沒有「關閉」。</summary>
public interface IOperatorTwoFactor : ITwoFactor;

/// <summary>
/// 管理營運者帳號（後台、命令列與本機開發的種子資料共用）。沒有自助註冊：
/// 帳號只能由已被信任的營運者或系統管理者建立。啟用寄信時是<b>邀請</b>（<see cref="InviteAsync"/>）：對方開啟單次、限時的連結自己設定密碼並驗證信箱，
/// 沒有人經手密碼；沒啟用時由建立者把一次性的初始密碼親手交給對方（對方第一次登入必須改掉）。兩條路之後都要註冊驗證器。
/// 每個動作都留稽核，<paramref name="actor"/> 由已驗證的身分決定，不接受自報。
/// </summary>
public interface IOperatorAdmin
{
    Task<AdminOutcome<IReadOnlyList<OperatorInfo>>> ListAsync(CancellationToken cancellationToken);

    Task<AdminOutcome<IssuedOperatorPassword>> CreateAsync(
        ClaimsPrincipal actor, string email, string? displayName, CancellationToken cancellationToken);

    /// <summary>
    /// 邀請：建立沒有密碼的帳號，寄單次、限時的連結讓對方自己設定密碼並驗證信箱。沒啟用寄信時回 <see cref="OutcomeCodes.MailNotEnabled"/>，
    /// 改用 <see cref="CreateAsync"/>（暫時密碼）。寄信失敗不讓建立失敗，結果在 <see cref="OperatorInvitation.Mail"/>，可再 <see cref="ResendInvitationAsync"/>。
    /// </summary>
    Task<AdminOutcome<OperatorInvitation>> InviteAsync(
        ClaimsPrincipal actor, string email, string? displayName, CancellationToken cancellationToken);

    /// <summary>
    /// 重寄：還沒設密碼的帳號重發邀請連結（先前的連結失效）；已有密碼但信箱沒驗證的帳號寄驗證連結；
    /// 已啟用且已驗證則 <see cref="OutcomeCodes.AccountAlreadyActive"/>。
    /// </summary>
    Task<AdminOutcome<OperatorInvitation>> ResendInvitationAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken);

    /// <summary>設一組新的一次性密碼、解除鎖定、讓既有 session 失效，並要求下次登入改密碼。</summary>
    Task<AdminOutcome<IssuedOperatorPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken);

    /// <summary>
    /// 營運者弄丟了驗證器也沒有復原碼：關掉他的兩步驟驗證、丟掉金鑰與復原碼、解除鎖定、讓既有 session 失效。
    /// 他下次用密碼登入後必須重新註冊。這是繞過第二步的唯一人工途徑，要先確認對方是本人。
    /// </summary>
    Task<AdminOutcome<OperatorInfo>> ResetTwoFactorAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken);

    /// <summary>停用或重新啟用（冪等）。停用讓既有 session 在下一個請求就失效；帳號與稽核紀錄都保留。</summary>
    Task<AdminOutcome<OperatorInfo>> SetDisabledAsync(
        ClaimsPrincipal actor, string operatorId, bool disabled, CancellationToken cancellationToken);
}

/// <summary>某個後台 session 此刻對應的帳號狀態（來自資料庫，不是 cookie 裡的 claim）。</summary>
public sealed record OperatorSessionState(bool MustChangePassword, bool TwoFactorEnabled);

/// <summary>
/// 授權用：每個請求都重查 session 對應的營運者帳號。不開自己的 scope 就不能在 Blazor 的長壽命連線裡用，
/// 所以這個服務是 singleton，每次呼叫自己開 scope（理由同 <see cref="IAuditQuery"/>）。
/// </summary>
public interface IOperatorSessions
{
    /// <returns>
    /// 帳號存在、沒被停用、且 session 的安全戳記仍是目前的才回傳狀態；否則 null
    /// （帳號被刪、被停用、密碼或兩步驟驗證被重設都讓既有 session 失效）。
    /// </returns>
    Task<OperatorSessionState?> FindAsync(ClaimsPrincipal user, CancellationToken cancellationToken);
}