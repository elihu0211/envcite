using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvLaw.Platform.Web.Auth;

public static class UiEndpoints
{
    public const string LoginPath = "/ui/login";
    public const string DeniedPath = "/ui/denied";
    public const string SignedOutPath = "/ui/signed-out";
    public const string HomePath = "/ui";

    public static IEndpointRouteBuilder MapUiAuthEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet(LoginPath, (string? returnUrl) =>
            TypedResults.Challenge(
                new AuthenticationProperties { RedirectUri = SafeReturnUrl(returnUrl) },
                [PlatformAuth.UiGoogleScheme]))
            .AllowAnonymous();

        // POST + antiforgery：不能被別的網站用連結或圖片誘導登出。沒有表單參數可綁，所以自己驗證，
        // 不依賴 endpoint metadata。
        app.MapPost("/ui/logout", async (HttpContext context, IAntiforgery antiforgery, IAuditLog audit) =>
        {
            try
            {
                await antiforgery.ValidateRequestAsync(context);
            }
            catch (AntiforgeryValidationException)
            {
                return Results.BadRequest();
            }

            if (context.User.Identity?.IsAuthenticated == true)
            {
                await audit.AppendAsync(
                    context.User,
                    new AuditEntry(OperatorSignIn.SignedOut, TargetType: "operator", TargetId: context.User.FindFirst(ClaimTypes.Email)?.Value),
                    context.RequestAborted);
            }

            await context.SignOutAsync(PlatformAuth.UiCookieScheme);
            return Results.Redirect(SignedOutPath);
        });

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