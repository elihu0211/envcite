using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 營運者（<see cref="IOperatorAccount"/>）與客戶入口成員（<see cref="IPortalAccount"/>）共有的帳號流程：登出、啟用連結、改密碼。
/// 兩邊的登入與兩步驟驗證有各自的結果型別，仍留在各自的介面；這三件事一樣，所以頁面與元件（<c>ChangePasswordFlow</c>、<c>ActivationFlow</c>）
/// 只依賴這個介面，不為兩邊各寫一份。
/// </summary>
public interface IAccountLifecycle
{
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