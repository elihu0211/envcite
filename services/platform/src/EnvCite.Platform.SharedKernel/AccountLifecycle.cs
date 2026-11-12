using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>登入與兩步驟驗證的結果。</summary>
public enum SignInStatus
{
    /// <summary>已簽發完整的 session。</summary>
    Succeeded,

    /// <summary>密碼對，但這個密碼是別人經手的，必須先改。已簽發 session，只能進改密碼頁。</summary>
    MustChangePassword,

    /// <summary>密碼對、密碼也改過了，但還沒有註冊兩步驟驗證（只有營運者；入口成員在入口內註冊）。已簽發 session，只能進註冊頁。</summary>
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
/// 營運者（<see cref="IOperatorAccount"/>）與客戶入口成員（<see cref="IPortalAccount"/>）共有的帳號流程：登入、登出、啟用連結、改密碼。
/// 頁面與元件（<c>LoginFlow</c>、<c>ChangePasswordFlow</c>、<c>ActivationFlow</c>）只依賴這個介面，不為兩邊各寫一份。
/// 每個動作都留稽核；以 scoped 註冊（登入要寫 cookie，需要目前的請求）。
/// </summary>
public interface IAccountLifecycle
{
    /// <summary>帳密登入。稽核寫入失敗會讓登入失敗（fail closed）：寧可登不進去，也不要有一次沒留紀錄的登入。</summary>
    Task<SignInStatus> SignInAsync(string email, string password, CancellationToken cancellationToken);

    /// <summary>登出：收回 session 與暫存憑證，並留稽核。</summary>
    Task SignOutAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>檢視邀請／驗證連結（沒有副作用，所以信件掃描器預先開啟連結不會用掉它）。</summary>
    Task<ActivationState> InspectActivationAsync(string? userId, string? code, CancellationToken cancellationToken);

    /// <summary>
    /// 使用連結：邀請連結要帶 <paramref name="newPassword"/>，設好密碼並驗證信箱；驗證連結只驗證信箱。
    /// 不簽發 session：使用者接著登入。失敗一律是 <see cref="OutcomeCodes.ActivationLinkInvalid"/>，
    /// 或密碼不符規則（連結此時仍有效，可以重打）。
    /// </summary>
    Task<AdminOutcome<ActivationState>> ActivateAsync(string? userId, string? code, string? newPassword, CancellationToken cancellationToken);

    /// <summary>驗證目前密碼後改密碼；成功會換新 cookie（其他裝置的 session 失效），並清除「必須改密碼」。</summary>
    Task<AdminOutcome<bool>> ChangePasswordAsync(
        ClaimsPrincipal user, string currentPassword, string newPassword, CancellationToken cancellationToken);
}

/// <summary>
/// 兩步驟驗證（TOTP，RFC 6238）：註冊、登入的第二步、復原碼。營運者（<see cref="IOperatorTwoFactor"/>）與入口成員
/// （<see cref="IPortalTwoFactor"/>）共用。第二步驗證碼的失敗和密碼失敗<b>共用同一個鎖定計數</b>：
/// 否則知道密碼的人可以每次重新輸入密碼來歸零計數，無限次猜六位數。
/// </summary>
public interface ITwoFactor
{
    /// <summary>目前登入的人是否已啟用。</summary>
    Task<bool> IsEnabledAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>
    /// 取得（沒有就產生）還沒啟用的金鑰與 QR 的內容。重複呼叫回傳同一把，所以重新整理頁面不會讓已掃的 QR 失效。
    /// 已啟用則 <see cref="AdminStatus.Invalid"/>。
    /// </summary>
    Task<AdminOutcome<TwoFactorSetup>> BeginSetupAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>用驗證器上的碼確認註冊；成功才啟用，回傳十組復原碼（只此一次，之後只存在資料庫）。</summary>
    Task<AdminOutcome<IReadOnlyList<string>>> EnableAsync(ClaimsPrincipal user, string code, CancellationToken cancellationToken);

    /// <summary>用驗證器的碼確認後，產生一組新的十個復原碼（舊的全部作廢），只此一次顯示。</summary>
    Task<AdminOutcome<IReadOnlyList<string>>> RegenerateRecoveryCodesAsync(ClaimsPrincipal user, string code, CancellationToken cancellationToken);

    /// <summary>目前這個請求是否帶著五分鐘內有效的「密碼已通過、等第二步」憑證（入口：且是這個租戶的）。</summary>
    Task<bool> HasPendingSignInAsync();

    /// <param name="code">驗證器的六位數碼，或一組復原碼。</param>
    /// <param name="isRecoveryCode">true 表示 <paramref name="code"/> 是復原碼（每組只能用一次）。</param>
    /// <returns><see cref="SignInStatus.Succeeded"/>、<see cref="SignInStatus.MustChangePassword"/> 或 <see cref="SignInStatus.Failed"/>。</returns>
    Task<SignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken);
}