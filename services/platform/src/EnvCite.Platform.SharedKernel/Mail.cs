using EnvCite.ServiceDefaults;

using MailKit;
using MailKit.Net.Smtp;
using MailKit.Security;

using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
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
/// <c>Platform:Mail</c> 區段：寄邀請與驗證信的 SMTP 中繼。預設關閉。
/// 這是不加密、免驗證的內網中繼（只有來源位址被中繼信任），所以刻意沒有帳密與 TLS 設定；
/// 信裡因此<b>只放單次、限時的連結，不放任何密碼</b>。
/// </summary>
public sealed class MailOptions : ISectionOptions
{
    public static string SectionName => "Platform:Mail";

    public bool Enabled { get; set; }

    public string Host { get; set; } = string.Empty;

    public int Port { get; set; } = 25;

    /// <summary>寄件者位址；中繼通常只收自己網域的寄件者。</summary>
    public string From { get; set; } = string.Empty;

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

    /// <summary>連結來源 <see cref="BaseUrl"/> 解析後的絕對網址；沒設或不合法為 null。</summary>
    public Uri? BaseUri => Uri.TryCreate(BaseUrl, UriKind.Absolute, out var uri) && IsOrigin(uri) ? uri : null;

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

        if (!System.Net.Mail.MailAddress.TryCreate(options.From, out var from) || from.Address != options.From.Trim())
        {
            return ValidateOptionsResult.Fail("Platform:Mail:From must be a valid mail address when Platform:Mail:Enabled is true.");
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
/// 以 MailKit 寄信到 SMTP 中繼：純 TCP、不 STARTTLS、不驗證（<see cref="SecureSocketOptions.None"/>、沒有 Authenticate），
/// 因為校內中繼就是這樣。信是 multipart/alternative：純文字在前、HTML 在後（RFC 2046：最後一個是最偏好的）。
/// 日誌只記例外型別：收件者、主旨與內文（含連結權杖）都不進日誌，例外訊息也不記（可能回顯位址）。
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
            await client.ConnectAsync(settings.Host, settings.Port, SecureSocketOptions.None, timeout.Token);
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
            or System.IO.IOException or System.Net.Sockets.SocketException)
        {
            DeliveryFailed(logger, exception.GetType().Name, (exception as SmtpCommandException)?.StatusCode);
            return MailDelivery.Failed;
        }
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Mail delivery failed: {ExceptionType} {SmtpStatus}")]
    private static partial void DeliveryFailed(ILogger logger, string exceptionType, SmtpStatusCode? smtpStatus);
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
        services.AddSectionOptions<MailOptions, MailOptionsValidator>();
        services.AddSectionOptions<MailTemplateOptions, MailTemplateOptionsValidator>();
        services.TryAddSingleton<MailRenderServices>();
        services.TryAddSingleton(provider => new HtmlRenderer(provider.GetRequiredService<MailRenderServices>(), provider.GetRequiredService<ILoggerFactory>()));
        services.TryAddSingleton<IMailComposer, MailComposer>();
        services.TryAddSingleton<IMailSender, SmtpMailSender>();
        return services;
    }
}