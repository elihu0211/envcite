using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

using EnvCite.ServiceDefaults;

using MailKit;
using MailKit.Net.Smtp;
using MailKit.Security;

using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

using MimeKit;

namespace EnvCite.Platform.SharedKernel;

/// <summary>寄信的結果。寄不出去不讓動作失敗：呼叫端據此提示操作者重寄，或改用暫時密碼。</summary>
public enum MailDelivery
{
    /// <summary>沒有收件地址，沒有嘗試寄。</summary>
    NotRequested,

    /// <summary>部署者沒有啟用寄信（<c>Platform:Mail:Enabled</c>）。</summary>
    Disabled,

    /// <summary>中繼主機接受了信（只代表交給中繼，不代表對方收到）。</summary>
    Sent,

    /// <summary>連不上、逾時或被中繼拒絕。</summary>
    Failed,
}

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
/// <c>Platform:Mail</c> 區段：寄邀請與驗證信的 SMTP 中繼。預設關閉。
/// 連線預設不加密、免驗證（來源位址被中繼信任的內網中繼）；中繼要求登入或加密時設
/// <see cref="Security"/> 與 <see cref="Username"/>、<see cref="Password"/>。
/// 信裡<b>只放單次、限時的連結，不放任何密碼</b>。<see cref="Password"/> 是機密：只經環境變數（k8s Secret），
/// 不進日誌、稽核、例外訊息、命令列輸出與 <see cref="ToString"/>。
/// </summary>
public sealed class MailOptions : ISectionOptions
{
    public static string SectionName => "Platform:Mail";

    public bool Enabled { get; set; }

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

    /// <summary>
    /// 寄件者位址；中繼通常只收自己網域的寄件者，要求登入的中繼（例如 Exchange）更要求它等於認證的帳號。
    /// 沒設而 <see cref="Username"/> 是 email 時，預設用 <see cref="Username"/>。
    /// </summary>
    public string From { get; set; } = string.Empty;

    /// <summary>選填：信件的 <c>Reply-To</c> 位址；收件者按「回覆」時寄到這裡，不必是寄件者本身。</summary>
    public string? ReplyTo { get; set; }

    /// <summary>寄件者顯示名稱；沒設（或空白）時用系統名稱 <see cref="BrandOptions.Name"/>。</summary>
    public string? FromName { get; set; }

    /// <summary>連線、傳送與等待回應的總時限（秒）。逾時算寄信失敗，不拖住建立帳號的請求。</summary>
    public int TimeoutSeconds { get; set; } = 10;

    /// <summary>
    /// 信裡連結的來源（<c>https://主機</c>，不含路徑）。寫死在設定，<b>不從請求的 Host 標頭組出來</b>：
    /// 否則攻擊者能讓信裡的連結指向他的網站（OWASP Forgot Password Cheat Sheet）。
    /// </summary>
    public string BaseUrl { get; set; } = string.Empty;

    /// <summary>邀請與驗證連結的有效期限；也是 Identity 資料保護權杖的期限。</summary>
    public TimeSpan LinkLifetime { get; set; } = TimeSpan.FromDays(3);

    /// <summary>是否以帳密登入中繼。</summary>
    [JsonIgnore]
    public bool HasCredentials => !string.IsNullOrWhiteSpace(Username) && !string.IsNullOrEmpty(Password);

    /// <summary>帳密在網路上可能以明文傳送：沒有加密，或只在伺服器公告 STARTTLS 時才加密。</summary>
    [JsonIgnore]
    public bool SendsCredentialsInClear => HasCredentials && Security is MailSecurity.None or MailSecurity.StartTlsWhenAvailable;

    /// <summary>連結來源 <see cref="BaseUrl"/> 解析後的絕對網址；沒設或不合法為 null。</summary>
    public Uri? BaseUri => Uri.TryCreate(BaseUrl, UriKind.Absolute, out var uri) && IsOrigin(uri) ? uri : null;

    /// <summary>只列非機密的欄位；帳號與密碼不出現，所以即使選項被字串化（日誌、診斷）也不外洩。</summary>
    public override string ToString() =>
        $"{nameof(MailOptions)} {{ {nameof(Enabled)} = {Enabled}, {nameof(Host)} = {Host}, {nameof(Port)} = {Port}, {nameof(Security)} = {Security}, {nameof(HasCredentials)} = {HasCredentials} }}";

