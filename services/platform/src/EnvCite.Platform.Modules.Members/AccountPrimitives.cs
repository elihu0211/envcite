using System.Net.Mail;
using System.Security.Cryptography;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;

using OtpNet;

namespace EnvCite.Platform.Modules.Members;

// 客戶入口的成員（ApplicationUser）與後台的營運者（OperatorUser）共用的小零件：兩邊各寫一份遲早會走樣。

/// <summary>營運者經手的一次性初始密碼。</summary>
internal static class OneTimePassword
{
    // 去掉容易看錯的字元（0/O、1/l/I）。16 個字元約 92 位元，遠高於需求；第一次登入就要改掉。
    private const string Alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private const int Length = 16;

    public static string New() => RandomNumberGenerator.GetString(Alphabet, Length);
}

internal static class EmailAddresses
{
    public static bool IsValid(string? email) =>
        !string.IsNullOrWhiteSpace(email)
        && email.Length <= 254
        && MailAddress.TryCreate(email.Trim(), out var parsed)
        && parsed.Address == email.Trim();
}

/// <summary>Identity 存驗證器金鑰與復原碼的固定位置（使用者的 token；與 <c>GetAuthenticatorKeyAsync</c> 讀的是同一格）。</summary>
internal static class TwoFactorTokens
{
    public const string Provider = "[AspNetUserStore]";
    public const string AuthenticatorKey = "AuthenticatorKey";
    public const string RecoveryCodes = "RecoveryCodes";
}

internal static class TotpSetup
{
    private const string Issuer = "envcite";

    /// <summary>
    /// 自己產生金鑰，不用 <c>ResetAuthenticatorKeyAsync</c>：它會順手更新安全戳記，使用者一打開設定頁，
    /// 自己目前的 session 就失效了。20 位元組 = RFC 4226 建議的長度。
    /// </summary>
    public static string NewKey() => Base32Encoding.ToString(KeyGeneration.GenerateRandomKey(20));

    public static TwoFactorSetup Create(string? email, string key)
    {
        var label = Uri.EscapeDataString($"{Issuer}:{email}");
        var uri = $"otpauth://totp/{label}?secret={key}&issuer={Uri.EscapeDataString(Issuer)}&digits=6&period=30";
        return new TwoFactorSetup(Grouped(key), uri);
    }

    /// <summary>四個字元一組，方便手動輸入時核對。</summary>
    private static string Grouped(string key) =>
        string.Join(' ', Enumerable.Range(0, (key.Length + 3) / 4).Select(i => key.Substring(i * 4, Math.Min(4, key.Length - (i * 4)))));
}

internal static class OperatorUsers
{
    /// <summary>
    /// 營運者的 Id 是 Guid，不是 Guid 的字串就是找不到。Identity 的 <c>FindByIdAsync</c> 會直接丟 <see cref="FormatException"/>，
    /// 而這個 Id 來自網址（<c>/admin/operators/{id}</c>），不能讓一個亂打的網址變成 500。
    /// </summary>
    public static async Task<OperatorUser?> FindOperatorAsync(this UserManager<OperatorUser> users, string? id) =>
        Guid.TryParse(id, out _) ? await users.FindByIdAsync(id!) : null;
}