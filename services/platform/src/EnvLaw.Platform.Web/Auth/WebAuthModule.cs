using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Google;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Web.Auth;

/// <summary>後台的登入：Google 單點登入簽發 cookie，白名單決定誰能進。</summary>
internal static class WebAuthModule
{
    /// <summary>
    /// 預設 scheme 仍是 ApiKey，後台走自己的 cookie（見 <c>UsePlatformWebAuthentication</c>）。
    /// 設定來自 <see cref="UiOptions"/>（<c>Platform:Ui</c>），啟動時驗證。
    /// </summary>
    public static IServiceCollection AddWebAuth(this IServiceCollection services)
    {
        services.AddPlatformOptions<UiOptions>();
        services.AddPlatformOptions<PortalOptions>();
        services.AddSingleton<IValidateOptions<PortalOptions>, PortalOptionsValidator>();
        services.AddRateLimiter(PortalRateLimit.Configure);
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
                google.SignInScheme = PlatformAuth.UiCookieScheme;
                google.SaveTokens = false;
                google.ClaimActions.MapJsonKey(OperatorAllowlist.EmailVerifiedClaim, "email_verified");
                google.ClaimActions.MapJsonKey(OperatorAllowlist.HostedDomainClaim, "hd");
                // 不在白名單就不簽發 cookie；登入與被拒絕都留稽核（見 OperatorSignIn）。
                google.Events.OnTicketReceived = OperatorSignIn.HandleAsync;
            });

        // 憑證在用到時才自選項取得。Development 缺憑證用占位值，讓不碰後台的本機開發照常啟動。
        services.AddOptions<GoogleOptions>(PlatformAuth.UiGoogleScheme)
            .Configure<IOptions<UiOptions>>((google, ui) =>
            {
                google.ClientId = string.IsNullOrEmpty(ui.Value.Google.ClientId) ? "unset" : ui.Value.Google.ClientId;
                google.ClientSecret = string.IsNullOrEmpty(ui.Value.Google.ClientSecret) ? "unset" : ui.Value.Google.ClientSecret;
            });

        services.AddAuthorizationBuilder()
            .AddPolicy(PlatformAuth.UiPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.UiCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new OperatorRequirement()));

        return services;
    }
}