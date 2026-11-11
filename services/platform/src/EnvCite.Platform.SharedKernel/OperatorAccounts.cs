using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>營運者帳號。營運者不屬於任何租戶，登入名稱就是 email；每位都必須使用兩步驟驗證。</summary>
public sealed record OperatorInfo(
    string Id, string Email, string? DisplayName, bool MustChangePassword, bool LockedOut, bool TwoFactorEnabled, bool Disabled);

/// <summary>初始密碼只在建立或重設當下回傳一次；資料庫只存雜湊，之後無法取回。</summary>
public sealed record IssuedOperatorPassword(OperatorInfo Operator, string Password);

public enum OperatorSignInStatus
{
    /// <summary>已簽發完整的後台 session。</summary>
    Succeeded,

    /// <summary>密碼對，但這個密碼是營運者經手的，必須先改。已簽發 session，只能進改密碼頁。</summary>
    MustChangePassword,

    /// <summary>密碼對、密碼也改過了，但還沒有註冊兩步驟驗證。已簽發 session，只能進註冊頁。</summary>
    MustEnrollTwoFactor,

    /// <summary>
    /// 密碼對，帳號已啟用兩步驟驗證：還<b>沒有</b>簽發 session，只有一張五分鐘內有效、
    /// 只能用來輸入第二步驗證碼的暫存憑證。
    /// </summary>
    RequiresTwoFactor,

    /// <summary>帳號或密碼不對、帳號被鎖定或被停用。刻意不區分：區分了就是在告訴攻擊者哪些 email 有帳號。</summary>
    Failed,
}

/// <summary>
/// 後台的帳密登入與改密碼（營運者版的 <see cref="IPortalAccount"/>，但沒有租戶）。每個動作都留稽核，
/// 稽核寫入失敗會讓動作失敗（fail closed）：營運者只有少數幾位，寧可登不進去也不要有一次沒留紀錄的登入。
/// 以 scoped 註冊：登入要寫 cookie，需要目前的請求。
/// </summary>
public interface IOperatorAccount
{
    Task<OperatorSignInStatus> SignInAsync(string email, string password, CancellationToken cancellationToken);

    /// <summary>登出：收回 session 與暫存憑證，並留稽核。</summary>
    Task SignOutAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>驗證目前密碼後改密碼；成功會換新 cookie（其他裝置的 session 失效），並清除「必須改密碼」。</summary>
    Task<AdminOutcome<bool>> ChangePasswordAsync(
        ClaimsPrincipal user, string currentPassword, string newPassword, CancellationToken cancellationToken);
}

/// <summary>
/// 後台的兩步驟驗證（TOTP，RFC 6238）：註冊、登入的第二步、復原碼。營運者版的 <see cref="IPortalTwoFactor"/>，
/// 差別是<b>每位營運者都必須使用</b>，所以沒有「關閉」。第二步的失敗與密碼失敗共用同一個鎖定計數。
/// </summary>
public interface IOperatorTwoFactor
{
    Task<bool> IsEnabledAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>取得（沒有就產生）還沒啟用的金鑰與 QR 的內容；重複呼叫回傳同一把。已啟用則 <see cref="AdminStatus.Invalid"/>。</summary>
    Task<AdminOutcome<TwoFactorSetup>> BeginSetupAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>用驗證器上的碼確認註冊；成功才啟用，回傳十組復原碼（只此一次）。</summary>
    Task<AdminOutcome<IReadOnlyList<string>>> EnableAsync(ClaimsPrincipal user, string code, CancellationToken cancellationToken);

    /// <summary>用驗證器的碼確認後，產生一組新的十個復原碼（舊的全部作廢），只此一次顯示。</summary>
    Task<AdminOutcome<IReadOnlyList<string>>> RegenerateRecoveryCodesAsync(ClaimsPrincipal user, string code, CancellationToken cancellationToken);

    /// <summary>目前這個請求是否帶著五分鐘內有效的「密碼已通過、等第二步」憑證。</summary>
    Task<bool> HasPendingSignInAsync();

    /// <param name="code">驗證器的六位數碼，或一組復原碼。</param>
    /// <param name="isRecoveryCode">true 表示 <paramref name="code"/> 是復原碼（每組只能用一次）。</param>
    /// <returns><see cref="OperatorSignInStatus.Succeeded"/>、<see cref="OperatorSignInStatus.MustChangePassword"/> 或 <see cref="OperatorSignInStatus.Failed"/>。</returns>
    Task<OperatorSignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken);
}

/// <summary>
/// 管理營運者帳號（後台、命令列與本機開發的種子資料共用）。沒有自助註冊：
/// 帳號只能由已被信任的營運者或系統管理者建立，並把一次性的初始密碼交給對方（對方第一次登入必須改掉，再註冊驗證器）。
/// 每個動作都留稽核，<paramref name="actor"/> 由已驗證的身分決定，不接受自報。
/// </summary>
public interface IOperatorAdmin
{
    Task<AdminOutcome<IReadOnlyList<OperatorInfo>>> ListAsync(CancellationToken cancellationToken);

    Task<AdminOutcome<IssuedOperatorPassword>> CreateAsync(
        ClaimsPrincipal actor, string email, string? displayName, CancellationToken cancellationToken);

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