    /// <summary>
    /// 預設值：<see cref="From"/> 沒設而 <see cref="Username"/> 是 email 時用它（寄件者必須等於認證的帳號，否則 Exchange 回 550 5.7.60）。
    /// 在驗證之前套用（<c>PostConfigure</c>），所以驗證器與所有使用端看到的是同一個值。
    /// </summary>
    internal static void ApplyDefaults(MailOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.From) && IsMailAddress(options.Username))
        {
            options.From = options.Username!.Trim();
        }
    }

    internal static bool IsMailAddress(string? value) =>
        value is not null && System.Net.Mail.MailAddress.TryCreate(value, out var address) && address.Address == value.Trim();

    internal static bool IsOrigin(Uri uri) =>
        (uri.Scheme == Uri.UriSchemeHttps || (uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback))
        && uri.AbsolutePath == "/"
        && string.IsNullOrEmpty(uri.Query)
        && string.IsNullOrEmpty(uri.Fragment)
        && string.IsNullOrEmpty(uri.UserInfo);
}

internal sealed class MailOptionsValidator : IValidateOptions<MailOptions>
{
    public ValidateOptionsResult Validate(string? name, MailOptions options)
    {
        if (!options.Enabled)
        {
            return ValidateOptionsResult.Success;
        }

        if (string.IsNullOrWhiteSpace(options.Host))
        {
            return ValidateOptionsResult.Fail("Platform:Mail:Host is required when Platform:Mail:Enabled is true.");
        }

        if (options.Port is < 1 or > 65535)
        {
            return ValidateOptionsResult.Fail("Platform:Mail:Port must be between 1 and 65535.");
        }

        if (!Enum.IsDefined(options.Security))
        {
            return ValidateOptionsResult.Fail($"Platform:Mail:Security must be one of: {string.Join(", ", Enum.GetNames<MailSecurity>())}.");
        }

        // 只指名鍵，不帶值：帳號與密碼都不進訊息。
        if (string.IsNullOrWhiteSpace(options.Username) != string.IsNullOrEmpty(options.Password))
        {
            return ValidateOptionsResult.Fail(string.IsNullOrWhiteSpace(options.Username)
                ? "Platform:Mail:Username is required when Platform:Mail:Password is set (set both or neither)."
                : "Platform:Mail:Password is required when Platform:Mail:Username is set (set both or neither).");
        }

        if (!MailOptions.IsMailAddress(options.From))
        {
            return ValidateOptionsResult.Fail("Platform:Mail:From must be a valid mail address when Platform:Mail:Enabled is true (or set Platform:Mail:Username to a mail address).");
        }

        if (!string.IsNullOrWhiteSpace(options.ReplyTo) && !MailOptions.IsMailAddress(options.ReplyTo))
        {
            return ValidateOptionsResult.Fail("Platform:Mail:ReplyTo must be a valid mail address when set.");
        }

        if (options.BaseUri is null)
        {
            return ValidateOptionsResult.Fail("Platform:Mail:BaseUrl must be an https origin (http only for localhost) without a path when Platform:Mail:Enabled is true.");
        }

        if (options.TimeoutSeconds is < 1 or > 120)
        {
            return ValidateOptionsResult.Fail("Platform:Mail:TimeoutSeconds must be between 1 and 120.");
        }

        return options.LinkLifetime < TimeSpan.FromHours(1) || options.LinkLifetime > TimeSpan.FromDays(14)
            ? ValidateOptionsResult.Fail("Platform:Mail:LinkLifetime must be between 1 hour and 14 days.")
            : ValidateOptionsResult.Success;
    }
}

/// <summary>寄一封 multipart/alternative 信（純文字加 HTML）。實作不丟例外（呼叫端自己的取消除外），失敗以 <see cref="MailDelivery.Failed"/> 回報。</summary>
public interface IMailSender
{
    /// <summary>是否啟用寄信。沒啟用時，建立帳號改走「暫時密碼」，由操作者親手交付。</summary>
    bool Enabled { get; }

    Task<MailDelivery> SendAsync(string recipient, string subject, MailContent content, CancellationToken cancellationToken);
}

