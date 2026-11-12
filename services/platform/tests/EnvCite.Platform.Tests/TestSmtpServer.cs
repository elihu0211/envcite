using System.Buffers;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

using MimeKit;

using SmtpServer;
using SmtpServer.Authentication;
using SmtpServer.ComponentModel;
using SmtpServer.Mail;
using SmtpServer.Protocol;
using SmtpServer.Storage;

using SmtpHost = SmtpServer.SmtpServer;

namespace EnvCite.Platform.Tests;

/// <summary>伺服器收到的一封信：信封的寄件者與收件者，加上解析後的信。</summary>
internal sealed record ReceivedMail(string From, IReadOnlyList<string> Recipients, MimeMessage Message);

/// <summary>測試用 SMTP 伺服器的行為。預設是校內中繼的樣子：不公告 STARTTLS 與 AUTH，收下所有信。</summary>
internal sealed class TestSmtpServerOptions
{
    /// <summary>設了就公告 AUTH（PLAIN、LOGIN），而且只接受這組帳密；沒設就不公告。</summary>
    public (string User, string Password)? Credentials { get; init; }

    /// <summary>沒登入就拒絕信封（530 5.7.0），像要求登入才肯代寄的中繼。</summary>
    public bool RequireAuthentication { get; init; }

    /// <summary>公告帳密驗證時，不加密的連線也允許。MailKit 在不加密的連線上能不能驗證，由測試實測。</summary>
    public bool AllowAuthenticationWithoutEncryption { get; init; } = true;

    /// <summary>拒絕所有收件者（550）。</summary>
    public bool RefuseRecipients { get; init; }

    /// <summary>拒絕信封寄件者，並回這段文字（例如 Exchange 的「5.7.60 寄件者不是認證的帳號」）；null 表示不拒絕。</summary>
    public string? RefuseSenderWith { get; init; }

    /// <summary>設了就公告 STARTTLS；<see cref="ImplicitTls"/> 時連上就是 TLS。</summary>
    public X509Certificate2? Certificate { get; init; }

    public bool ImplicitTls { get; init; }
}

/// <summary>
/// 行程內的 SMTP 伺服器（<c>SmtpServer</c> NuGet 套件），測試的寄信對真的 TCP 連線進行，不寄任何真實郵件、不連外。
/// 只記錄收到的東西：指令（連線層）、驗證嘗試、完整的信。不自己實作 SMTP。
/// </summary>
internal sealed class TestSmtpServer : IAsyncDisposable
{
    private readonly SmtpHost _server;
    private readonly CancellationTokenSource _stop = new();
    private readonly Task _running;
    private readonly List<ReceivedMail> _received = [];
    private readonly List<string> _commands = [];
    private readonly List<(string User, bool Accepted)> _authentications = [];
    private int _sessions;

    private TestSmtpServer(TestSmtpServerOptions options, int port)
    {
        Port = port;
        var endpoint = new EndpointDefinitionBuilder()
            .Endpoint(new IPEndPoint(IPAddress.Loopback, port))
            .IsSecure(options.ImplicitTls)
            .AllowUnsecureAuthentication(options.AllowAuthenticationWithoutEncryption)
            .AuthenticationRequired(options.RequireAuthentication)
            .SessionTimeout(TimeSpan.FromSeconds(15));
        if (options.Certificate is not null)
        {
            endpoint.Certificate(options.Certificate);
        }

        var services = new SmtpServer.ComponentModel.ServiceProvider();
        services.Add((IMessageStore)new Store(this));
        services.Add((IMailboxFilter)new Filter(options));
        if (options.Credentials is { } credentials)
        {
            services.Add((IUserAuthenticator)new Authenticator(this, credentials));
        }

        _server = new SmtpHost(new SmtpServerOptionsBuilder().ServerName("relay.test").Endpoint(endpoint.Build()).Build(), services);
        _server.SessionCreated += (_, e) =>
        {
            Interlocked.Increment(ref _sessions);
            e.Context.CommandExecuting += (_, command) => Record(command.Command);
        };
        _running = _server.StartAsync(_stop.Token);
    }

    public int Port { get; }

    /// <summary>真正的連線數（不含啟動時確認伺服器已在聽的那一次）。</summary>
    public int Connections => Volatile.Read(ref _sessions) - 1;

    public IReadOnlyList<ReceivedMail> Received => Snapshot(_received);

    /// <summary>連線層收到的指令，一個指令一項，例如 <c>EHLO relay.test</c>、<c>AUTH PLAIN</c>、<c>STARTTLS</c>。</summary>
    public IReadOnlyList<string> Commands => Snapshot(_commands);

    /// <summary>伺服器驗證過的帳號與結果；密碼不記。</summary>
    public IReadOnlyList<(string User, bool Accepted)> Authentications => Snapshot(_authentications);

