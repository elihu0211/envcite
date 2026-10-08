using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

namespace EnvLaw.Platform.Web.Auth;

/// <summary>後台的登入：Google 單點登入簽發 cookie，白名單決定誰能進。</summary>
internal static class WebAuthModule
{
    /// <summary>
    /// 預設 scheme 仍是 ApiKey，後台走自己的 cookie（見 <c>UsePlatformWebAuthentication</c>）。
    /// <paramref name="uiConfig"/> 是 <c>Platform:Ui</c>；Google 憑證缺少時，非 Development 直接啟動失敗。
    /// </summary>
    public static IServiceCollection AddWebAuth(
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

        return services;
    }
}