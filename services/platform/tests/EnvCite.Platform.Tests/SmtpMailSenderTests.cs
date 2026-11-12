using System.Net;
using System.Net.Sockets;
using System.Text;

using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 對真的 TCP 連線測 SMTP 寄信：行程內的假中繼只說最小的 SMTP，而且<b>不宣告</b> STARTTLS 與 AUTH——
/// 校內中繼是不加密、免驗證的，寄信端不能因為它們不存在就失敗，也不能自己試著要求它們。
/// </summary>
public sealed class SmtpMailSenderTests
{
    private static ServiceProvider Sender(Dictionary<string, string?> settings, CapturingLoggerProvider? logs = null)
    {
        var services = new ServiceCollection();
        services.AddSingleton<IConfiguration>(new ConfigurationBuilder().AddInMemoryCollection(settings).Build());
        services.AddLogging(b => b.AddProvider(logs ?? new CapturingLoggerProvider()));
        services.AddPlatformMail();
        return services.BuildServiceProvider();
    }

    private static Dictionary<string, string?> Settings(int port, bool enabled = true) => new()
    {
        ["Platform:Mail:Enabled"] = enabled ? "true" : "false",
        ["Platform:Mail:Host"] = "127.0.0.1",
        ["Platform:Mail:Port"] = port.ToString(System.Globalization.CultureInfo.InvariantCulture),
        ["Platform:Mail:From"] = "envcite@relay.test",
        ["Platform:Mail:FromName"] = "環境知識",
        ["Platform:Mail:BaseUrl"] = "https://platform.test",
        ["Platform:Mail:TimeoutSeconds"] = "5",
    };

    [Fact]
    public async Task ItDeliversPlainTextOverAnUnencryptedUnauthenticatedConnection()
    {
        await using var relay = new FakeRelay();
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", "連結：https://platform.test/x?code=abc", TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        var mail = Assert.Single(relay.Received);
        Assert.Equal("envcite@relay.test", mail.From);
        Assert.Equal(["someone@recipient.test"], mail.Recipients);
        Assert.Equal("主旨", mail.Message.Subject);
        Assert.Contains("連結：https://platform.test/x?code=abc", mail.Message.TextBody, StringComparison.Ordinal);
        Assert.Equal("auto-generated", mail.Message.Headers["Auto-Submitted"]); // RFC 3834

        // 自報的名稱與 Message-Id 都用寄件者的網域，不洩漏執行環境的主機名稱。
        Assert.StartsWith("EHLO relay.test", relay.Commands[0], StringComparison.Ordinal);
        Assert.EndsWith("@relay.test", mail.Message.MessageId, StringComparison.Ordinal);
        Assert.DoesNotContain(relay.Commands, c => c.StartsWith("AUTH", StringComparison.OrdinalIgnoreCase));
        Assert.DoesNotContain(relay.Commands, c => c.StartsWith("STARTTLS", StringComparison.OrdinalIgnoreCase));
    }

    // 寄件者顯示名稱：Platform:Mail:FromName 有設就以它為準；沒設（或空白）用系統名稱 Platform:Brand:Name。
    [Theory]
    [InlineData(null, null, "環境知識")] // 都沒設：預設系統名稱
    [InlineData(null, "測試系統", "測試系統")] // 改系統名稱，寄件者名稱跟著變
    [InlineData("", "測試系統", "測試系統")] // 空字串視為沒設
    [InlineData("   ", "測試系統", "測試系統")]
    [InlineData("客服信箱", "測試系統", "客服信箱")] // 有設就是它，不受系統名稱影響
    [InlineData("客服信箱", null, "客服信箱")]
    public async Task TheSenderNameIsTheFromNameAndOtherwiseTheBrandName(string? fromName, string? brandName, string expected)
    {
        await using var relay = new FakeRelay();
        var settings = Settings(relay.Port);
        settings.Remove("Platform:Mail:FromName");
        if (fromName is not null)
        {
            settings["Platform:Mail:FromName"] = fromName;
        }

        if (brandName is not null)
        {
            settings["Platform:Brand:Name"] = brandName;
        }

        using var provider = Sender(settings);

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", "內文", TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        var from = Assert.Single(Assert.Single(relay.Received).Message.From.Mailboxes);
        Assert.Equal(expected, from.Name);
        Assert.Equal("envcite@relay.test", from.Address);
    }

    [Fact]
    public async Task WhenMailIsNotEnabledItNeverConnects()
    {
        await using var relay = new FakeRelay();
        using var provider = Sender(Settings(relay.Port, enabled: false));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", "內文", TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Disabled, result);
        Assert.Equal(0, relay.Connections);
    }

