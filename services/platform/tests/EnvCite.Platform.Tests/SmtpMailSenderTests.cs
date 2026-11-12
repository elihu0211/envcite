using System.Net;
using System.Net.Sockets;
using System.Text.Json;

using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 對真的 TCP 連線測 SMTP 寄信：行程內的 SMTP 伺服器（<c>SmtpServer</c> 套件，<see cref="TestSmtpServer"/>）預設<b>不宣告</b> STARTTLS 與 AUTH——
/// 校內中繼是不加密、免驗證的，寄信端不能因為它們不存在就失敗，也不能自己試著要求它們。
/// 需要帳密、加密或拒絕的情境，由伺服器的選項切換；不寄任何真實郵件。
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

    private static MailContent Content(string text) => new(text, $"<p>{text}</p>");

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
    public async Task ItDeliversAMultipartAlternativeOverAnUnencryptedUnauthenticatedConnection()
    {
        await using var relay = await TestSmtpServer.StartAsync();
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("連結：https://platform.test/x?code=abc"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        var mail = Assert.Single(relay.Received);
        Assert.Equal("envcite@relay.test", mail.From);
        Assert.Equal(["someone@recipient.test"], mail.Recipients);
        Assert.Equal("主旨", mail.Message.Subject);
        Assert.Contains("連結：https://platform.test/x?code=abc", mail.Message.TextBody, StringComparison.Ordinal);
        Assert.Equal("auto-generated", mail.Message.Headers["Auto-Submitted"]); // RFC 3834

        // multipart/alternative：純文字在前、HTML 在後（RFC 2046：最後一個最偏好），各自的字元集完整還原。
        var alternative = Assert.IsType<MimeKit.MultipartAlternative>(mail.Message.Body);
        Assert.Equal(["text/plain", "text/html"], alternative.Select(part => part.ContentType.MimeType));
        Assert.Equal("<p>連結：https://platform.test/x?code=abc</p>", mail.Message.HtmlBody);

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
        await using var relay = await TestSmtpServer.StartAsync();
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
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        var from = Assert.Single(Assert.Single(relay.Received).Message.From.Mailboxes);
        Assert.Equal(expected, from.Name);
        Assert.Equal("envcite@relay.test", from.Address);
    }

    [Fact]
    public async Task WhenMailIsNotEnabledItNeverConnects()
    {
        await using var relay = await TestSmtpServer.StartAsync();
        using var provider = Sender(Settings(relay.Port, enabled: false));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

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
            .SendAsync("someone@recipient.test", "主旨-不能進日誌", Content("密碼-不能進日誌"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        var dump = string.Join('\n', logs.Messages);
        Assert.Contains("Mail delivery failed", dump, StringComparison.Ordinal);
        Assert.DoesNotContain("someone@recipient.test", dump, StringComparison.Ordinal);
        Assert.DoesNotContain("不能進日誌", dump, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ARelayThatRefusesTheRecipientIsAFailure()
    {
        await using var relay = await TestSmtpServer.StartAsync(new() { RefuseRecipients = true });
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        Assert.Empty(relay.Received);
    }

    [Fact]
    public async Task ABadRecipientAddressIsAFailureAndNeverReachesTheRelay()
    {
        await using var relay = await TestSmtpServer.StartAsync();
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("not an address", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        Assert.Equal(0, relay.Connections);
    }

    // 呼叫端自己取消要照常傳出去，不能被當成寄信失敗吞掉。
    [Fact]
    public async Task TheCallersOwnCancellationIsNotSwallowed()
    {
        await using var relay = await TestSmtpServer.StartAsync();
        using var provider = Sender(Settings(relay.Port));
        using var cancelled = new CancellationTokenSource();
        await cancelled.CancelAsync();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), cancelled.Token));
    }

    // ---- 帳密驗證 ----

    private const string Account = "relay-account@relay.test";
    private const string Secret = "s3cret-不能進日誌";

    private static Dictionary<string, string?> WithAccount(Dictionary<string, string?> settings, string user = Account, string password = Secret)
    {
        settings["Platform:Mail:Username"] = user;
        settings["Platform:Mail:Password"] = password;
        return settings;
    }

    // 不加密的連線也能驗證：校內中繼就是不加密、要登入（Security 沒設＝None）。MailKit 在不加密連線上是否肯送出帳密，以此實測。
    [Fact]
    public async Task WithCredentialsItAuthenticatesAndDeliversEvenOverAnUnencryptedConnection()
    {
        await using var relay = await TestSmtpServer.StartAsync(new() { Credentials = (Account, Secret), RequireAuthentication = true });
        using var provider = Sender(WithAccount(Settings(relay.Port)));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        Assert.Equal([(Account, true)], relay.Authentications);
        Assert.Single(relay.Commands, c => c.StartsWith("AUTH", StringComparison.Ordinal));
        Assert.DoesNotContain("STARTTLS", relay.Commands);
        Assert.Single(relay.Received);
    }

    // Secret 檔案常多一個換行：帳號前後的空白不算帳號的一部分（密碼則原樣送出）。
    [Fact]
    public async Task WhitespaceAroundTheUsernameIsNotPartOfTheAccount()
    {
        await using var relay = await TestSmtpServer.StartAsync(new() { Credentials = (Account, Secret), RequireAuthentication = true });
        using var provider = Sender(WithAccount(Settings(relay.Port), user: $" {Account}\n"));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        Assert.Equal([(Account, true)], relay.Authentications);
    }

    // 沒設帳密就不送 AUTH，即使伺服器公告了它（現行部署不能因為這次改動開始登入）。
    [Fact]
    public async Task WithoutCredentialsItNeverSendsAuthEvenWhenTheRelayAdvertisesIt()
    {
        await using var relay = await TestSmtpServer.StartAsync(new() { Credentials = (Account, Secret) });
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        Assert.Empty(relay.Authentications);
        Assert.DoesNotContain(relay.Commands, c => c.StartsWith("AUTH", StringComparison.OrdinalIgnoreCase));
    }

    // 帳密錯誤是寄信失敗，不是例外；日誌認得 535，但沒有密碼、帳號與收件者。
    [Fact]
    public async Task AWrongPasswordIsAFailureAndNeitherTheLogsNorTheOutcomeHoldTheCredentials()
    {
        var logs = new CapturingLoggerProvider();
        await using var relay = await TestSmtpServer.StartAsync(new() { Credentials = (Account, Secret), RequireAuthentication = true });
        using var provider = Sender(WithAccount(Settings(relay.Port), password: "wrong-密碼-也不能進日誌"), logs);

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        // MailKit 對伺服器公告的每一種機制各試一次（這裡是 PLAIN 與 LOGIN），所以一次寄信可能記多次失敗的登入。
        Assert.NotEmpty(relay.Authentications);
        Assert.All(relay.Authentications, attempt => Assert.Equal((Account, false), attempt));
        Assert.Empty(relay.Received);
        var dump = string.Join('\n', logs.Messages);
        Assert.Contains("Mail delivery failed", dump, StringComparison.Ordinal);
        Assert.Contains("535", dump, StringComparison.Ordinal);
        foreach (var secret in new[] { Secret, "wrong-密碼-也不能進日誌", "不能進日誌", Account, "relay-account", "someone@recipient.test" })
        {
            Assert.DoesNotContain(secret, dump, StringComparison.Ordinal);
        }
    }

    // Exchange 的 550 5.7.60：寄件者必須等於認證的帳號。日誌認得增強狀態碼，但不帶位址。
    [Fact]
    public async Task ASenderThatIsNotTheAuthenticatedAccountIsAFailureAndTheLogNamesTheEnhancedStatus()
    {
        var logs = new CapturingLoggerProvider();
        await using var relay = await TestSmtpServer.StartAsync(new()
        {
            Credentials = (Account, Secret),
            RequireAuthentication = true,
            RefuseSenderWith = "5.7.60 Client does not have permissions to send as this sender",
        });
        using var provider = Sender(WithAccount(Settings(relay.Port)), logs);

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        Assert.Empty(relay.Received);
        var dump = string.Join('\n', logs.Messages);
        Assert.Contains("550 5.7.60", dump, StringComparison.Ordinal);
        Assert.DoesNotContain("permissions", dump, StringComparison.Ordinal);
        Assert.DoesNotContain("envcite@relay.test", dump, StringComparison.Ordinal);
        Assert.DoesNotContain(Secret, dump, StringComparison.Ordinal);
    }

    // 要求登入的中繼，沒給帳密就被擋下：失敗而不是例外。
    [Fact]
    public async Task ARelayThatRequiresLoginRejectsAMailWithoutCredentials()
    {
        await using var relay = await TestSmtpServer.StartAsync(new() { Credentials = (Account, Secret), RequireAuthentication = true });
        using var provider = Sender(Settings(relay.Port));

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        Assert.Empty(relay.Received);
    }

    // ---- 寄件者與回覆位址 ----

    // 寄件者必須等於認證的帳號：From 沒設、帳號是 email 時，信封與標頭的寄件者都用帳號。
    [Fact]
    public async Task TheSenderDefaultsToTheUsernameWhenFromIsNotSet()
    {
        await using var relay = await TestSmtpServer.StartAsync(new() { Credentials = (Account, Secret), RequireAuthentication = true });
        var settings = WithAccount(Settings(relay.Port));
        settings.Remove("Platform:Mail:From");
        using var provider = Sender(settings);

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Sent, result);
        var mail = Assert.Single(relay.Received);
        Assert.Equal(Account, mail.From);
        Assert.Equal(Account, Assert.Single(mail.Message.From.Mailboxes).Address);
        Assert.StartsWith("EHLO relay.test", relay.Commands[0], StringComparison.Ordinal); // EHLO 名稱仍是寄件者的網域
    }

    [Fact]
    public async Task AnExplicitFromWinsOverTheUsername()
    {
        await using var relay = await TestSmtpServer.StartAsync(new() { Credentials = (Account, Secret), RequireAuthentication = true });
        using var provider = Sender(WithAccount(Settings(relay.Port)));

        await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal("envcite@relay.test", Assert.Single(relay.Received).From);
    }

    [Fact]
    public async Task ReplyToIsAddedWhenSetAndAbsentOtherwise()
    {
        await using var relay = await TestSmtpServer.StartAsync();
        var withReplyTo = Settings(relay.Port);
        withReplyTo["Platform:Mail:ReplyTo"] = "support@relay.test";

        using (var provider = Sender(withReplyTo))
        {
            await provider.GetRequiredService<IMailSender>()
                .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);
        }

        using (var provider = Sender(Settings(relay.Port)))
        {
            await provider.GetRequiredService<IMailSender>()
                .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);
        }

        var (first, second) = (relay.Received[0].Message, relay.Received[1].Message);
        Assert.Equal("support@relay.test", Assert.Single(first.ReplyTo.Mailboxes).Address);
        Assert.Equal("envcite@relay.test", Assert.Single(first.From.Mailboxes).Address); // 寄件者不受影響
        Assert.Empty(second.ReplyTo);
        Assert.False(second.Headers.Contains(MimeKit.HeaderId.ReplyTo));
    }

    // ---- 加密模式 ----

    // 伺服器沒有公告 STARTTLS（校內中繼）：None 與「有就升級」照常寄；要求升級或連上就加密的模式必須失敗，不能悄悄退回明文。
    [Theory]
    [InlineData(MailSecurity.None, MailDelivery.Sent)]
    [InlineData(MailSecurity.StartTlsWhenAvailable, MailDelivery.Sent)]
    [InlineData(MailSecurity.Auto, MailDelivery.Sent)]
    [InlineData(MailSecurity.StartTls, MailDelivery.Failed)]
    [InlineData(MailSecurity.SslOnConnect, MailDelivery.Failed)]
    public async Task TheSecurityModeIsHonouredAgainstARelayWithoutTls(MailSecurity security, MailDelivery expected)
    {
        await using var relay = await TestSmtpServer.StartAsync();
        var settings = Settings(relay.Port);
        settings["Platform:Mail:Security"] = security.ToString();
        using var provider = Sender(settings);

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(expected, result);
        Assert.Equal(expected == MailDelivery.Sent ? 1 : 0, relay.Received.Count);
    }

    // 憑證一律照預設驗證：中繼用不受信任的自簽憑證，STARTTLS 與連上就加密都必須失敗，信與帳密都不能送出去。
    [Theory]
    [InlineData(MailSecurity.StartTls, false)]
    [InlineData(MailSecurity.StartTlsWhenAvailable, false)]
    [InlineData(MailSecurity.SslOnConnect, true)]
    public async Task AnUntrustedCertificateIsNeverAcceptedAndNothingIsSent(MailSecurity security, bool implicitTls)
    {
        using var certificate = TestSmtpServer.SelfSignedCertificate();
        var logs = new CapturingLoggerProvider();
        await using var relay = await TestSmtpServer.StartAsync(new()
        {
            Certificate = certificate,
            ImplicitTls = implicitTls,
            Credentials = (Account, Secret),
            RequireAuthentication = true,
        });
        var settings = WithAccount(Settings(relay.Port));
        settings["Platform:Mail:Security"] = security.ToString();
        using var provider = Sender(settings, logs);

        var result = await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal(MailDelivery.Failed, result);
        Assert.Empty(relay.Received);
        Assert.Empty(relay.Authentications);
        var dump = string.Join('\n', logs.Messages);
        Assert.Contains("Mail delivery failed", dump, StringComparison.Ordinal);
        Assert.DoesNotContain(Secret, dump, StringComparison.Ordinal);
    }

    [Fact]
    public async Task StartTlsAsksTheRelayToUpgradeBeforeAnythingElse()
    {
        using var certificate = TestSmtpServer.SelfSignedCertificate();
        await using var relay = await TestSmtpServer.StartAsync(new() { Certificate = certificate });
        var settings = Settings(relay.Port);
        settings["Platform:Mail:Security"] = nameof(MailSecurity.StartTls);
        using var provider = Sender(settings);

        await provider.GetRequiredService<IMailSender>()
            .SendAsync("someone@recipient.test", "主旨", Content("內文"), TestContext.Current.CancellationToken);

        Assert.Equal("STARTTLS", relay.Commands[1]); // EHLO、STARTTLS，之後才可能有別的指令
    }

    // ---- 設定檢查與機密 ----

    private static IOptions<MailOptions> Options(Dictionary<string, string?> settings) =>
        Sender(settings).GetRequiredService<IOptions<MailOptions>>();

    [Theory]
    [InlineData("Platform:Mail:Username", "only-user@relay.test", "Platform:Mail:Password is required when Platform:Mail:Username is set")]
    [InlineData("Platform:Mail:Password", "only-the-password-不能進訊息", "Platform:Mail:Username is required when Platform:Mail:Password is set")]
    [InlineData("Platform:Mail:Security", "42", "Platform:Mail:Security must be one of")]
    [InlineData("Platform:Mail:ReplyTo", "not-a-mail", "Platform:Mail:ReplyTo must be a valid mail address")]
    public void InvalidCredentialSettingsStopTheStartupAndNameTheKeyWithoutTheValue(string key, string value, string expected)
    {
        var settings = Settings(587);
        settings[key] = value;

        var failure = Assert.Throws<OptionsValidationException>(() => Options(settings).Value);

        Assert.Contains(expected, failure.Message, StringComparison.Ordinal);
        Assert.DoesNotContain(value, failure.Message.Replace(key, string.Empty, StringComparison.Ordinal), StringComparison.Ordinal);
    }

    // 列舉鍵寫錯字：綁定就失敗，訊息指名鍵。
    [Fact]
    public void ASecurityValueThatIsNotAModeFailsAndNamesTheKey()
    {
        var settings = Settings(587);
        settings["Platform:Mail:Security"] = "Bogus";

        var failure = Assert.ThrowsAny<Exception>(() => Options(settings).Value);

        Assert.Contains("Platform:Mail:Security", string.Join(' ', Messages(failure)), StringComparison.Ordinal);
    }

    // 兩者都沒設是現行部署：不驗證、不加密，和以前一樣。兩者都設則通過。
    [Fact]
    public void NeitherOrBothOfUsernameAndPasswordAreValidAndSecurityDefaultsToNone()
    {
        var none = Options(Settings(25)).Value;
        Assert.False(none.HasCredentials);
        Assert.Equal(MailSecurity.None, none.Security);
        Assert.Null(none.ReplyTo);

        var both = Options(WithAccount(Settings(25))).Value;
        Assert.True(both.HasCredentials);
    }

    // From 沒設、帳號是 email：選項層就把 From 補成帳號，驗證器與所有使用端看到同一個值。
    [Fact]
    public void FromDefaultsToTheUsernameOnlyWhenItIsAMailAddress()
    {
        var settings = WithAccount(Settings(25));
        settings.Remove("Platform:Mail:From");
        Assert.Equal(Account, Options(settings).Value.From);

        // 帳號不是 email（例如 DOMAIN\user）：不能當寄件者，From 仍是必填。
        var plain = WithAccount(Settings(25), user: "service-account");
        plain.Remove("Platform:Mail:From");
        var failure = Assert.Throws<OptionsValidationException>(() => Options(plain).Value);
        Assert.Contains("Platform:Mail:From must be a valid mail address", failure.Message, StringComparison.Ordinal);
    }

    // 密碼不出現在任何字串化裡：選項的 ToString、JSON 序列化。
    [Fact]
    public void ThePasswordIsInNoStringFormOfTheOptions()
    {
        var options = Options(WithAccount(Settings(25))).Value;

        var forms = new[] { options.ToString(), JsonSerializer.Serialize(options), $"{options}", string.Format(System.Globalization.CultureInfo.InvariantCulture, "{0}", options) };

        foreach (var form in forms)
        {
            Assert.DoesNotContain(Secret, form, StringComparison.Ordinal);
            Assert.DoesNotContain("不能進日誌", form, StringComparison.Ordinal);
            Assert.DoesNotContain("relay-account", form, StringComparison.Ordinal);
        }
    }

    // ---- 啟動時的提醒 ----

    private static async Task<string> StartupLogAsync(Dictionary<string, string?> settings)
    {
        var logs = new CapturingLoggerProvider();
        using var provider = Sender(settings, logs);
        foreach (var service in provider.GetServices<IHostedService>())
        {
            await service.StartAsync(TestContext.Current.CancellationToken);
        }

        return string.Join('\n', logs.Messages);
    }

    [Theory]
    [InlineData(MailSecurity.None, true)]
    [InlineData(MailSecurity.StartTlsWhenAvailable, true)]
    [InlineData(MailSecurity.StartTls, false)]
    [InlineData(MailSecurity.SslOnConnect, false)]
    public async Task CredentialsOnAConnectionThatMayBeClearTextWarnAtStartup(MailSecurity security, bool warns)
    {
        var settings = WithAccount(Settings(25));
        settings["Platform:Mail:Security"] = security.ToString();

        var log = await StartupLogAsync(settings);

        Assert.Equal(warns, log.Contains("clear text", StringComparison.Ordinal));
        Assert.DoesNotContain(Secret, log, StringComparison.Ordinal);
        Assert.DoesNotContain(Account, log, StringComparison.Ordinal);
    }

    [Fact]
    public async Task NoCredentialsOrNoMailMeansNoWarning()
    {
        Assert.Empty(await StartupLogAsync(Settings(25))); // 現行部署：不加密、免驗證，不吵
        Assert.Empty(await StartupLogAsync(WithAccount(Settings(25, enabled: false))));
    }

    [Theory]
    [InlineData("other@relay.test", true)] // 與帳號不同：Exchange 會拒絕
    [InlineData("RELAY-ACCOUNT@relay.test", false)] // 只差大小寫：同一個帳號
    public async Task AFromThatDiffersFromTheUsernameWarnsAtStartup(string from, bool warns)
    {
        var settings = WithAccount(Settings(25));
        settings["Platform:Mail:From"] = from;
        settings["Platform:Mail:Security"] = nameof(MailSecurity.StartTls);

        var log = await StartupLogAsync(settings);

        Assert.Equal(warns, log.Contains("differs from Platform:Mail:Username", StringComparison.Ordinal));
        Assert.DoesNotContain(from, log, StringComparison.OrdinalIgnoreCase);
    }

    private static IEnumerable<string> Messages(Exception exception)
    {
        for (var current = exception; current is not null; current = current.InnerException!)
        {
            yield return current.Message;
        }
    }

    private static int UnusedPort()
    {
        using var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        return ((IPEndPoint)probe.LocalEndpoint).Port;
    }
}