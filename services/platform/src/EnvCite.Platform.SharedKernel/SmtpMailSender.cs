using System.Text.RegularExpressions;

using MailKit;
using MailKit.Net.Smtp;
using MailKit.Security;

using Microsoft.Extensions.Logging;

using MimeKit;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 建立 SMTP 用戶端的工廠。MailKit 的 <see cref="SmtpClient"/> 不是執行緒安全，所以<b>每封信向工廠要一個新的</b>，用完由呼叫端釋放；
/// 工廠本身是無狀態的單例。測試以 DI 換掉它（指向測試用的 SMTP 伺服器、計數或失敗注入），寄信的程式不必知道。
/// </summary>
public interface ISmtpClientFactory
{
    /// <param name="localDomain">EHLO 自報的名稱；呼叫端用寄件者位址的網域，不用本機主機名稱（pod 名稱會外洩，也不是中繼認得的名字）。</param>
    ISmtpClient Create(string localDomain);
}

/// <summary>預設的工廠：時限取自 <see cref="MailTransportOptions.TimeoutSeconds"/>。憑證驗證照 MailKit 的預設，不在這裡放行。</summary>
internal sealed class SmtpClientFactory(IOptions<MailTransportOptions> transport) : ISmtpClientFactory
{
    public ISmtpClient Create(string localDomain) =>
        new SmtpClient { Timeout = transport.Value.TimeoutSeconds * 1000, LocalDomain = localDomain };
}

/// <summary>
/// 以 MailKit 寄信到 SMTP 中繼。加密模式照 <see cref="MailTransportOptions.Security"/>（預設不加密）；設了帳密就在連線後呼叫
/// <c>AuthenticateAsync</c>，由 MailKit 依伺服器公告的機制協商（不自己寫 SASL、不自己寫 STARTTLS）。
/// 用到 TLS 時憑證照預設驗證，<b>不設 <c>ServerCertificateValidationCallback</c> 放行</b>：內部 CA 簽的憑證，做法是把 CA 放進容器信任。
/// 信是 multipart/alternative：純文字在前、HTML 在後（RFC 2046：最後一個是最偏好的）。
/// 日誌只記例外型別與 SMTP 狀態碼：收件者、帳號、密碼、主旨與內文（含連結權杖）都不進日誌，例外訊息也不記（可能回顯位址）。
/// </summary>
internal sealed partial class SmtpMailSender(
    IOptions<MailOptions> mail,
    IOptions<MailTransportOptions> transport,
    IOptions<MailIdentityOptions> identity,
    IOptions<BrandOptions> brand,
    ISmtpClientFactory clients,
    ILogger<SmtpMailSender> logger) : IMailSender
{
    public bool Enabled => mail.Value.Enabled;

    public async Task<MailDelivery> SendAsync(string recipient, string subject, MailContent content, CancellationToken cancellationToken)
    {
        if (!Enabled)
            return MailDelivery.Disabled;

        var connection = transport.Value;
        var sender = identity.Value;
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(connection.TimeoutSeconds));

        try
        {
            using var message = new MimeMessage();
            var from = new MailboxAddress(string.IsNullOrWhiteSpace(sender.FromName) ? brand.Value.Name : sender.FromName, sender.From);
            message.From.Add(from);
            if (!string.IsNullOrWhiteSpace(sender.ReplyTo))
                message.ReplyTo.Add(MailboxAddress.Parse(sender.ReplyTo));

            // Message-Id 的網域用寄件者的網域，不用執行環境的主機名稱。
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

            // EHLO 自報的名稱同理：中繼的反垃圾規則常檢查它，用寄件者的網域。
            using var client = clients.Create(from.Domain);
            await client.ConnectAsync(connection.Host, connection.Port, connection.Security.ToSocketOptions(), timeout.Token);
            if (connection.HasCredentials)
            {
                // 帳號前後的空白幾乎都是 Secret 檔案多出來的換行；密碼則一個字元都不動。
                await client.AuthenticateAsync(connection.Username!.Trim(), connection.Password!, timeout.Token);
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
/// 碼的意思（給人看的原因）在 <c>mail test</c> 的 <see cref="IMailTestReport"/> 與 README 的排查表，不在這裡判斷。
/// </summary>
internal static partial class SmtpFailure
{
    public static (int? Status, string? Enhanced) Describe(Exception exception)
    {
        // 登入失敗時 MailKit 把伺服器的 SMTP 回應包在 AuthenticationException 裡。
        var command = exception as SmtpCommandException ?? exception.InnerException as SmtpCommandException;
        if (command is null)
            return (null, null);

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