using EnvCite.ServiceDefaults;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Google;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// <c>Platform:Portal:Google</c> 區段：客戶入口的 Google 登入。預設關閉；關閉時登入頁沒有按鈕、端點回 404。
/// 全站只有一個 Google 用戶端（OAuth 2.0 授權碼加 PKCE），不是每租戶一組；金鑰只經環境變數。
/// 登入只對應到已存在的成員，不建立帳號（見 <see cref="PortalAccount"/>）。
/// </summary>
public sealed class PortalGoogleOptions : ISectionOptions
{
    public static string SectionName => "Platform:Portal:Google";

    public bool Enabled { get; set; }

    public string ClientId { get; set; } = string.Empty;

    public string ClientSecret { get; set; } = string.Empty;
}

internal sealed class PortalGoogleOptionsValidator : IValidateOptions<PortalGoogleOptions>
{
    public ValidateOptionsResult Validate(string? name, PortalGoogleOptions options) =>
        options.Enabled && (string.IsNullOrWhiteSpace(options.ClientId) || string.IsNullOrWhiteSpace(options.ClientSecret))
            ? ValidateOptionsResult.Fail("Platform:Portal:Google:ClientId and ClientSecret are required when Platform:Portal:Google:Enabled is true.")
            : ValidateOptionsResult.Success;
}

internal sealed class GoogleSignInAvailability(IOptions<PortalGoogleOptions> options) : IGoogleSignInAvailability
{
    public bool Enabled => options.Value.Enabled;
}

/// <summary>
/// 把 <see cref="PortalGoogleOptions"/> 套到框架的 Google handler。handler 一律註冊（授權管線每個請求都會問它），
/// 但它在選項為空時會讓每個請求都丟例外，所以關閉時給一組不會被使用的佔位值：起點端點關閉時回 404，沒有人會走到 Google。
/// </summary>
internal sealed class ConfigurePortalGoogle(IOptions<PortalGoogleOptions> portal) : IConfigureNamedOptions<GoogleOptions>
{
    private const string Inert = "disabled";

    public void Configure(string? name, GoogleOptions options)
    {
        if (name != PlatformAuth.PortalGoogleScheme)
            return;

        var google = portal.Value;
        options.ClientId = google.Enabled ? google.ClientId : Inert;
        options.ClientSecret = google.Enabled ? google.ClientSecret : Inert;
    }

    public void Configure(GoogleOptions options) => Configure(PlatformAuth.PortalGoogleScheme, options);

    /// <summary>Google handler 的固定設定（ClientId 與金鑰由上面的 <c>Configure</c> 依環境填入，這些不隨環境變）。</summary>
    internal static void ConfigureHandler(GoogleOptions google)
    {
        // 外部身分先放在 Identity 原生的暫存 cookie，SignInManager 從那裡讀；租戶路徑下的第二段才簽發 session。
        google.SignInScheme = IdentityConstants.ExternalScheme;
        google.CallbackPath = PortalGoogle.CallbackPath;
        google.SaveTokens = false;

        // 只認 Google 說「這個 email 已驗證」的身分；其餘 claim 用框架預設對應（sub、email、name）。
        google.ClaimActions.MapJsonKey(PortalGoogleClaims.EmailVerified, "email_verified");

        // 取消授權、state 不符、Google 回錯：回到發起的租戶的登入頁，只說失敗，不轉述 Google 的錯誤內容；
        // 沒有發起的租戶（例如功能關閉時有人直接打回呼位址）就當作不存在。
        google.Events.OnRemoteFailure = context =>
        {
            context.HandleResponse();
            if (context.Properties?.Items.TryGetValue(PortalGoogle.TenantItem, out var tenant) == true && !string.IsNullOrEmpty(tenant))
                context.Response.Redirect($"{PortalPaths.Login(tenant)}?google=failed");
            else
                context.Response.StatusCode = StatusCodes.Status404NotFound;

            return Task.CompletedTask;
        };
    }
}

internal static class PortalGoogleClaims
{
    public const string EmailVerified = "urn:envcite:google:email_verified";
}