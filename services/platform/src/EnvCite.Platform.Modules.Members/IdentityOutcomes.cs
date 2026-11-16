using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>Identity 的錯誤轉成穩定的結果代碼（Identity 的描述是英文、不隨語言，不直接傳給使用者）。</summary>
internal static class IdentityOutcomes
{
    /// <summary>建立或重設帳號時 Identity 拒絕：沒有對應的代碼，描述放在參數裡（英文，給 API 與日誌）。</summary>
    public static Outcome<T> Rejected<T>(IdentityError error) =>
        Outcomes.Invalid<T>(OutcomeCodes.IdentityRejected, error.Description);

    /// <summary>建立帳號失敗：email 已被用（營運者與成員各有自己的代碼）是衝突，其他是 Identity 拒絕。</summary>
    public static Outcome<T> CreateFailed<T>(IdentityResult created, string emailTakenCode) =>
        created.Errors.Any(e => e.Code is nameof(IdentityErrorDescriber.DuplicateUserName) or nameof(IdentityErrorDescriber.DuplicateEmail))
            ? Outcomes.Conflict<T>(emailTakenCode)
            : Rejected<T>(created.Errors.First());

    /// <summary>改密碼時 Identity 拒絕：目前的密碼不對、太短、太長、含有 email、太容易被猜到，或其他不符規則。</summary>
    public static Outcome<T> PasswordRejected<T>(IdentityError error) => error.Code switch
    {
        nameof(IdentityErrorDescriber.PasswordMismatch) => Outcomes.Invalid<T>(OutcomeCodes.PasswordCurrentIncorrect),
        nameof(IdentityErrorDescriber.PasswordTooShort) => Outcomes.Invalid<T>(OutcomeCodes.PasswordTooShort, PasswordPolicy.MinimumLength),
        PasswordPolicyErrors.TooLong => Outcomes.Invalid<T>(OutcomeCodes.PasswordTooLong, PasswordPolicy.MaximumLength),
        PasswordPolicyErrors.ContainsAccountInfo => Outcomes.Invalid<T>(OutcomeCodes.PasswordContainsAccountInfo),
        PasswordPolicyErrors.TooGuessable => Outcomes.Invalid<T>(OutcomeCodes.PasswordTooGuessable),
        _ => Outcomes.Invalid<T>(OutcomeCodes.PasswordNotAcceptable),
    };
}