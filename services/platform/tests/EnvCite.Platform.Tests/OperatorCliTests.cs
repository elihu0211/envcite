using EnvCite.Platform.Api;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 營運者的命令列入口：正式環境沒有種子帳號，第一位營運者與救援都走這裡。每個測試用自己的全新資料庫。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class OperatorCliTests(PlatformFactory factory)
{
    private async Task<WebApplicationFactory<Program>> HostAsync()
    {
        var database = await factory.CreateDatabaseAsync();
        return factory.WithWebHostBuilder(builder =>
        {
            builder.UseEnvironment("Production");
            builder.UseSetting("ConnectionStrings:Platform", database);
        });
    }

    private static async Task<(int Code, string Output, string Error)> RunAsync(IServiceProvider services, params string[] args)
    {
        var output = new StringWriter();
        var error = new StringWriter();
        var code = await OperatorCli.RunAsync(services, args, output, error, CancellationToken.None);
        return (code, output.ToString(), error.ToString());
    }

    private static string PasswordIn(string output) =>
        output.Split('\n').Single(l => l.StartsWith("一次性密碼", StringComparison.Ordinal)).Split('：')[^1].Trim();

    [Fact]
    public async Task CreateMakesTheOperatorAndPrintsAOneTimePasswordThatSignsIn()
    {
        await using var host = await HostAsync();

        var (code, output, error) = await RunAsync(host.Services, "operator", "create", "first@example.com", "第一位");

        Assert.Equal(0, code);
        Assert.Empty(error);
        var created = Assert.Single((await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!);
        Assert.Equal(("first@example.com", "第一位", true, false), (created.Email, created.DisplayName, created.MustChangePassword, created.TwoFactorEnabled));
        var password = PasswordIn(output);
        Assert.True(password.Length >= 16);
        Assert.Contains("/ui/login", output, StringComparison.Ordinal);
        using var browser = new PortalBrowser(host);
        Assert.Equal(OperatorTestSupport.UiPassword, (await browser.SubmitAsync(OperatorTestSupport.UiLogin, Credentials("first@example.com", password))).Location);
    }

    // 稽核誠實地記「系統」做的；一次性密碼只在標準輸出，不進日誌，也不進稽核。
    [Fact]
    public async Task CreateIsAuditedAsTheSystemAndTheOneTimePasswordReachesNeitherLogsNorAudit()
    {
        var logs = new CapturingLoggerProvider();
        await using var host = (await HostAsync()).WithWebHostBuilder(b => b.ConfigureLogging(l => l.AddProvider(logs)));

        var (code, output, _) = await RunAsync(host.Services, "operator", "create", "first@example.com");

        Assert.Equal(0, code);
        var audit = await host.Services.GetRequiredService<IAuditQuery>().QueryAsync(new AuditFilter(Action: AuditActions.OperatorCreated), CancellationToken.None);
        Assert.Equal(AuditActors.System, Assert.Single(audit.Items).ActorType);
        var dump = System.Text.Json.JsonSerializer.Serialize(audit) + string.Join('\n', logs.Messages);
        Assert.DoesNotContain(PasswordIn(output), dump, StringComparison.Ordinal);
    }

    [Fact]
    public async Task CreatingAnExistingEmailFailsAndPrintsNoPassword()
    {
        await using var host = await HostAsync();
        await RunAsync(host.Services, "operator", "create", "first@example.com");

        var (code, output, error) = await RunAsync(host.Services, "operator", "create", "first@example.com");

        Assert.Equal(1, code);
        Assert.Empty(output);
        Assert.NotEmpty(error);
        Assert.Single((await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!);
    }

    [Fact]
    public async Task ResetPasswordPrintsANewOneTimePasswordThatSignsIn()
    {
        await using var host = await HostAsync();
        var first = PasswordIn((await RunAsync(host.Services, "operator", "create", "first@example.com")).Output);

        var (code, output, error) = await RunAsync(host.Services, "operator", "reset-password", "FIRST@example.com");

        Assert.Equal(0, code);
        Assert.Empty(error);
        var reset = PasswordIn(output);
        Assert.NotEqual(first, reset);
        using var browser = new PortalBrowser(host);
        Assert.Equal(OperatorTestSupport.UiPassword, (await browser.SubmitAsync(OperatorTestSupport.UiLogin, Credentials("first@example.com", reset))).Location);
    }

    [Fact]
    public async Task ResetTwoFactorSucceedsForAnExistingOperator()
    {
        await using var host = await HostAsync();
        await RunAsync(host.Services, "operator", "create", "first@example.com");

        var (code, output, error) = await RunAsync(host.Services, "operator", "reset-two-factor", "first@example.com");

        Assert.Equal(0, code);
        Assert.Empty(error);
        Assert.Contains("first@example.com", output, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("reset-password")]
    [InlineData("reset-two-factor")]
    public async Task ResettingAnUnknownOperatorFailsWithoutPrintingAnything(string command)
    {
        await using var host = await HostAsync();

        var (code, output, error) = await RunAsync(host.Services, "operator", command, "nobody@example.com");

        Assert.Equal(1, code);
        Assert.Empty(output);
        Assert.Contains("nobody@example.com", error, StringComparison.Ordinal);
    }

    // 啟用寄信後，create 是邀請：對方自己設定密碼，所以命令列不產生也不印出任何密碼，連結也不印。
    [Fact]
    public async Task WithMailEnabledCreateInvitesAndPrintsNoPasswordOrLink()
    {
        await using var host = await HostAsync();
        var email = $"first-{Guid.NewGuid():N}@example.test";
        await factory.WithMail(async () =>
        {
            var (code, output, error) = await RunAsync(host.Services, "operator", "create", email, "第一位");

            Assert.Equal(0, code);
            Assert.Empty(error);
            Assert.Contains("邀請信已寄到", output, StringComparison.Ordinal);
            Assert.DoesNotContain("一次性密碼", output, StringComparison.Ordinal);
            Assert.DoesNotContain("code=", output, StringComparison.Ordinal);
            var created = Assert.Single((await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!);
            Assert.True(created.Invited);
            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == email);
            Assert.Contains("https://platform.test/ui/activate?userId=", mail.Body, StringComparison.Ordinal);

            // 重寄（信箱不分大小寫）：先前的連結失效。
            var (again, _, _) = await RunAsync(host.Services, "operator", "resend-invite", email.ToUpperInvariant());
            Assert.Equal(0, again);
            Assert.Equal(2, factory.Mail.Sent.Count(m => m.Recipient == email));
        });
    }

    // 帳號已經建立、信沒寄出去：結束碼 1 讓腳本察覺，並說明退路（重寄，或 reset-password 取得一次性密碼）。
    [Fact]
    public async Task AFailedInvitationMailExitsNonZeroAndPointsToTheFallbacks()
    {
        await using var host = await HostAsync();
        var email = $"first-{Guid.NewGuid():N}@example.test";
        await factory.WithMail(async () =>
        {
            var (code, _, error) = await RunAsync(host.Services, "operator", "create", email);

            Assert.Equal(1, code);
            Assert.Contains("resend-invite", error, StringComparison.Ordinal);
            Assert.Contains("reset-password", error, StringComparison.Ordinal);
            Assert.Single((await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!);
        }, MailDelivery.Failed);
    }

    // 寄信冒煙測試：寄一封沒有連結、沒有密碼的信，並照實說出結果（要在服務自己的環境執行才有意義）。
    [Fact]
    public async Task MailTestSendsAPlainNoteAndReportsTheRelayAcceptedIt()
    {
        await using var host = await HostAsync();
        var to = $"probe-{Guid.NewGuid():N}@example.test";
        await factory.WithMail(async () =>
        {
            var (code, output, error) = await RunAsync(host.Services, "mail", "test", to);

            Assert.Equal(0, code);
            Assert.Empty(error);
            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == to);
            Assert.DoesNotContain("http", mail.Body, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("中繼已接受", output, StringComparison.Ordinal);
            Assert.Contains("platform.test", output, StringComparison.Ordinal); // 印出實際使用的連結來源
        });
    }

    // 測試信也是 HTML 加純文字，套同一個版型；沒有連結、沒有按鈕、沒有任何網址。
    [Fact]
    public async Task MailTestSendsTheSameNoteAsHtmlWithoutAnyAddress()
    {
        await using var host = await HostAsync();
        var to = $"probe-{Guid.NewGuid():N}@example.test";
        await factory.WithMail(async () =>
        {
            var (code, _, _) = await RunAsync(host.Services, "mail", "test", to);

            Assert.Equal(0, code);
            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == to);
            Assert.StartsWith("<!DOCTYPE html>", mail.Html, StringComparison.Ordinal);
            Assert.Contains("這是一封測試信，用來確認「環境知識」的寄信設定與中繼可用。", mail.Html, StringComparison.Ordinal);
            Assert.DoesNotContain("http", mail.Html, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("<a ", mail.Html, StringComparison.Ordinal);
            Assert.DoesNotContain("<img", mail.Html, StringComparison.OrdinalIgnoreCase);
            await Task.CompletedTask;
        });
    }

    [Fact]
    public async Task MailTestExplainsWhyItFailedAndNeverClaimsSuccess()
    {
        await using var host = await HostAsync();
        var to = $"probe-{Guid.NewGuid():N}@example.test";

        // 沒啟用：不寄，說明要開哪個設定。
        var (disabledCode, _, disabledError) = await RunAsync(host.Services, "mail", "test", to);
        Assert.Equal(1, disabledCode);
        Assert.Contains("Platform:Mail:Enabled", disabledError, StringComparison.Ordinal);
        Assert.DoesNotContain(factory.Mail.Sent, m => m.Recipient == to);

        // 中繼拒絕或連不上：結束碼 1，不說成功，指出常見原因與去哪看細節。
        await factory.WithMail(async () =>
        {
            var (code, output, error) = await RunAsync(host.Services, "mail", "test", to);

            Assert.Equal(1, code);
            Assert.DoesNotContain("已接受", output, StringComparison.Ordinal);
            Assert.Contains("來源位址", error, StringComparison.Ordinal);
            Assert.Contains("Mail delivery failed", error, StringComparison.Ordinal);
            await Task.CompletedTask;
        }, MailDelivery.Failed);
    }

    [Fact]
    public async Task MailTestRejectsABadAddressWithoutSending()
    {
        await using var host = await HostAsync();
        await factory.WithMail(async () =>
        {
            var before = factory.Mail.Sent.Count;

            var (code, _, error) = await RunAsync(host.Services, "mail", "test", "not-an-address");

            Assert.Equal(1, code);
            Assert.Contains("不合法", error, StringComparison.Ordinal);
            Assert.Equal(before, factory.Mail.Sent.Count);
        });
    }

    [Theory]
    [InlineData("operator")]
    [InlineData("operator", "create")]
    [InlineData("operator", "unknown", "a@example.com")]
    [InlineData("operator", "create", "a@example.com", "name", "extra")]
    [InlineData("operator", "resend-invite")]
    [InlineData("mail")]
    [InlineData("mail", "test")]
    [InlineData("mail", "send", "a@example.com")]
    public async Task AWrongCommandPrintsTheUsageAndChangesNothing(params string[] args)
    {
        await using var host = await HostAsync();

        var (code, output, error) = await RunAsync(host.Services, args);

        Assert.Equal(2, code);
        Assert.Empty(output);
        Assert.Contains(OperatorCli.Usage, error, StringComparison.Ordinal);
        Assert.Empty((await host.Services.GetRequiredService<IOperatorAdmin>().ListAsync(CancellationToken.None)).Value!);
    }
}