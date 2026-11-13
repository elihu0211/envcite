using EnvCite.Platform.Api;

using Microsoft.Extensions.Configuration;

namespace EnvCite.Platform.Tests;

/// <summary>
/// <c>mail preview</c>：不寄信、不連資料庫就能把信件版型寫成檔案查看。這裡不用任何伺服器或資料庫（沒有 <see cref="PlatformFactory"/>），
/// 測試能跑完本身就是「不需要它們」的證明。
/// </summary>
public sealed class MailPreviewTests : IDisposable
{
    private static readonly string[] Names =
        ["invitation", "invitation-tenant", "verification", "verification-tenant", "test"];

    private readonly string _directory = Path.Combine(Path.GetTempPath(), $"mail-preview-{Guid.NewGuid():N}");

    public void Dispose()
    {
        if (Directory.Exists(_directory))
            Directory.Delete(_directory, recursive: true);
    }

    private static IConfiguration Settings(params (string Key, string Value)[] values) =>
        new ConfigurationBuilder().AddInMemoryCollection(values.ToDictionary(v => v.Key, v => (string?)v.Value)).Build();

    private static async Task<(int Code, string Output, string Error)> RunAsync(IConfiguration configuration, params string[] args)
    {
        var output = new StringWriter();
        var error = new StringWriter();
        var code = await MailPreview.RunAsync(configuration, args, output, error, TestContext.Current.CancellationToken);
        return (code, output.ToString(), error.ToString());
    }

    [Fact]
    public void ItIsTheMailPreviewCommandOnlyForMailPreview()
    {
        Assert.True(MailPreview.IsCommand(["mail", "preview", "out"]));
        Assert.True(MailPreview.IsCommand(["mail", "preview"]));
        Assert.False(MailPreview.IsCommand(["mail", "test", "a@example.test"]));
        Assert.False(MailPreview.IsCommand(["operator", "create", "a@example.test"]));
        Assert.False(MailPreview.IsCommand([]));
    }

