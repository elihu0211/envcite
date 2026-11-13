using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 啟動時對寄信設定做<b>不該停下</b>、但該讓人看見的提醒（停下的錯誤由三個驗證器處理）：
/// 帳密在可能明文的連線上傳送；寄件者與登入的帳號不同（Exchange 會拒絕，有些中繼允許 Send As）。
/// 這是跨選項類別的規則（傳輸層的帳密與加密、寄件者的位址），所以放在這個明確的服務裡，不在寄信的方法裡。
/// 日誌不含位址與帳密，只指名鍵。
/// </summary>
internal sealed partial class MailSettingsAdvisor(
    IOptions<MailOptions> mail,
    IOptions<MailTransportOptions> transport,
    IOptions<MailIdentityOptions> identity,
    ILogger<MailSettingsAdvisor> logger) : IHostedService
{
    public Task StartAsync(CancellationToken cancellationToken)
    {
        if (!mail.Value.Enabled)
            return Task.CompletedTask;

        var connection = transport.Value;
        if (connection.SendsCredentialsInClear)
            CredentialsMayTravelInClear(logger, connection.Security);

        if (connection.HasCredentials && MailAddresses.IsValid(connection.Username)
            && !string.Equals(identity.Value.From.Trim(), connection.Username!.Trim(), StringComparison.OrdinalIgnoreCase))
        {
            SenderDiffersFromAccount(logger);
        }

        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    [LoggerMessage(Level = LogLevel.Warning, Message = "Platform:Mail has credentials but Platform:Mail:Security is {Security}: the username and password may travel over the network in clear text. Use StartTls or SslOnConnect when the relay supports it.")]
    private static partial void CredentialsMayTravelInClear(ILogger logger, MailSecurity security);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Platform:Mail:From differs from Platform:Mail:Username. Relays such as Exchange reject a sender that is not the authenticated account (550 5.7.60); keep them equal unless the relay allows Send As.")]
    private static partial void SenderDiffersFromAccount(ILogger logger);
}