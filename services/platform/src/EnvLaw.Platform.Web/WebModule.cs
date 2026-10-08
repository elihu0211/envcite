using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;
using EnvLaw.Platform.Web.Components;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

using MudBlazor.Services;

namespace EnvLaw.Platform.Web;

public static class WebModule
{
    /// <summary>
    /// 營運者後台（Blazor Web App，全站 Interactive Server）：Cookie + Google 單點登入 + email 白名單。
    /// <paramref name="uiConfig"/> 是 <c>Platform:Ui</c>；Google 憑證缺少時，非 Development 直接啟動失敗。
    /// </summary>
    public static IServiceCollection AddPlatformWeb(
        this IServiceCollection services, IConfiguration uiConfig, IHostEnvironment environment)
    {
        var clientId = uiConfig["Google:ClientId"];
        var clientSecret = uiConfig["Google:ClientSecret"];
        if ((string.IsNullOrEmpty(clientId) || string.IsNullOrEmpty(clientSecret)) && !environment.IsDevelopment())
        {
            throw new InvalidOperationException(
                "Platform:Ui:Google:ClientId and Platform:Ui:Google:ClientSecret are not configured.");
        }

        services.Configure<UiOptions>(uiConfig);
        services.AddSingleton<OperatorAllowlist>();
        services.AddSingleton<IAuthorizationHandler, OperatorHandler>();

        // 不指定預設 scheme：預設仍是 ApiKey，後台走自己的 cookie（見 UsePlatformWebAuthentication）。
        services.AddAuthentication()
            .AddCookie(PlatformAuth.UiCookieScheme, cookie =>
            {
                cookie.Cookie.Name = "EnvLaw.Ui";
                cookie.Cookie.HttpOnly = true;
                cookie.Cookie.SameSite = SameSiteMode.Lax;
                cookie.Cookie.SecurePolicy = CookieSecurePolicy.Always;
                cookie.LoginPath = UiEndpoints.LoginPath;
                cookie.AccessDeniedPath = UiEndpoints.DeniedPath;
                cookie.ExpireTimeSpan = TimeSpan.FromHours(8);
                cookie.SlidingExpiration = true;
            })
            .AddGoogle(PlatformAuth.UiGoogleScheme, google =>
            {
                // Development 缺憑證時用占位值，讓不碰後台的本機開發照常啟動；真的登入時會被 Google 拒絕。
                google.ClientId = string.IsNullOrEmpty(clientId) ? "unset" : clientId;
                google.ClientSecret = string.IsNullOrEmpty(clientSecret) ? "unset" : clientSecret;
                google.SignInScheme = PlatformAuth.UiCookieScheme;
                google.SaveTokens = false;
                google.ClaimActions.MapJsonKey(OperatorAllowlist.EmailVerifiedClaim, "email_verified");
                google.ClaimActions.MapJsonKey(OperatorAllowlist.HostedDomainClaim, "hd");
                google.Events.OnTicketReceived = context =>
                {
                    // 不在白名單就不簽發 cookie。
                    var allowlist = context.HttpContext.RequestServices.GetRequiredService<OperatorAllowlist>();
                    if (context.Principal is null || !allowlist.IsAllowed(context.Principal))
                    {
                        context.HandleResponse();
                        context.Response.Redirect(UiEndpoints.DeniedPath);
                    }

                    return Task.CompletedTask;
                };
            });

        services.AddAuthorizationBuilder()
            .AddPolicy(PlatformAuth.UiPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.UiCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new OperatorRequirement()));

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
        app.MapStaticAssets();
        app.MapUiAuthEndpoints();
        app.MapRazorComponents<App>().AddInteractiveServerRenderMode();
        return app;
    }
}