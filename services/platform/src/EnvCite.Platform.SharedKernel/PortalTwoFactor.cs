using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <param name="SharedKey">給人手動輸入驗證器的金鑰（分組的 base32）。</param>
/// <param name="OtpauthUri"><c>otpauth://totp/…</c>，驗證器 App 掃的 QR 就是它；含金鑰，不可寫進日誌。</param>
public sealed record TwoFactorSetup(string SharedKey, string OtpauthUri);

/// <summary>客戶入口的兩步驟驗證：在目前請求的租戶內，並留稽核。</summary>
public interface IPortalTwoFactor : ITwoFactor
{
    /// <summary>這個成員的角色是否被要求使用兩步驟驗證（Owner、Admin，且設定要求）。被要求的人不能自己關閉。</summary>
    bool IsRequired(ClaimsPrincipal user);

    /// <summary>
    /// 自己關閉：要目前的密碼<b>與</b>驗證器的碼（只知道其中一個的人，例如借到手機或看到密碼，不能關掉）。
    /// 被要求的角色回 <see cref="OutcomeStatus.Invalid"/>。成功會丟掉金鑰與復原碼。
    /// </summary>
    Task<Outcome<bool>> DisableAsync(ClaimsPrincipal user, string password, string code, CancellationToken cancellationToken);
}