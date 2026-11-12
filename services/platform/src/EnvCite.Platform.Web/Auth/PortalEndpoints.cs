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

        // 換介面語言：登入前後都要能用；導回的位置只限這個租戶的入口之下。
        app.MapPost($"{PortalPaths.Prefix}/{{tenant}}/language", (HttpContext context, IAntiforgery antiforgery, IPortalAccount account) =>
            account.CurrentTenant is not { } tenant
                ? Task.FromResult(Results.NotFound())
                : Localization.SwitchAsync(context, antiforgery, SignInRoutes.Portal(tenant.Identifier).SafeReturnUrl)).AllowAnonymous();

        // 設定外觀：同語言，只是這個瀏覽器的偏好（cookie）；路徑上的租戶不存在就當作這個路徑不存在。
        app.MapPost($"{PortalPaths.Prefix}/{{tenant}}/appearance", (HttpContext context, IAntiforgery antiforgery, IAppearancePreference preference, IPortalAccount account) =>
            account.CurrentTenant is not { } tenant
                ? Task.FromResult(Results.NotFound())
                : AppearanceModule.SetAsync(context, antiforgery, preference, SignInRoutes.Portal(tenant.Identifier).SafeReturnUrl)).AllowAnonymous();

        return app;
    }
}