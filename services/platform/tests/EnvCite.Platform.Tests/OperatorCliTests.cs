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

    [Theory]
    [InlineData("operator")]
    [InlineData("operator", "create")]
    [InlineData("operator", "unknown", "a@example.com")]
    [InlineData("operator", "create", "a@example.com", "name", "extra")]
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