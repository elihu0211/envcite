using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// 後台的閘門與 cookie 驗證（<c>UsePlatformWebAuthentication</c>）：沒開的面在這裡回 404，其餘的 <c>/ui</c>
/// 與 <c>/_blazor</c> 請求以營運者 cookie 驗證。接線與順序的理由見 <c>WebModule.UsePlatformWebAuthentication</c>。
/// </summary>
internal sealed class SurfaceGateMiddleware(RequestDelegate next, PlatformSurfaces surfaces)
{
    public async Task InvokeAsync(HttpContext context)
    {
        var path = context.Request.Path;
        var isUi = path.StartsWithSegments(UiEndpoints.HomePath);

        if ((isUi && !surfaces.OperatorUi)
            || (path.StartsWithSegments(PortalPaths.Prefix) && !surfaces.Portal)
            || (path.StartsWithSegments(WebModule.DesignGalleryPath) && !surfaces.DesignGallery))
        {
            context.Response.StatusCode = StatusCodes.Status404NotFound;
            return;
        }

        if (isUi)
        {
            // 後台的登入頁與註冊頁含驗證器金鑰與復原碼：瀏覽器與中間的快取都不能留（客戶入口同理）。
            context.Response.Headers.CacheControl = "no-store";
        }

        if (isUi || path.StartsWithSegments(WebModule.BlazorPath))
        {
            var result = await context.AuthenticateAsync(PlatformAuth.UiCookieScheme);
            if (result.Principal is not null)
                context.User = result.Principal;
        }

        await next(context);
    }
}

/// <summary>
/// 客戶入口的 cookie 驗證（<c>UsePlatformPortalAuthentication</c>）。必須在租戶解析之後，理由見
/// <c>WebModule.UsePlatformPortalAuthentication</c>。
/// </summary>
internal sealed class PortalAuthenticationMiddleware(RequestDelegate next, PlatformSurfaces surfaces)
{
    public async Task InvokeAsync(HttpContext context)
    {
        var path = context.Request.Path;
        if (path.StartsWithSegments(PortalPaths.Prefix))
        {
            // 入口的頁面含個人資料、驗證器金鑰與復原碼：瀏覽器與中間的快取都不能留。
            context.Response.Headers.CacheControl = "no-store";
            var result = await context.AuthenticateAsync(PlatformAuth.PortalCookieScheme);
            if (result.Principal is not null)
                context.User = result.Principal;
        }
        else if (surfaces.Portal && path.StartsWithSegments(WebModule.BlazorPath))
        {
            var result = await context.AuthenticateAsync(PlatformAuth.PortalCookieScheme);
            if (result.Principal is not null)
                context.User = CircuitIdentities.Combine(context.User, result.Principal);
        }

        await next(context);
    }
}