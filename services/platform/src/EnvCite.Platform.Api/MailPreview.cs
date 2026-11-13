using System.Text;

namespace EnvCite.Platform.Api;

/// <summary>
/// <c>mail preview &lt;輸出目錄&gt;</c>：把信件版型寫成檔案，用瀏覽器或信件客戶端的預覽查看，<b>不寄信、不連資料庫</b>。
/// 因為不需要它們，在建立主機（遷移與種子資料）之前就處理完：用 <see cref="MailServiceCollectionExtensions.AddPlatformMail"/> 註冊的真實 <see cref="IMailComposer"/> 組信（不解析 <see cref="IMailSender"/>），
/// 系統名稱、連結期限與版型選項照目前的設定，所以本機改了名稱、語言或色票就能看到效果。連結是占位值（網址的來源不是任何真實主機，權杖是不能用的假值），沒有任何真實的登入鑰匙。
/// </summary>
public static class MailPreview
{
    public const string Usage = "用法：mail preview <輸出目錄>（寫出 HTML 與純文字檔；不寄信、不連資料庫）";

    /// <summary>占位連結的來源；<c>.example</c> 是保留的網域，永遠不會解析到真實主機。</summary>
    private const string PlaceholderOrigin = "https://platform.example";

    private const string PlaceholderUserId = "00000000-0000-0000-0000-000000000000";

    /// <summary>長度與真實權杖同級（約三百字元），才看得出長連結在窄螢幕上怎麼換行；內容是假的。</summary>
    private static readonly string PlaceholderCode = string.Concat(Enumerable.Repeat("PREVIEW-NOT-A-REAL-TOKEN-", 14));

    public static bool IsCommand(string[] args) => args is ["mail", "preview", ..];

    /// <returns>結束碼：0 成功，1 設定錯誤或寫檔失敗，2 用法錯誤。</returns>
    public static async Task<int> RunAsync(
        IConfiguration configuration, string[] args, TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        if (args is not ["mail", "preview", var directory] || string.IsNullOrWhiteSpace(directory))
        {
            await error.WriteLineAsync(Usage, cancellationToken);
            return 2;
        }

        var services = new ServiceCollection();
        services.AddSingleton(configuration);
        services.AddLogging();
        services.AddPlatformMail();
        using var provider = services.BuildServiceProvider();

        (string Name, ComposedMail Mail)[] samples;
        try
        {
            var composer = provider.GetRequiredService<IMailComposer>();
            var lifetime = provider.GetRequiredService<IOptions<MailOptions>>().Value.LinkLifetime;
            var memberLink = Placeholder("/portal/acme/activate");
            var operatorLink = Placeholder("/ui/activate");
            samples =
            [
                ("invitation", await composer.ComposeActivationAsync(new(operatorLink, lifetime, null, Invitation: true), cancellationToken)),
                ("invitation-tenant", await composer.ComposeActivationAsync(new(memberLink, lifetime, "acme", Invitation: true), cancellationToken)),
                ("verification", await composer.ComposeActivationAsync(new(operatorLink, lifetime, null, Invitation: false), cancellationToken)),
                ("verification-tenant", await composer.ComposeActivationAsync(new(memberLink, lifetime, "acme", Invitation: false), cancellationToken)),
                ("test", await composer.ComposeTestAsync(cancellationToken)),
            ];
        }
        catch (OptionsValidationException exception)
        {
            await error.WriteLineAsync(string.Join('\n', exception.Failures), cancellationToken);
            return 1;
        }

        try
        {
            var target = Directory.CreateDirectory(directory);
            var utf8 = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
            foreach (var (name, mail) in samples)
            {
                await File.WriteAllTextAsync(Path.Combine(target.FullName, $"{name}.html"), mail.Content.Html, utf8, cancellationToken);
                await File.WriteAllTextAsync(Path.Combine(target.FullName, $"{name}.txt"), mail.Content.Text, utf8, cancellationToken);
            }

            await output.WriteLineAsync($"已寫出 {samples.Length * 2} 個檔案到 {target.FullName}（{samples.Length} 種信，各有 .html 與 .txt）。連結是占位值，沒有寄信，也沒有連線。", cancellationToken);
            return 0;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or ArgumentException or NotSupportedException)
        {
            await error.WriteLineAsync($"寫檔失敗（{exception.GetType().Name}）：{directory}", cancellationToken);
            return 1;
        }
    }

    private static Uri Placeholder(string path) =>
        new($"{PlaceholderOrigin}{path}?userId={PlaceholderUserId}&code={PlaceholderCode}");
}