/// <summary>
/// 以 MailKit 寄信到 SMTP 中繼。加密模式照 <see cref="MailOptions.Security"/>（預設不加密）；設了帳密就在連線後呼叫
/// <c>AuthenticateAsync</c>，由 MailKit 依伺服器公告的機制協商（不自己寫 SASL、不自己寫 STARTTLS）。
/// 用到 TLS 時憑證照預設驗證，<b>不設 <c>ServerCertificateValidationCallback</c> 放行</b>：內部 CA 簽的憑證，做法是把 CA 放進容器信任。
/// 信是 multipart/alternative：純文字在前、HTML 在後（RFC 2046：最後一個是最偏好的）。
/// 日誌只記例外型別與 SMTP 狀態碼：收件者、帳號、密碼、主旨與內文（含連結權杖）都不進日誌，例外訊息也不記（可能回顯位址）。
/// </summary>
internal sealed partial class SmtpMailSender(IOptions<MailOptions> options, IOptions<BrandOptions> brand, ILogger<SmtpMailSender> logger) : IMailSender
{
    public bool Enabled => options.Value.Enabled;

    public async Task<MailDelivery> SendAsync(string recipient, string subject, MailContent content, CancellationToken cancellationToken)
    {
        var settings = options.Value;
        if (!settings.Enabled)
        {
            return MailDelivery.Disabled;
        }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(settings.TimeoutSeconds));

        try
        {
            using var message = new MimeMessage();
            var from = new MailboxAddress(string.IsNullOrWhiteSpace(settings.FromName) ? brand.Value.Name : settings.FromName, settings.From);
            message.From.Add(from);
            if (!string.IsNullOrWhiteSpace(settings.ReplyTo))
            {
                message.ReplyTo.Add(MailboxAddress.Parse(settings.ReplyTo));
            }

            // Message-Id 的網域用寄件者的網域，不用執行環境的主機名稱（pod 名稱會外洩，也不是中繼認得的名字）。
            message.MessageId = MimeKit.Utils.MimeUtils.GenerateMessageId(from.Domain);
            message.To.Add(MailboxAddress.Parse(recipient));
            message.Subject = subject;
            // RFC 3834：自動產生的信，收件端的自動回覆（休假通知等）不要回它。
            message.Headers.Add("Auto-Submitted", "auto-generated");
            message.Body = new MultipartAlternative
            {
                new TextPart("plain") { Text = content.Text },
                new TextPart("html") { Text = content.Html },
            };

            // EHLO 自報的名稱同理：中繼的反垃圾規則常檢查它，用寄件者的網域而不是本機主機名稱。
            using var client = new SmtpClient { Timeout = settings.TimeoutSeconds * 1000, LocalDomain = from.Domain };
            await client.ConnectAsync(settings.Host, settings.Port, settings.Security.ToSocketOptions(), timeout.Token);
            if (settings.HasCredentials)
            {
                // 帳號前後的空白幾乎都是 Secret 檔案多出來的換行；密碼則一個字元都不動。
                await client.AuthenticateAsync(settings.Username!.Trim(), settings.Password!, timeout.Token);
            }

            await client.SendAsync(message, timeout.Token);
            await client.DisconnectAsync(quit: true, timeout.Token);
            return MailDelivery.Sent;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception) when (exception is OperationCanceledException or CommandException or ProtocolException
            or ServiceNotConnectedException or FormatException or InvalidOperationException or TimeoutException
            or MailKit.Security.AuthenticationException or MailKit.Security.SslHandshakeException
            or System.Security.Authentication.AuthenticationException or NotSupportedException
            or System.IO.IOException or System.Net.Sockets.SocketException)
        {
            var (status, enhanced) = SmtpFailure.Describe(exception);
            DeliveryFailed(logger, exception.GetType().Name, status?.ToString(System.Globalization.CultureInfo.InvariantCulture) ?? "-", enhanced ?? "-");
            return MailDelivery.Failed;
        }
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Mail delivery failed: {ExceptionType} {SmtpStatus} {EnhancedStatus}")]
    private static partial void DeliveryFailed(ILogger logger, string exceptionType, string smtpStatus, string enhancedStatus);
}

/// <summary>
/// 從寄信的例外取出<b>只有數字的</b>診斷碼：SMTP 狀態碼（535 帳號密碼錯誤、550 被拒絕）與 RFC 3463 的增強狀態碼
/// （例如 Exchange 的 <c>5.7.60</c> 寄件者不是認證的帳號）。例外訊息本身不外傳：它可能回顯位址或帳號。
/// </summary>
internal static partial class SmtpFailure
{
    public static (int? Status, string? Enhanced) Describe(Exception exception)
    {
        // 登入失敗時 MailKit 把伺服器的 SMTP 回應包在 AuthenticationException 裡。
        var command = exception as SmtpCommandException ?? exception.InnerException as SmtpCommandException;
        if (command is null)
        {
            return (null, null);
        }

        var match = EnhancedStatus().Match(command.Message);
        return ((int)command.StatusCode, match.Success ? match.Groups["code"].Value : null);
    }

