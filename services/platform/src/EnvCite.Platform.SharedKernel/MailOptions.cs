using System.Text.Json.Serialization;

using EnvCite.ServiceDefaults;

using MailKit.Security;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// SMTP 連線的加密模式，一對一對應 MailKit 的 <see cref="SecureSocketOptions"/>（鍵值是與部署庫的契約，所以不直接暴露函式庫的列舉）。
/// 預設 <see cref="None"/> 是為了不破壞既有部署（來源位址被信任的內網中繼）；<see cref="None"/> 與 <see cref="StartTlsWhenAvailable"/>
/// 都可能以明文傳輸：前者從不加密，後者在伺服器沒有公告 STARTTLS 時（包括被中間人拿掉公告）不加密地繼續。
/// 能選就選 <see cref="StartTls"/> 或 <see cref="SslOnConnect"/>。
/// </summary>
public enum MailSecurity
{
    /// <summary>不使用 SSL／TLS。</summary>
    None,

    /// <summary>連線後以 STARTTLS 升級；伺服器不支援就連線失敗。</summary>
    StartTls,

    /// <summary>伺服器公告 STARTTLS 才升級，否則不加密地繼續。</summary>
    StartTlsWhenAvailable,

    /// <summary>連上就是 SSL／TLS（通常是 465 埠）。</summary>
    SslOnConnect,

    /// <summary>交給 MailKit 依埠號決定（465 為 <see cref="SslOnConnect"/>，其餘為 <see cref="StartTlsWhenAvailable"/>）；不保證有加密。</summary>
    Auto,
}

// 寄信的設定分三個選項類別，各自單一職責、各自驗證，鍵仍在同一個區段 Platform:Mail（鍵名是與部署庫的契約，拆類別不改鍵）：
//   MailOptions          功能層：是否啟用、信裡連結的來源與期限（呼叫端讀）
//   MailTransportOptions 傳輸層：中繼、加密、帳密、時限（只有傳輸層讀）
//   MailIdentityOptions  寄件者：位址、顯示名稱、回覆位址
// 版型是 Platform:Mail:Template（MailTemplateOptions）。跨類別的規則在 AddPlatformMail 的註冊裡明說（預設值）與
// MailSettingsAdvisor（警告），不散在寄信的方法裡。

/// <summary>
/// <c>Platform:Mail</c> 區段的功能層：寄邀請與驗證信的 SMTP 中繼是否啟用，以及信裡連結的來源與期限。預設關閉。
/// 信裡<b>只放單次、限時的連結，不放任何密碼</b>。中繼與帳密見 <see cref="MailTransportOptions"/>，寄件者見 <see cref="MailIdentityOptions"/>。
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

/// <summary>
/// <c>Platform:Mail</c> 區段的傳輸層：怎麼連到 SMTP 中繼。<b>只有傳輸層（<c>SmtpMailSender</c>）讀 <see cref="Password"/></b>；
/// 其他程式只問 <see cref="HasCredentials"/>。連線預設不加密、免驗證（來源位址被中繼信任的內網中繼）；
/// 中繼要求登入或加密時設 <see cref="Security"/> 與 <see cref="Username"/>、<see cref="Password"/>。
/// <see cref="Password"/> 是機密：只經環境變數（k8s Secret 的 <c>secretKeyRef</c>），不進日誌、稽核、例外訊息、命令列輸出、
/// <see cref="ToString"/> 與序列化。
/// </summary>
public sealed class MailTransportOptions : ISectionOptions
{
    public static string SectionName => MailOptions.SectionName;

    public string Host { get; set; } = string.Empty;

    public int Port { get; set; } = 25;

    /// <summary>加密模式；沒設時維持不加密（<see cref="MailSecurity.None"/>），見 <see cref="MailSecurity"/>。</summary>
    public MailSecurity Security { get; set; } = MailSecurity.None;

    /// <summary>登入中繼的帳號；與 <see cref="Password"/> 要嘛都設、要嘛都不設。不進日誌與命令列輸出，也不被序列化。</summary>
    [JsonIgnore]
    public string? Username { get; set; }

    /// <summary>登入中繼的密碼；機密，只經環境變數。不進日誌、稽核、例外訊息與任何輸出。</summary>
    [JsonIgnore]
    public string? Password { get; set; }

    /// <summary>連線、傳送與等待回應的總時限（秒）。逾時算寄信失敗，不拖住建立帳號的請求。</summary>
    public int TimeoutSeconds { get; set; } = 10;

    /// <summary>是否以帳密登入中繼。</summary>
    [JsonIgnore]
    public bool HasCredentials => !string.IsNullOrWhiteSpace(Username) && !string.IsNullOrEmpty(Password);

    /// <summary>帳密在網路上可能以明文傳送：沒有加密，或只在伺服器公告 STARTTLS 時才加密。</summary>
    [JsonIgnore]
    public bool SendsCredentialsInClear => HasCredentials && Security is MailSecurity.None or MailSecurity.StartTlsWhenAvailable;

    /// <summary>只列非機密的欄位；帳號與密碼不出現，所以即使選項被字串化（日誌、診斷）也不外洩。</summary>
    public override string ToString() =>
        $"{nameof(MailTransportOptions)} {{ {nameof(Host)} = {Host}, {nameof(Port)} = {Port}, {nameof(Security)} = {Security}, {nameof(HasCredentials)} = {HasCredentials} }}";
}

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
        {
            identity.From = transport.Value.Username!.Trim();
        }
    }
}

internal static class MailAddresses
{
    /// <summary>是不是單純的 email 位址（不含顯示名稱，前後空白不算）。</summary>
    public static bool IsValid(string? value) =>
        value is not null && System.Net.Mail.MailAddress.TryCreate(value, out var address) && address.Address == value.Trim();
}