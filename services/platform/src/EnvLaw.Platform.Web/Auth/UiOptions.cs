using EnvLaw.Platform.SharedKernel;

using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Web.Auth;

/// <summary><c>Platform:Ui</c> 區段。Google 憑證只走環境變數（k8s 的 Secret），不進版控。</summary>
public sealed class UiOptions : IPlatformOptions
{
    public static string SectionName => "Platform:Ui";

    public GoogleCredentials Google { get; set; } = new();

    /// <summary>可登入後台的營運者 email（不分大小寫）。空清單 = 沒有人能登入。</summary>
    public IReadOnlyList<string> OperatorEmails { get; set; } = [];

    /// <summary>非空時，另要求 Google Workspace 網域（<c>hd</c> claim）相符。</summary>
    public string? AllowedHostedDomain { get; set; }
}

/// <summary>Google OAuth client 的憑證（<c>Platform:Ui:Google</c>）。</summary>
public sealed class GoogleCredentials
{
    public string ClientId { get; set; } = string.Empty;

    public string ClientSecret { get; set; } = string.Empty;
}

/// <summary>
/// 非 Development 缺 Google 憑證就啟動失敗；Development 缺憑證時，不碰後台的本機開發照常啟動
/// （真的登入時會被 Google 拒絕）。
/// </summary>
public sealed class UiOptionsValidator(IHostEnvironment environment) : IValidateOptions<UiOptions>
{
    public ValidateOptionsResult Validate(string? name, UiOptions options)
    {
        var missing = string.IsNullOrEmpty(options.Google.ClientId) || string.IsNullOrEmpty(options.Google.ClientSecret);
        return missing && !environment.IsDevelopment()
            ? ValidateOptionsResult.Fail("Platform:Ui:Google:ClientId and Platform:Ui:Google:ClientSecret are not configured.")
            : ValidateOptionsResult.Success;
    }
}