    [Fact]
    public async Task ItWritesEveryMailAsHtmlAndTextWithPlaceholderLinks()
    {
        var (code, output, error) = await RunAsync(Settings(), "mail", "preview", _directory);

        Assert.Equal(0, code);
        Assert.Empty(error);
        Assert.Contains("占位值", output, StringComparison.Ordinal);
        Assert.Equal(
            Names.SelectMany(n => new[] { $"{n}.html", $"{n}.txt" }).Order(StringComparer.Ordinal),
            Directory.GetFiles(_directory).Select(Path.GetFileName).Order(StringComparer.Ordinal));
        foreach (var name in Names)
        {
            var html = await File.ReadAllTextAsync(Path.Combine(_directory, $"{name}.html"), TestContext.Current.CancellationToken);
            var text = await File.ReadAllTextAsync(Path.Combine(_directory, $"{name}.txt"), TestContext.Current.CancellationToken);
            Assert.StartsWith("<!DOCTYPE html>", html, StringComparison.Ordinal);
            Assert.Contains("環境知識", html, StringComparison.Ordinal);
            Assert.Equal(!name.StartsWith("verification", StringComparison.Ordinal), text.Contains("環境知識", StringComparison.Ordinal)); // 驗證信的說明句不帶名稱
            if (name != "test")
            {
                // 連結是保留網域上的假值，不是任何真實主機，權杖也不能用。
                Assert.Contains("https://platform.example/", html, StringComparison.Ordinal);
                Assert.Contains("https://platform.example/", text, StringComparison.Ordinal);
                Assert.Contains("PREVIEW-NOT-A-REAL-TOKEN", text, StringComparison.Ordinal);
            }
        }

        // 有租戶的走入口、沒租戶的是營運者。
        Assert.Contains("/portal/acme/activate", await File.ReadAllTextAsync(Path.Combine(_directory, "invitation-tenant.txt"), TestContext.Current.CancellationToken), StringComparison.Ordinal);
        Assert.Contains("/ui/activate", await File.ReadAllTextAsync(Path.Combine(_directory, "verification.txt"), TestContext.Current.CancellationToken), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ItFollowsTheConfiguredNameAndLifetime()
    {
        var configuration = Settings(("Platform:Brand:Name", "測試系統"), ("Platform:Mail:LinkLifetime", "12:00:00"));

        var (code, _, _) = await RunAsync(configuration, "mail", "preview", _directory);

        Assert.Equal(0, code);
        foreach (var file in Directory.GetFiles(_directory))
        {
            var content = await File.ReadAllTextAsync(file, TestContext.Current.CancellationToken);
            if (!Path.GetFileName(file).StartsWith("verification", StringComparison.Ordinal) || file.EndsWith(".html", StringComparison.Ordinal))
                Assert.Contains("測試系統", content, StringComparison.Ordinal);

            Assert.DoesNotContain("環境知識", content, StringComparison.Ordinal);
        }

        Assert.Contains("連結 12 小時內有效", await File.ReadAllTextAsync(Path.Combine(_directory, "invitation.txt"), TestContext.Current.CancellationToken), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ABadNameStopsWithTheKeyNamedAndWritesNothing()
    {
        var (code, _, error) = await RunAsync(Settings(("Platform:Brand:Name", "   ")), "mail", "preview", _directory);

        Assert.Equal(1, code);
        Assert.Contains("Platform:Brand:Name", error, StringComparison.Ordinal);
        Assert.False(Directory.Exists(_directory));
    }

    // 預覽用的是正式環境同一個組信服務與選項：改語言、寬度、色票在預覽就看得到。
    [Fact]
    public async Task ItFollowsTheTemplateOptions()
    {
        var configuration = Settings(("Platform:Mail:Template:Culture", "en"), ("Platform:Mail:Template:Width", "560"), ("Platform:Mail:Template:Light:Button", "#0b5cad"));

        var (code, _, _) = await RunAsync(configuration, "mail", "preview", _directory);

        Assert.Equal(0, code);
        var html = await File.ReadAllTextAsync(Path.Combine(_directory, "invitation-tenant.html"), TestContext.Current.CancellationToken);
        Assert.Contains("<html lang=\"en\"", html, StringComparison.Ordinal);
        Assert.Contains("max-width:560px", html, StringComparison.Ordinal);
        Assert.Contains("fillcolor=\"#0b5cad\"", html, StringComparison.Ordinal);
        Assert.StartsWith("This is a test message", await File.ReadAllTextAsync(Path.Combine(_directory, "test.txt"), TestContext.Current.CancellationToken), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ABadTemplateOptionStopsWithTheKeyNamedAndWritesNothing()
    {
        var (code, _, error) = await RunAsync(Settings(("Platform:Mail:Template:Light:Muted", "#c0c0c0")), "mail", "preview", _directory);

        Assert.Equal(1, code);
        Assert.Contains("Platform:Mail:Template:Light:Muted", error, StringComparison.Ordinal);
        Assert.False(Directory.Exists(_directory));
    }

    [Theory]
    [InlineData("mail", "preview")]
    [InlineData("mail", "preview", "a", "b")]
    [InlineData("mail", "preview", "  ")]
    public async Task AMissingOrExtraArgumentIsAUsageError(params string[] args)
    {
        var (code, _, error) = await RunAsync(Settings(), args);

        Assert.Equal(2, code);
        Assert.Contains("mail preview", error, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnUnwritableTargetIsAFailureNotACrash()
    {
        var file = Path.Combine(Path.GetTempPath(), $"mail-preview-file-{Guid.NewGuid():N}");
        await File.WriteAllTextAsync(file, "x", TestContext.Current.CancellationToken);
        try
        {
            var (code, _, error) = await RunAsync(Settings(), "mail", "preview", file); // 目標是一個檔案，不是目錄

            Assert.Equal(1, code);
            Assert.Contains("寫檔失敗", error, StringComparison.Ordinal);
        }
        finally
        {
            File.Delete(file);
        }
    }
}