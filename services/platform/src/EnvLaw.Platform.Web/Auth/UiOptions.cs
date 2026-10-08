namespace EnvLaw.Platform.Web.Auth;

/// <summary><c>Platform:Ui</c> 區段。Google 憑證只走環境變數，不進版控。</summary>
public sealed class UiOptions
{
    /// <summary>可登入後台的營運者 email（不分大小寫）。空清單 = 沒有人能登入。</summary>
    public IReadOnlyList<string> OperatorEmails { get; set; } = [];

    /// <summary>非空時，另要求 Google Workspace 網域（<c>hd</c> claim）相符。</summary>
    public string? AllowedHostedDomain { get; set; }
}