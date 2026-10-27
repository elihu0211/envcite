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

        return app;
    }

    /// <summary>只接受 <c>/ui</c> 底下的站內相對路徑；其餘一律回首頁（防 open redirect）。</summary>
    public static string SafeReturnUrl(string? returnUrl)
    {
        if (string.IsNullOrEmpty(returnUrl)
            || !returnUrl.StartsWith(HomePath, StringComparison.Ordinal)
            || (returnUrl.Length > HomePath.Length && returnUrl[HomePath.Length] is not ('/' or '?' or '#'))
            || returnUrl.Contains('\\', StringComparison.Ordinal)
            || returnUrl.StartsWith("/ui//", StringComparison.Ordinal))
        {
            return HomePath;
        }

        return returnUrl;
    }
}