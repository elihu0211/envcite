using System.Text;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.WebUtilities;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 邀請與驗證連結的權杖，全用 Identity 內建的電子郵件確認權杖（<c>GenerateEmailConfirmationTokenAsync</c>，
/// 資料保護加密、綁使用者 Id 與安全戳記、限時）。不自製權杖，也不存權杖：用過、過期或安全戳記改變，連結就失效。
/// </summary>
internal static class Activation
{
    /// <summary>
    /// 產生連結要帶的 code（Base64Url，可放進網址）。<paramref name="revokeOld"/> 先換安全戳記，使先前寄出的連結失效；
    /// 只用在還沒有密碼的帳號（沒有 session 會被波及）。
    /// </summary>
    public static async Task<string> NewCodeAsync<TUser>(UserManager<TUser> users, TUser user, bool revokeOld)
        where TUser : class
    {
        if (revokeOld)
        {
            await users.UpdateSecurityStampAsync(user);
        }

        var token = await users.GenerateEmailConfirmationTokenAsync(user);
        return WebEncoders.Base64UrlEncode(Encoding.UTF8.GetBytes(token));
    }

    /// <summary>網址上的 code 還原成權杖；格式不對一律 null（來自使用者輸入，不能讓亂打的值變成 500）。</summary>
    public static string? Decode(string? code)
    {
        if (string.IsNullOrEmpty(code) || code.Length > 2048)
        {
            return null;
        }

        try
        {
            return Encoding.UTF8.GetString(WebEncoders.Base64UrlDecode(code));
        }
        catch (FormatException)
        {
            return null;
        }
    }

    /// <summary>檢視連結，沒有副作用。使用者不存在、被停用、權杖不對或過期，都是 <see cref="ActivationState.Invalid"/>。</summary>
    public static async Task<ActivationState> InspectAsync<TUser, TKey>(UserManager<TUser> users, TUser? user, string? code)
        where TUser : IdentityUser<TKey>
        where TKey : IEquatable<TKey>
    {
        if (user is null || IsDisabled(user) || Decode(code) is not { } token)
        {
            return ActivationState.Invalid;
        }

        var valid = await users.VerifyUserTokenAsync(
            user, users.Options.Tokens.EmailConfirmationTokenProvider, UserManager<TUser>.ConfirmEmailTokenPurpose, token);
        if (!valid)
        {
            return ActivationState.Invalid;
        }

        return await users.HasPasswordAsync(user) ? ActivationState.ConfirmEmail : ActivationState.SetPassword;
    }

    /// <summary>
    /// 使用連結。邀請連結：先驗權杖（不改任何東西）→ 設密碼（Identity 驗證規則，不過則連結仍有效）→ 標記信箱已驗證。
    /// 設密碼會換安全戳記，連結就此失效；並發的第二次提交會因為「已經有密碼」而失敗。驗證連結：<c>ConfirmEmailAsync</c>（同樣換戳記）。
    /// </summary>
    public static async Task<(AdminOutcome<ActivationState> Outcome, TUser? User)> CompleteAsync<TUser, TKey>(
        UserManager<TUser> users, TUser? user, string? code, string? newPassword)
        where TUser : IdentityUser<TKey>
        where TKey : IEquatable<TKey>
    {
        var state = await InspectAsync<TUser, TKey>(users, user, code);
        if (state == ActivationState.Invalid || user is null)
        {
            return (AdminOutcome.Invalid<ActivationState>(OutcomeCodes.ActivationLinkInvalid), null);
        }

        if (state == ActivationState.ConfirmEmail)
        {
            var confirmed = await users.ConfirmEmailAsync(user, Decode(code)!);
            return confirmed.Succeeded
                ? (AdminOutcome.Ok(state), user)
                : (AdminOutcome.Invalid<ActivationState>(OutcomeCodes.ActivationLinkInvalid), null);
        }

        if (string.IsNullOrEmpty(newPassword))
        {
            return (AdminOutcome.Invalid<ActivationState>(OutcomeCodes.PasswordTooShort, MembersModule.MinimumPasswordLength), null);
        }

        var added = await users.AddPasswordAsync(user, newPassword);
        if (!added.Succeeded)
        {
            return (IdentityOutcomes.PasswordRejected<ActivationState>(added.Errors.First()), null);
        }

        user.EmailConfirmed = true;
        var saved = await users.UpdateAsync(user);
        return saved.Succeeded
            ? (AdminOutcome.Ok(state), user)
            : (IdentityOutcomes.Rejected<ActivationState>(saved.Errors.First()), null);
    }

    private static bool IsDisabled<TUser>(TUser user) => user switch
    {
        ApplicationUser member => member.Disabled,
        OperatorUser op => op.Disabled,
        _ => false,
    };
}

/// <summary>邀請與驗證連結的網址：來源取自設定（<see cref="MailOptions.BaseUrl"/>），絕不取自請求的 Host 標頭。</summary>
internal static class ActivationLinks
{
    public static Uri? ForMember(MailOptions options, string tenantIdentifier, string userId, string code) =>
        Build(options, PortalPaths.Activate(tenantIdentifier), userId, code);

    public static Uri? ForOperator(MailOptions options, string userId, string code) =>
        Build(options, "/ui/activate", userId, code);

    private static Uri? Build(MailOptions options, string path, string userId, string code) =>
        options.BaseUri is { } origin
            ? new Uri(origin, $"{path}?userId={Uri.EscapeDataString(userId)}&code={code}")
            : null;
}

/// <summary>邀請與驗證信（內容由 <see cref="MailTemplate"/> 產生）。信裡只有單次、限時的連結：不放密碼，也不放使用者的其他資料。</summary>
internal static class ActivationMail
{
    public static Task<MailDelivery> SendAsync(
        IMailSender mail, string brand, string recipient, Uri? link, TimeSpan lifetime, string? tenantIdentifier, bool invitation, CancellationToken cancellationToken)
    {
        if (link is null)
        {
            return Task.FromResult(MailDelivery.Failed); // 沒有設定連結來源（Platform:Mail:BaseUrl），寄出去的連結沒用
        }

        var content = MailTemplate.Activation(brand, link, lifetime, tenantIdentifier, invitation);
        return mail.SendAsync(recipient, invitation ? $"{brand}：邀請您啟用帳號" : $"{brand}：請驗證您的信箱", content, cancellationToken);
    }

    public static string Status(MailDelivery delivery) => delivery switch
    {
        MailDelivery.Sent => "sent",
        MailDelivery.Failed => "failed",
        MailDelivery.Disabled => "disabled",
        _ => "not_requested",
    };
}