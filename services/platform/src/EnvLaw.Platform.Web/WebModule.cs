using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;
using EnvLaw.Platform.Web.Components;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

using MudBlazor.Services;

namespace EnvLaw.Platform.Web;

public static class WebModule
{
    /// <summary>營運者後台（Blazor Web App，全站 Interactive Server）。登入的設定見 <see cref="WebAuthModule"/>。</summary>
    public static IServiceCollection AddPlatformWeb(this IServiceCollection services)
    {
        services.AddWebAuth();
        services.AddRazorComponents().AddInteractiveServerComponents();
        services.AddCascadingAuthenticationState();
        services.AddMudServices();
        return services;
    }

    /// <summary>
    /// 接在 <c>UseAuthentication()</c> 之後，要早於授權：沒開的面在這裡就回 404，不會先被導去登入。
    /// 預設 scheme 是 ApiKey，所以 <c>/ui</c> 與 Blazor 的 SignalR 連線（<c>/_blazor</c>，不經任何 policy）
    /// 要自己用營運者 cookie 驗證，否則 circuit 裡的使用者會是匿名。客戶入口的 cookie 不在這裡驗證：
    /// 見 <see cref="UsePlatformPortalAuthentication"/>。
    /// </summary>
    public static IApplicationBuilder UsePlatformWebAuthentication(this IApplicationBuilder app)
    {
        var (operatorUi, portal) = Surfaces(app.ApplicationServices);
        return app.Use(async (context, next) =>
        {
            var path = context.Request.Path;
            var isUi = path.StartsWithSegments("/ui");

            if ((isUi && !operatorUi) || (path.StartsWithSegments(PortalPaths.Prefix) && !portal))
            {
                context.Response.StatusCode = StatusCodes.Status404NotFound;
                return;
            }

            if (isUi || path.StartsWithSegments("/_blazor"))
            {
                var result = await context.AuthenticateAsync(PlatformAuth.UiCookieScheme);
                if (result.Principal is not null)
                {
                    context.User = result.Principal;
                }
            }

            await next(context);
        });
    }

    /// <summary>
    /// 客戶入口的 cookie 驗證，<b>必須接在 <c>UseMultiTenant()</c> 之後</b>。驗證 cookie 會檢查安全戳記，
    /// 那要讀資料庫：<c>PlatformDbContext</c> 在建構時就捕捉當下的租戶，而且同一個請求裡的 scoped 服務只建一次，
    /// 所以若在租戶解析之前就驗證，整個請求（包括之後的登入、改密碼）都會拿到沒有租戶的 DbContext。
    /// 兩個 cookie 互不通用：營運者的通不過入口，客戶的通不過後台。
    /// </summary>
    public static IApplicationBuilder UsePlatformPortalAuthentication(this IApplicationBuilder app) =>
        app.Use(async (context, next) =>
        {
            if (context.Request.Path.StartsWithSegments(PortalPaths.Prefix))
            {
                var result = await context.AuthenticateAsync(PlatformAuth.PortalCookieScheme);
                if (result.Principal is not null)
                {
                    context.User = result.Principal;
                }
            }

            await next(context);
        });

    /// <summary>
    /// 把後台掛在 <c>/ui</c>、客戶入口掛在 <c>/portal</c>（頁面路由寫在各頁的 <c>@page</c>）。與 <c>/v2</c>、<c>/admin</c>
    /// 的路徑互不重疊，不在速率限制、計量的群組內。需要 <c>UseAntiforgery()</c>。
    /// </summary>
    public static WebApplication MapPlatformWeb(this WebApplication app)
    {
        // 兩個面都是可選功能，API 照常：後台沒有 Google 憑證就不開（/ui 回 404），入口要明確開
        // （Platform:Portal:Enabled）。Development 缺憑證時仍開著，本機能看到畫面（真的後台登入會被 Google 拒絕）。
        var (operatorUi, portal) = Surfaces(app.Services);
        var log = app.Services.GetRequiredService<ILoggerFactory>().CreateLogger("EnvLaw.Platform.Web");
        if (!operatorUi)
        {
            WebLog.UiDisabled(log);
        }

        if (!operatorUi && !portal)
        {
            return app;
        }

        app.MapStaticAssets();
        app.MapUiAuthEndpoints();
        app.MapPortalAuthEndpoints();
        app.MapRazorComponents<App>().AddInteractiveServerRenderMode();
        return app;
    }

    private static (bool OperatorUi, bool Portal) Surfaces(IServiceProvider services)
    {
        var development = services.GetRequiredService<IHostEnvironment>().IsDevelopment();
        return (
            services.GetRequiredService<IOptions<UiOptions>>().Value.HasGoogleCredentials || development,
            services.GetRequiredService<IOptions<PortalOptions>>().Value.Enabled || development);
    }
}