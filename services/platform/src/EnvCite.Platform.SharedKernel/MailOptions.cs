using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.SharedKernel;

// 寄信的設定分三個選項類別，各自單一職責、各自驗證，鍵仍在同一個區段 Platform:Mail（鍵名是與部署庫的契約，拆類別不改鍵）。
// 這裡只有功能層（呼叫端讀）；傳輸層、寄件者與版型屬於寄信模組（Modules.Mail），只有它讀。

/// <summary>
/// <c>Platform:Mail</c> 區段的功能層：寄邀請與驗證信的 SMTP 中繼是否啟用，以及信裡連結的來源與期限。預設關閉。
/// 信裡<b>只放單次、限時的連結，不放任何密碼</b>。中繼與帳密見 <c>MailTransportOptions</c>，寄件者見 <c>MailIdentityOptions</c>（寄信模組）。
/// 其餘鍵只在啟用時驗證，未啟用時被忽略。
/// </summary>
public sealed class MailOptions : ISectionOptions
{
    public static string SectionName => "Platform:Mail";

    public bool Enabled { get; set; }

    /// <summary>
    /// 信裡連結的來源（<c>https://主機</c>，不含路徑）。寫死在設定，<b>不從請求的 Host 標頭組出來</b>：
    /// 否則攻擊者能讓信裡的連結指向他的網站（OWASP Forgot Password Cheat Sheet）。
    /// </summary>
    public string BaseUrl { get; set; } = string.Empty;

    /// <summary>邀請與驗證連結的有效期限；也是 Identity 資料保護權杖的期限。</summary>
    public TimeSpan LinkLifetime { get; set; } = TimeSpan.FromDays(3);

    /// <summary>連結來源 <see cref="BaseUrl"/> 解析後的絕對網址；沒設或不合法為 null。</summary>
    public Uri? BaseUri => Uri.TryCreate(BaseUrl, UriKind.Absolute, out var uri) && IsOrigin(uri) ? uri : null;

    internal static bool IsOrigin(Uri uri) =>
        (uri.Scheme == Uri.UriSchemeHttps || (uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback))
        && uri.AbsolutePath == "/"
        && string.IsNullOrEmpty(uri.Query)
        && string.IsNullOrEmpty(uri.Fragment)
        && string.IsNullOrEmpty(uri.UserInfo);
}