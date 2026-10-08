using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;
using EnvLaw.Platform.Web.Components;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Builder;
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
    /// 接在 <c>UseAuthentication()</c> 之後。預設 scheme 是 ApiKey，所以 <c>/ui</c> 與 Blazor 的 SignalR 連線
    /// （<c>/_blazor</c>，不經任何 policy）要自己用 cookie 驗證，否則 circuit 裡的使用者會是匿名。
    /// </summary>
    public static IApplicationBuilder UsePlatformWebAuthentication(this IApplicationBuilder app) =>
        app.Use(async (context, next) =>
        {
            if (context.Request.Path.StartsWithSegments("/ui") || context.Request.Path.StartsWithSegments("/_blazor"))
            {
                var result = await context.AuthenticateAsync(PlatformAuth.UiCookieScheme);
                if (result.Principal is not null)
                {
                    context.User = result.Principal;
                }
            }

            await next(context);
        });

    /// <summary>
    /// 把後台掛在 <c>/ui</c>（頁面路由寫在各頁的 <c>@page</c>）。與 <c>/v2</c>、<c>/admin</c> 的路徑互不重疊，
    /// 不在速率限制、計量的群組內。需要 <c>UseAntiforgery()</c>。
    /// </summary>
    public static WebApplication MapPlatformWeb(this WebApplication app)
    {
        // 後台是可選功能：沒有 Google 憑證就整個不掛（/ui 回 404、靜態資產也不公開），API 照常。
        // Development 缺憑證時仍掛著，本機能看到畫面（真的登入會被 Google 拒絕）。
        var ui = app.Services.GetRequiredService<IOptions<UiOptions>>().Value;
        if (!ui.HasGoogleCredentials && !app.Environment.IsDevelopment())
        {
            WebLog.UiDisabled(app.Services.GetRequiredService<ILoggerFactory>().CreateLogger("EnvLaw.Platform.Web"));
            return app;
        }

        app.MapStaticAssets();
        app.MapUiAuthEndpoints();
        app.MapRazorComponents<App>().AddInteractiveServerRenderMode();
        return app;
    }
}