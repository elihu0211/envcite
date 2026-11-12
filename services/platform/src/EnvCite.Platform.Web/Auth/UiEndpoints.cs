using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Web.Auth;

public static class UiEndpoints
{
    public const string LoginPath = "/ui/login";
    public const string MfaPath = "/ui/login/mfa";
    public const string PasswordPath = "/ui/password";
    public const string SecurityPath = "/ui/security";
    public const string DeniedPath = "/ui/denied";
    public const string SignedOutPath = "/ui/signed-out";
    public const string HomePath = "/ui";
    public const string LanguagePath = "/ui/language";
    public const string SettingsPath = "/ui/settings";
    public const string AppearancePath = "/ui/appearance";

    /// <summary>登入、第二步驗證、改密碼、註冊驗證器都是頁面（靜態渲染的 POST 表單，見 <c>Components/Account</c>）；這裡只剩登出。</summary>
    public static IEndpointRouteBuilder MapUiAuthEndpoints(this IEndpointRouteBuilder app)
    {
        // POST + antiforgery：不能被別的網站用連結或圖片誘導登出。沒有表單參數可綁，所以自己驗證，
        // 不依賴 endpoint metadata。
        app.MapPost("/ui/logout", async (HttpContext context, IAntiforgery antiforgery, IOperatorAccount account) =>
        {
            try
            {
                await antiforgery.ValidateRequestAsync(context);
            }
            catch (AntiforgeryValidationException)
            {
                return Results.BadRequest();
            }

            await account.SignOutAsync(context.User, context.RequestAborted);
            return Results.Redirect(SignedOutPath);
        });

        // 換介面語言：登入前後都要能用（登入頁也有語言選項），所以不需要登入。
        app.MapPost(LanguagePath, (HttpContext context, IAntiforgery antiforgery) =>
            Localization.SwitchAsync(context, antiforgery, SafeReturnUrl));

        // 設定外觀：只是這個瀏覽器的偏好（cookie），與語言同理不需要登入；入口只在登入後的個人設定提供，匿名頁面不放切換。
        app.MapPost(AppearancePath, (HttpContext context, IAntiforgery antiforgery, IAppearancePreference preference) =>
            AppearanceModule.SetAsync(context, antiforgery, preference, SafeReturnUrl));

        return app;
    }

    public static string SafeReturnUrl(string? returnUrl) => SignInRoutes.Operator.SafeReturnUrl(returnUrl);
}