    // 增強狀態碼出現在回應文字的開頭：「5.7.60 Client does not have permissions…」。只取這一段數字。
    [GeneratedRegex(@"\A\s*(?<code>[245]\.\d{1,3}\.\d{1,3})(?=\s|\z)", RegexOptions.CultureInvariant, matchTimeoutMilliseconds: 1000)]
    private static partial Regex EnhancedStatus();
}

internal static class MailSecurityExtensions
{
    /// <summary>對應 MailKit 的連線選項；未定義的值不會到這裡（啟動時由驗證器擋下），到了就是程式錯誤。</summary>
    public static SecureSocketOptions ToSocketOptions(this MailSecurity security) => security switch
    {
        MailSecurity.None => SecureSocketOptions.None,
        MailSecurity.StartTls => SecureSocketOptions.StartTls,
        MailSecurity.StartTlsWhenAvailable => SecureSocketOptions.StartTlsWhenAvailable,
        MailSecurity.SslOnConnect => SecureSocketOptions.SslOnConnect,
        MailSecurity.Auto => SecureSocketOptions.Auto,
        _ => throw new ArgumentOutOfRangeException(nameof(security), security, "Unsupported mail security mode."),
    };
}

/// <summary>
/// 啟動時對寄信設定做<b>不該停下</b>、但該讓人看見的提醒（停下的錯誤由 <see cref="MailOptionsValidator"/> 處理）：
/// 帳密在可能明文的連線上傳送；寄件者與登入的帳號不同（Exchange 會拒絕，有些中繼允許 Send As）。
/// 日誌不含位址與帳密，只指名鍵。
/// </summary>
internal sealed partial class MailSettingsAdvisor(IOptions<MailOptions> options, ILogger<MailSettingsAdvisor> logger) : IHostedService
{
    public Task StartAsync(CancellationToken cancellationToken)
    {
        var settings = options.Value;
        if (settings.Enabled)
        {
            if (settings.SendsCredentialsInClear)
            {
                CredentialsMayTravelInClear(logger, settings.Security);
            }

            if (settings.HasCredentials && MailOptions.IsMailAddress(settings.Username)
                && !string.Equals(settings.From.Trim(), settings.Username!.Trim(), StringComparison.OrdinalIgnoreCase))
            {
                SenderDiffersFromAccount(logger);
            }
        }

        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    [LoggerMessage(Level = LogLevel.Warning, Message = "Platform:Mail has credentials but Platform:Mail:Security is {Security}: the username and password may travel over the network in clear text. Use StartTls or SslOnConnect when the relay supports it.")]
    private static partial void CredentialsMayTravelInClear(ILogger logger, MailSecurity security);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Platform:Mail:From differs from Platform:Mail:Username. Relays such as Exchange reject a sender that is not the authenticated account (550 5.7.60); keep them equal unless the relay allows Send As.")]
    private static partial void SenderDiffersFromAccount(ILogger logger);
}

public static class MailServiceCollectionExtensions
{
    /// <summary>
    /// 寄信所需的一切，集中在這一處註冊：<c>Platform:Mail</c>（傳輸）、<c>Platform:Mail:Template</c>（版型）、<c>Platform:Brand</c>（系統名稱）、
    /// 文案的本地化、<see cref="IMailComposer"/>（組信）與 <see cref="IMailSender"/>（只管傳輸）。
    /// 需要 <c>AddLogging()</c>（主機本來就有）。測試可在其後換掉 <see cref="IMailSender"/>。
    /// </summary>
    public static IServiceCollection AddPlatformMail(this IServiceCollection services)
    {
        services.AddPlatformBrand();
        services.AddLocalization();
        services.AddSectionOptions<MailOptions, MailOptionsValidator>().PostConfigure(MailOptions.ApplyDefaults);
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IHostedService, MailSettingsAdvisor>());
        services.AddSectionOptions<MailTemplateOptions, MailTemplateOptionsValidator>();
        services.TryAddSingleton<MailRenderServices>();
        services.TryAddSingleton(provider => new HtmlRenderer(provider.GetRequiredService<MailRenderServices>(), provider.GetRequiredService<ILoggerFactory>()));
        services.TryAddSingleton<IMailComposer, MailComposer>();
        services.TryAddSingleton<MailTestReport>();
        services.TryAddSingleton<IMailSender, SmtpMailSender>();
        return services;
    }
}