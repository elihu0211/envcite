using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Web.Auth;

public static class PortalEndpoints
{
    public static IEndpointRouteBuilder MapPortalAuthEndpoints(this IEndpointRouteBuilder app)
    {
        // POST + antiforgery：不能被別的網站用連結或圖片誘導登出。沒有表單參數可綁，所以自己驗證。
        app.MapPost($"{PortalPaths.Prefix}/{{tenant}}/logout", async (HttpContext context, IAntiforgery antiforgery, IPortalAccount account) =>
        {
            try
            {
                await antiforgery.ValidateRequestAsync(context);
            }
            catch (AntiforgeryValidationException)
            {
                return Results.BadRequest();
            }

            // 路徑上的租戶不存在，就當作這個路徑不存在。
            if (account.CurrentTenant is not { } tenant)
            {
                return Results.NotFound();
            }

            await account.SignOutAsync(context.User, context.RequestAborted);
            return Results.Redirect(PortalPaths.Login(tenant.Identifier));
        }).AllowAnonymous();

        return app;
    }

    /// <summary>登入後導回的地方：只接受這個租戶入口之下的站內相對路徑，其餘回首頁（防 open redirect）。</summary>
    public static string SafeReturnUrl(string? returnUrl, string tenantIdentifier)
    {
        var home = PortalPaths.Home(tenantIdentifier);
        if (string.IsNullOrEmpty(returnUrl)
            || !returnUrl.StartsWith(home, StringComparison.Ordinal)
            || (returnUrl.Length > home.Length && returnUrl[home.Length] is not ('/' or '?' or '#'))
            || returnUrl.Contains('\\', StringComparison.Ordinal)
            || returnUrl.StartsWith($"{home}//", StringComparison.Ordinal))
        {
            return home;
        }

        return returnUrl;
    }
}