    public static async Task<TestSmtpServer> StartAsync(TestSmtpServerOptions? options = null)
    {
        options ??= new TestSmtpServerOptions();
        for (var attempt = 1; ; attempt++)
        {
            var server = new TestSmtpServer(options, FreePort());
            try
            {
                await server.WaitUntilListeningAsync();
                return server;
            }
            catch (Exception) when (attempt < 3)
            {
                // 挑到的埠剛好被別人佔走：換一個再試。
                await server.DisposeAsync();
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _stop.CancelAsync();
        try
        {
            _server.Shutdown();
            await _running.WaitAsync(TimeSpan.FromSeconds(5));
        }
        catch (Exception ex) when (ex is OperationCanceledException or TimeoutException or ObjectDisposedException or SocketException)
        {
            // 關閉時的預期結果。
        }

        _stop.Dispose();
    }

    /// <summary>自簽憑證（CN=localhost、SAN 127.0.0.1）：寄信端不信任它，用來證明憑證確實被驗證。</summary>
    public static X509Certificate2 SelfSignedCertificate()
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest("CN=localhost", key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        var names = new SubjectAlternativeNameBuilder();
        names.AddIpAddress(IPAddress.Loopback);
        names.AddDnsName("localhost");
        request.CertificateExtensions.Add(names.Build());
        using var certificate = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(1));
        return X509CertificateLoader.LoadPkcs12(certificate.Export(X509ContentType.Pfx), password: null);
    }

    private static int FreePort()
    {
        using var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        return ((IPEndPoint)probe.LocalEndpoint).Port;
    }

    /// <summary>連一次再斷開，確認伺服器已經在聽；這一次連線會被算進 <c>_sessions</c>，所以 <see cref="Connections"/> 扣掉它。</summary>
    private async Task WaitUntilListeningAsync()
    {
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (true)
        {
            if (_running.IsFaulted)
            {
                await _running;
            }

            try
            {
                using var probe = new TcpClient();
                await probe.ConnectAsync(IPAddress.Loopback, Port);
                break;
            }
            catch (SocketException) when (DateTime.UtcNow < deadline)
            {
                await Task.Delay(20);
            }
        }

        while (Volatile.Read(ref _sessions) < 1 && DateTime.UtcNow < deadline)
        {
            await Task.Delay(10);
        }
    }

    private void Record(SmtpCommand command)
    {
        var line = command switch
        {
            EhloCommand ehlo => $"EHLO {ehlo.DomainOrAddress}",
            AuthCommand auth => $"AUTH {auth.Method.ToString().ToUpperInvariant()}",
            _ => command.Name,
        };
        lock (_commands)
        {
            _commands.Add(line);
        }
    }

    private static List<T> Snapshot<T>(List<T> source)
    {
        lock (source)
        {
            return [.. source];
        }
    }

    private sealed class Store(TestSmtpServer owner) : MessageStore
    {
        public override async Task<SmtpResponse> SaveAsync(ISessionContext context, IMessageTransaction transaction, ReadOnlySequence<byte> buffer, CancellationToken cancellationToken)
        {
            await using var stream = new MemoryStream();
            var position = buffer.GetPosition(0);
            while (buffer.TryGet(ref position, out var memory))
            {
                await stream.WriteAsync(memory, cancellationToken);
            }

            stream.Position = 0;
            var message = await MimeMessage.LoadAsync(stream, cancellationToken);
            lock (owner._received)
            {
                owner._received.Add(new ReceivedMail(Address(transaction.From), [.. transaction.To.Select(Address)], message));
            }

            return SmtpResponse.Ok;
        }

        private static string Address(IMailbox mailbox) => $"{mailbox.User}@{mailbox.Host}";
    }

    private sealed class Filter(TestSmtpServerOptions options) : MailboxFilter
    {
        public override Task<bool> CanAcceptFromAsync(ISessionContext context, IMailbox from, int size, CancellationToken cancellationToken) =>
            options.RefuseSenderWith is { } refusal
                ? throw new SmtpResponseException(new SmtpResponse(SmtpReplyCode.MailboxUnavailable, refusal))
                : Task.FromResult(true);

        public override Task<bool> CanDeliverToAsync(ISessionContext context, IMailbox to, IMailbox from, CancellationToken cancellationToken) =>
            Task.FromResult(!options.RefuseRecipients);
    }

    private sealed class Authenticator(TestSmtpServer owner, (string User, string Password) credentials) : UserAuthenticator
    {
        public override Task<bool> AuthenticateAsync(ISessionContext context, string user, string password, CancellationToken cancellationToken)
        {
            var accepted = user == credentials.User && password == credentials.Password;
            lock (owner._authentications)
            {
                owner._authentications.Add((user, accepted));
            }

            return Task.FromResult(accepted);
        }
    }
}