using System.Text.Json.Serialization;

using EnvCite.ServiceDefaults;

using MailKit.Security;

namespace EnvCite.Platform.Modules.Mail;

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