    // 寄不出去不是例外：呼叫端（建立帳號）要能照常把密碼交給操作者。日誌不得帶收件者、主旨或內文（內文有一次性密碼）。
    [Fact]
    public async Task AnUnreachableRelayIsAFailureNotAnExceptionAndTheLogsHoldNoSecrets()
    {
        var logs = new CapturingLoggerProvider();
        using var provider = Sender(Settings(UnusedPort()), logs);

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨-不能進日誌", "密碼-不能進日誌", TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        var dump = string.Join('\n', logs.Messages);
        Assert.Contains("Mail delivery failed", dump, StringComparison.Ordinal);
        Assert.DoesNotContain("someone@recipient.test", dump, StringComparison.Ordinal);
        Assert.DoesNotContain("不能進日誌", dump, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ARelayThatRefusesTheRecipientIsAFailure()
    {
        await using var relay = new FakeRelay { RefuseRecipients = true };
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", "內文", TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        Assert.Empty(relay.Received);
    }

    [Fact]
    public async Task ABadRecipientAddressIsAFailureAndNeverReachesTheRelay()
    {
        await using var relay = new FakeRelay();
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("not an address", "主旨", "內文", TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        Assert.Equal(0, relay.Connections);
    }

    // 呼叫端自己取消要照常傳出去，不能被當成寄信失敗吞掉。
    [Fact]
    public async Task TheCallersOwnCancellationIsNotSwallowed()
    {
        await using var relay = new FakeRelay();
        using var provider = Sender(Settings(relay.Port));
        using var cancelled = new CancellationTokenSource();
        await cancelled.CancelAsync();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", "內文", cancelled.Token));
    }

    private static int UnusedPort()
    {
        using var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        return ((IPEndPoint)probe.LocalEndpoint).Port;
    }

    private sealed record ReceivedMail(string From, IReadOnlyList<string> Recipients, MimeKit.MimeMessage Message);

    /// <summary>只會 EHLO／MAIL／RCPT／DATA／QUIT 的 SMTP 伺服器；回應的功能清單不含 STARTTLS 與 AUTH。</summary>
    private sealed class FakeRelay : IAsyncDisposable
    {
        private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
        private readonly CancellationTokenSource _stop = new();
        private readonly Task _loop;
        private readonly List<ReceivedMail> _received = [];
        private readonly List<string> _commands = [];
        private int _connections;

        public FakeRelay()
        {
            _listener.Start();
            _loop = Task.Run(AcceptAsync);
        }

        public int Port => ((IPEndPoint)_listener.LocalEndpoint).Port;

        public bool RefuseRecipients { get; init; }

        public int Connections => Volatile.Read(ref _connections);

        public IReadOnlyList<ReceivedMail> Received
        {
            get
            {
                lock (_received)
                {
                    return [.. _received];
                }
            }
        }

        public IReadOnlyList<string> Commands
        {
            get
            {
                lock (_commands)
                {
                    return [.. _commands];
                }
            }
        }

        public async ValueTask DisposeAsync()
        {
            await _stop.CancelAsync();
            _listener.Stop();
            try
            {
                await _loop;
            }
            catch (Exception ex) when (ex is OperationCanceledException or ObjectDisposedException or SocketException)
            {
                // 關閉時的預期結果。
            }

            _stop.Dispose();
        }

        private async Task AcceptAsync()
        {
            while (!_stop.IsCancellationRequested)
            {
                var client = await _listener.AcceptTcpClientAsync(_stop.Token);
                _ = Task.Run(() => ServeAsync(client));
            }
        }

        private async Task ServeAsync(TcpClient client)
        {
            Interlocked.Increment(ref _connections);
            using var _ = client;
            await using var stream = client.GetStream();
            // Latin-1：一個位元組對一個字元，DATA 的原始位元組之後交給 MimeKit 解析。
            using var reader = new StreamReader(stream, Encoding.Latin1);
            await using var writer = new StreamWriter(stream, new UTF8Encoding(false)) { NewLine = "\r\n", AutoFlush = true };

            string? from = null;
            var recipients = new List<string>();
            await writer.WriteLineAsync("220 relay.test ESMTP");
            while (await reader.ReadLineAsync() is { } line)
            {
                lock (_commands)
                {
                    _commands.Add(line);
                }

                var verb = line.Split(' ', 2)[0].ToUpperInvariant();
                switch (verb)
                {
                    case "EHLO":
                        await writer.WriteLineAsync("250 relay.test");
                        break;
                    case "MAIL":
                        from = Address(line);
                        await writer.WriteLineAsync("250 OK");
                        break;
                    case "RCPT":
                        if (RefuseRecipients)
                        {
                            await writer.WriteLineAsync("550 5.7.1 relaying denied");
                            break;
                        }

                        recipients.Add(Address(line));
                        await writer.WriteLineAsync("250 OK");
                        break;
                    case "DATA":
                        await writer.WriteLineAsync("354 end with <CRLF>.<CRLF>");
                        var data = new StringBuilder();
                        while (await reader.ReadLineAsync() is { } dataLine && dataLine != ".")
                        {
                            data.Append(dataLine.StartsWith("..", StringComparison.Ordinal) ? dataLine[1..] : dataLine).Append("\r\n");
                        }

                        lock (_received)
                        {
                            _received.Add(new ReceivedMail(from ?? string.Empty, [.. recipients], MimeKit.MimeMessage.Load(new MemoryStream(Encoding.Latin1.GetBytes(data.ToString())))));
                        }

                        await writer.WriteLineAsync("250 queued");
                        break;
                    case "QUIT":
                        await writer.WriteLineAsync("221 bye");
                        return;
                    default:
                        await writer.WriteLineAsync("250 OK");
                        break;
                }
            }
        }

        /// <summary>「MAIL FROM:&lt;a@b&gt; SIZE=…」只取角括號裡的位址。</summary>
        private static string Address(string command)
        {
            var start = command.IndexOf('<', StringComparison.Ordinal) + 1;
            return command[start..command.IndexOf('>', StringComparison.Ordinal)];
        }
    }
}