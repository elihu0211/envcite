using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>Identity 的錯誤轉成穩定的結果代碼（Identity 的描述是英文、不隨語言，不直接傳給使用者）。</summary>
internal static class IdentityOutcomes
{
    /// <summary>建立或重設帳號時 Identity 拒絕：沒有對應的代碼，描述放在參數裡（英文，給 API 與日誌）。</summary>
    public static AdminOutcome<T> Rejected<T>(IdentityError error) =>
        AdminOutcome.Invalid<T>(OutcomeCodes.IdentityRejected, error.Description);

    /// <summary>改密碼時 Identity 拒絕：目前的密碼不對、太短、或不符規則。</summary>
    public static AdminOutcome<T> PasswordRejected<T>(IdentityError error) => error.Code switch
    {
        nameof(IdentityErrorDescriber.PasswordMismatch) => AdminOutcome.Invalid<T>(OutcomeCodes.PasswordCurrentIncorrect),
        nameof(IdentityErrorDescriber.PasswordTooShort) => AdminOutcome.Invalid<T>(OutcomeCodes.PasswordTooShort, MembersModule.MinimumPasswordLength),
        _ => AdminOutcome.Invalid<T>(OutcomeCodes.PasswordNotAcceptable),
    };
}