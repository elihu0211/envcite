using EnvLaw.Platform.SharedKernel;

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

    /// <summary>
    /// 後台是可選功能：Google 憑證沒設，就不掛 <c>/ui</c>（見 <c>MapPlatformWeb</c>），API 照常啟動。
    /// 憑證是 Secret，上線順序上常晚於程式碼；缺憑證不該讓整個服務起不來。
    /// </summary>
    public bool HasGoogleCredentials =>
        !string.IsNullOrEmpty(Google.ClientId) && !string.IsNullOrEmpty(Google.ClientSecret);
}

/// <summary>Google OAuth client 的憑證（<c>Platform:Ui:Google</c>）。</summary>
public sealed class GoogleCredentials
{
    public string ClientId { get; set; } = string.Empty;

    public string ClientSecret { get; set; } = string.Empty;
}