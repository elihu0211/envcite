using System.Text.RegularExpressions;

using Microsoft.AspNetCore.Mvc.Testing;

using OtpNet;

namespace EnvCite.Platform.Tests;

/// <summary>營運者後台測試共用：路徑、建立營運者、驗證碼。表單欄位與客戶入口同名（<c>Input.Email</c> 等），所以沿用 <see cref="PortalTestSupport"/>。</summary>
internal static partial class OperatorTestSupport
{
    public const string UiLogin = "/ui/login";
    public const string UiMfa = "/ui/login/mfa";
    public const string UiPassword = "/ui/password";
    public const string UiSecurity = "/ui/security";
    public const string UiHome = "/ui";

    public static string NewOperatorEmail() => $"op-{Guid.NewGuid():N}"[..20] + "@example.com";

    public static Dictionary<string, string> Code(string code) => new() { ["Input.Code"] = code };

    public static string CodeAt(string key, DateTimeOffset at) => new Totp(Base32Encoding.ToBytes(key)).ComputeTotp(at.UtcDateTime);

    /// <summary>經服務建立一位營運者；回傳初始密碼。</summary>
    public static async Task<(string Id, string Email, string Password)> NewOperatorAsync(WebApplicationFactory<Program> app, string? email = null)
    {
        email ??= NewOperatorEmail();
        var created = await app.Services.GetRequiredService<IOperatorAdmin>()
            .CreateAsync(OperatorSession.Principal("admin@example.com"), email, "王小明", CancellationToken.None);
        Assert.True(created.IsOk, created.Message);
        return (created.Value!.Operator.Id, email, created.Value.Password);
    }

    public static string SharedKeyOf(string html) => SharedKey().Match(html).Groups[1].Value.Replace(" ", string.Empty, StringComparison.Ordinal);

    public static List<string> RecoveryCodesOf(string html) => RecoveryCodeItem().Matches(html).Select(m => m.Groups[1].Value).ToList();

    [GeneratedRegex("""id="shared-key">([^<]+)</code>""")]
    private static partial Regex SharedKey();

    [GeneratedRegex("""<li><code class="ep-mono">([^<]+)</code></li>""")]
    private static partial Regex RecoveryCodeItem();
}