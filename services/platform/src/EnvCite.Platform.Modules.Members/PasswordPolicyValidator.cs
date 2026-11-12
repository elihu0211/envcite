using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;

using Zxcvbn;

namespace EnvCite.Platform.Modules.Members;

/// <summary><see cref="PasswordPolicyValidator{TUser}"/> 回報的錯誤代碼（<see cref="IdentityError.Code"/>）；與使用者型別無關，所以不放在泛型類別裡。</summary>
internal static class PasswordPolicyErrors
{
    public const string TooLong = "PasswordPolicyTooLong";
    public const string ContainsAccountInfo = "PasswordPolicyContainsAccountInfo";
    public const string TooGuessable = "PasswordPolicyTooGuessable";
}

/// <summary>
/// Identity 的密碼驗證擴充點（<see cref="IPasswordValidator{TUser}"/>），在內建的長度檢查之後執行，客戶入口的成員與後台的營運者共用。
/// 規則見 <see cref="PasswordPolicy"/>：不強制組成，只擋三種事——太長、含有自己的 email、太容易被猜到。
/// 「太容易被猜到」交給 zxcvbn（Dropbox 的強度估算，業界通用的做法），不自己維護常見密碼清單或樣式規則；
/// 帳號資料（email 與它的名稱部分）一併當成已知的猜測素材。錯誤的 <c>Code</c> 由 <see cref="IdentityOutcomes"/> 轉成穩定的結果代碼。
/// 密碼與帳號資料都不進日誌或錯誤描述。
/// </summary>
internal sealed class PasswordPolicyValidator<TUser> : IPasswordValidator<TUser>
    where TUser : class
{
    /// <summary>名稱部分太短（例如 <c>ab@…</c>）就不拿來比對，否則幾乎任何密碼都含有它。</summary>
    private const int MinimumNameLength = 4;

    public async Task<IdentityResult> ValidateAsync(UserManager<TUser> manager, TUser user, string? password)
    {
        // 空的、太短的由 Identity 內建的驗證器回報（它先執行）；這裡只補內建沒有的規則。
        if (string.IsNullOrEmpty(password))
        {
            return IdentityResult.Success;
        }

        if (password.Length > PasswordPolicy.MaximumLength)
        {
            return Fail(PasswordPolicyErrors.TooLong, $"Passwords can be at most {PasswordPolicy.MaximumLength} characters.");
        }

        var email = (await manager.GetEmailAsync(user))?.Trim();
        var name = email is { Length: > 0 } && email.IndexOf('@') is > 0 and var at ? email[..at] : null;
        if (Contains(password, email) || (name is { Length: >= MinimumNameLength } && Contains(password, name)))
        {
            return Fail(PasswordPolicyErrors.ContainsAccountInfo, "The password must not contain the account's email address.");
        }

        // 帳號資料也是猜測素材：「alice1234」對 alice@… 的帳號，比一般的「某個名字加數字」更容易被猜到。
        string[] known = [.. new[] { email, name }.OfType<string>().Where(value => value.Length > 0)];
        return Core.EvaluatePassword(password, known).Score < PasswordPolicy.MinimumScore
            ? Fail(PasswordPolicyErrors.TooGuessable, "The password is too easy to guess.")
            : IdentityResult.Success;
    }

    private static bool Contains(string password, string? value) =>
        !string.IsNullOrEmpty(value) && password.Contains(value, StringComparison.OrdinalIgnoreCase);

    private static IdentityResult Fail(string code, string description) =>
        IdentityResult.Failed(new IdentityError { Code = code, Description = description });
}