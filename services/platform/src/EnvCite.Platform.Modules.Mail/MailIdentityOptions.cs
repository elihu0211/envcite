using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Modules.Mail;

// 寄信的設定分三個選項類別（功能層 MailOptions 在 SharedKernel）：傳輸層 MailTransportOptions、寄件者 MailIdentityOptions，
// 版型 MailTemplateOptions（Platform:Mail:Template）。跨類別的規則在 AddMailModule 的註冊裡明說（預設值）與
// MailSettingsAdvisor（警告），不散在寄信的方法裡。

/// <summary>
/// <c>Platform:Mail</c> 區段的寄件者：信的 <c>From</c>、顯示名稱與 <c>Reply-To</c>。
/// 要求登入的中繼（例如 Exchange）要求寄件者等於認證的帳號，所以 <see cref="From"/> 沒設而
/// <see cref="MailTransportOptions.Username"/> 是 email 時預設用它（<see cref="DefaultFromUsername"/>，在驗證之前套用）。
/// </summary>
public sealed class MailIdentityOptions : ISectionOptions
{
    public static string SectionName => MailOptions.SectionName;

    /// <summary>寄件者位址；中繼通常只收自己網域的寄件者。</summary>
    public string From { get; set; } = string.Empty;

    /// <summary>寄件者顯示名稱；沒設（或空白）時用系統名稱 <see cref="BrandOptions.Name"/>。</summary>
    public string? FromName { get; set; }

    /// <summary>選填：信件的 <c>Reply-To</c> 位址；收件者按「回覆」時寄到這裡，不必是寄件者本身。</summary>
    public string? ReplyTo { get; set; }

    /// <summary>
    /// <c>PostConfigure</c>：<see cref="From"/> 沒設而帳號是 email 時用帳號。在驗證之前套用，
    /// 所以驗證器與所有使用端看到的是同一個值。帳號只在這裡被讀來比對，不被複製到別處。
    /// </summary>
    internal static void DefaultFromUsername(MailIdentityOptions identity, IOptions<MailTransportOptions> transport)
    {
        if (string.IsNullOrWhiteSpace(identity.From) && MailAddresses.IsValid(transport.Value.Username))
            identity.From = transport.Value.Username!.Trim();
    }
}

internal static class MailAddresses
{
    /// <summary>是不是單純的 email 位址（不含顯示名稱，前後空白不算）。</summary>
    public static bool IsValid(string? value) =>
        value is not null && System.Net.Mail.MailAddress.TryCreate(value, out var address) && address.Address == value.Trim();
}