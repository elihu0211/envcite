using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;
using EnvCite.Platform.Web.Components;
using EnvCite.Platform.Web.Design;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Diagnostics;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

using MudBlazor;
using MudBlazor.Services;

namespace EnvCite.Platform.Web;

public static class WebModule
{
    /// <summary>營運者後台（Blazor Web App，全站 Interactive Server）。登入的設定見 <see cref="WebAuthModule"/>。</summary>
    public static IServiceCollection AddPlatformWeb(this IServiceCollection services)
    {
        services.AddPlatformBrand();
        services.AddSingleton<BrandText>();
        services.AddWebAuth();
        services.AddRazorComponents().AddInteractiveServerComponents();
        // 取代預設的 AuthenticationStateProvider：circuit 每分鐘重驗營運者帳號（見 OperatorAuthenticationStateProvider）。
        services.AddScoped<AuthenticationStateProvider, OperatorAuthenticationStateProvider>();
        services.AddCascadingAuthenticationState();
        services.AddMudServices();
        // MudBlazor 內建文字的繁體中文；英文退回它內建的英文。
        services.AddTransient<MudLocalizer, PlatformMudLocalizer>();
        // 外觀只有 DesignTokens 一個來源。這裡把它裝成 MudBlazor 的主題，註冊一次，元件注入，不在元件裡再建一次。
        services.AddSingleton(PlatformTheme.Create());
        return services;
    }

    /// <summary>
    /// 後台、入口與展示頁上找不到的網址（沒有任何端點接手、回應沒有內容的 404）畫「找不到頁面」，不是一片空白。
    /// 必須接在 <c>UsePlatformLocalization()</c> 之前，而且<b>後面要明確呼叫 <c>UseRouting()</c></b>：重新執行 <see cref="NotFoundPath"/>
    /// 時端點已被清掉，要在這個中介層的下游重新比對路由、並再經過語言與其後的所有中介層；WebApplication 自動加的 UseRouting
    /// 在整條管線的最前面，在下游沒有。
    /// 只處理 404：同一個空本文的 429、403 等不能被畫成「找不到」；沒開的面不在範圍內，維持空的 404、不簽發任何 cookie。
    /// </summary>
    public static IApplicationBuilder UsePlatformNotFoundPage(this IApplicationBuilder app)
    {
        var (operatorUi, portal) = Surfaces(app.ApplicationServices);
        var development = app.ApplicationServices.GetRequiredService<IHostEnvironment>().IsDevelopment();
        if (!operatorUi && !portal)
        {
            return app;
        }

        return app.UseWhen(
            context =>
            {
                var path = context.Request.Path;
                return (operatorUi && path.StartsWithSegments("/ui"))
                    || (portal && path.StartsWithSegments(PortalPaths.Prefix))
                    || (development && path.StartsWithSegments(DesignGalleryPath));
            },
            branch =>
            {
                branch.UseStatusCodePagesWithReExecute(NotFoundPath, createScopeForStatusCodePages: true);
                branch.Use(async (context, next) =>
                {
                    await next(context);
                    if (context.Response.StatusCode != StatusCodes.Status404NotFound
                        && context.Features.Get<IStatusCodePagesFeature>() is { } statusPages)
                    {
                        statusPages.Enabled = false;
                    }
                });
            });
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
        var development = app.ApplicationServices.GetRequiredService<IHostEnvironment>().IsDevelopment();
        return app.Use(async (context, next) =>
        {
            var path = context.Request.Path;
            var isUi = path.StartsWithSegments("/ui");

            // /design 是元件展示頁：只在 Development，其他環境不存在。
            if ((isUi && !operatorUi) || (path.StartsWithSegments(PortalPaths.Prefix) && !portal)
                || (path.StartsWithSegments(DesignGalleryPath) && !development))
            {
                context.Response.StatusCode = StatusCodes.Status404NotFound;
                return;
            }

            if (isUi)
            {
                // 後台的登入頁與註冊頁含驗證器金鑰與復原碼：瀏覽器與中間的快取都不能留（客戶入口同理）。
                context.Response.Headers.CacheControl = "no-store";
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
                // 入口的頁面含個人資料、驗證器金鑰與復原碼：瀏覽器與中間的快取都不能留。
                context.Response.Headers.CacheControl = "no-store";
                var result = await context.AuthenticateAsync(PlatformAuth.PortalCookieScheme);
                if (result.Principal is not null)
                {
                    context.User = result.Principal;
                }
            }

            await next(context);
        });

    /// <summary>
    /// 把後台掛在 <c>/ui</c>、客戶入口掛在 <c>/portal</c>（頁面路由寫在各頁的 <c>@page</c>）。與 <c>/v2</c>
    /// 的路徑互不重疊，不在速率限制、計量的群組內。需要 <c>UseAntiforgery()</c>。
    /// </summary>
    public static WebApplication MapPlatformWeb(this WebApplication app)
    {
        // 兩個面都是可選功能，API 照常：要明確開（Platform:Ui:Enabled、Platform:Portal:Enabled），沒開就不掛（回 404）。
        // Development 一律開著，本機能看到畫面。
        var (operatorUi, portal) = Surfaces(app.Services);
        var log = app.Services.GetRequiredService<ILoggerFactory>().CreateLogger("EnvCite.Platform.Web");
        if (!operatorUi)
        {
            WebLog.UiDisabled(log);
        }

        if (!operatorUi && !portal)
        {
            return app;
        }

        if (operatorUi)
        {
            app.MapGet("/", () => Results.Redirect("/ui"));
        }

        app.MapStaticAssets();
        app.MapUiAuthEndpoints();
        app.MapPortalAuthEndpoints();
        app.MapRazorComponents<App>().AddInteractiveServerRenderMode();
        return app;
    }

    /// <summary>元件展示頁（只在 Development）。</summary>
    public const string DesignGalleryPath = "/design";

    /// <summary>「找不到頁面」（<c>NotFound.razor</c> 的 <c>@page</c>）：Router 的 NotFoundPage，也是找不到網址時重新執行的目標。</summary>
    public const string NotFoundPath = "/not-found";

    private static (bool OperatorUi, bool Portal) Surfaces(IServiceProvider services)
    {
        var development = services.GetRequiredService<IHostEnvironment>().IsDevelopment();
        return (
            services.GetRequiredService<IOptions<UiOptions>>().Value.Enabled || development,
            services.GetRequiredService<IOptions<PortalOptions>>().Value.Enabled || development);
    }
}