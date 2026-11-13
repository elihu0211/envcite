namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// 登入流程的去處。營運者（<c>/ui</c>）與客戶入口（<c>/portal/{租戶}</c>）各一組；登入與第二步驗證的元件只認這個，
/// 不為兩邊各寫一份。
/// </summary>
public sealed record SignInRoutes(string Login, string Mfa, string Password, string Security, string Home)
{
    public static SignInRoutes Operator { get; } =
        new(UiEndpoints.LoginPath, UiEndpoints.MfaPath, UiEndpoints.PasswordPath, UiEndpoints.SecurityPath, UiEndpoints.HomePath);

    public static SignInRoutes Portal(string tenantIdentifier)
    {
        var (login, home) = (PortalPaths.Login(tenantIdentifier), PortalPaths.Home(tenantIdentifier));
        return new(login, $"{login}/mfa", PortalPaths.Password(tenantIdentifier), $"{home}/security", home);
    }

    /// <summary>登入後導回的地方：只接受 <see cref="Home"/> 之下的站內相對路徑，其餘回首頁（防 open redirect）。</summary>
    public string SafeReturnUrl(string? returnUrl)
    {
        if (string.IsNullOrEmpty(returnUrl)
            || !returnUrl.StartsWith(Home, StringComparison.Ordinal)
            || (returnUrl.Length > Home.Length && returnUrl[Home.Length] is not ('/' or '?' or '#'))
            || returnUrl.Contains('\\', StringComparison.Ordinal)
            || returnUrl.StartsWith($"{Home}//", StringComparison.Ordinal))
        {
            return Home;
        }

        return returnUrl;
    }

    /// <summary>第二步驗證頁的連結：帶著登入後要去的地方，以及是否改用復原碼。</summary>
    public string MfaLink(string? returnUrl, bool recovery = false)
    {
        var parameters = new List<string>();
        if (recovery)
            parameters.Add("recovery=1");

        if (!string.IsNullOrEmpty(returnUrl))
            parameters.Add($"returnUrl={Uri.EscapeDataString(returnUrl)}");

        return parameters.Count == 0 ? Mfa : $"{Mfa}?{string.Join('&', parameters)}";
    }
}