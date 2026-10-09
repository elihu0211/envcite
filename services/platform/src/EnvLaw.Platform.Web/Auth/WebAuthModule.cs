using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Web.Auth;

/// <summary>後台的登入：營運者帳號（Identity，見 Members 模組）簽發 cookie，每個請求都向資料庫重查帳號。</summary>
internal static class WebAuthModule
{
    /// <summary>
    /// 預設 scheme 仍是 ApiKey，後台走自己的 cookie（見 <c>UsePlatformWebAuthentication</c>）。
    /// 設定來自 <see cref="UiOptions"/>（<c>Platform:Ui</c>），啟動時驗證。
    /// </summary>
    public static IServiceCollection AddWebAuth(this IServiceCollection services)
    {
        services.AddPlatformOptions<UiOptions>();
        services.AddSingleton<IValidateOptions<UiOptions>, UiOptionsValidator>();
        services.AddPlatformOptions<PortalOptions>();
        services.AddSingleton<IValidateOptions<PortalOptions>, PortalOptionsValidator>();
        services.AddRateLimiter(PortalRateLimit.Configure);
        services.AddSingleton<IAuthorizationHandler, OperatorHandler>();

        services.AddAuthentication()
            .AddCookie(PlatformAuth.UiCookieScheme, cookie =>
            {
                cookie.Cookie.Name = "EnvLaw.Ui";
                cookie.Cookie.HttpOnly = true;
                cookie.Cookie.SameSite = SameSiteMode.Lax;
                cookie.Cookie.SecurePolicy = CookieSecurePolicy.Always;
                cookie.LoginPath = UiEndpoints.LoginPath;
                cookie.ExpireTimeSpan = TimeSpan.FromHours(8);
                cookie.SlidingExpiration = true;
                cookie.Events.OnRedirectToAccessDenied = UiRedirects.ToAccessDenied;
            });

        // 三層，由寬到嚴；每一層都以 OperatorHandler 重查資料庫。
        services.AddAuthorizationBuilder()
            .AddPolicy(PlatformAuth.UiSessionPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.UiCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new OperatorRequirement(AllowPasswordChangePending: true, AllowEnrollmentPending: true)))
            .AddPolicy(PlatformAuth.UiEnrollmentPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.UiCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new OperatorRequirement(AllowPasswordChangePending: false, AllowEnrollmentPending: true)))
            .AddPolicy(PlatformAuth.UiPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.UiCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new OperatorRequirement(AllowPasswordChangePending: false, AllowEnrollmentPending: false)));

        return services;
    }
}

/// <summary>
/// 已登入但不符時導去哪一頁：還必須先改密碼 → 改密碼頁；密碼改過了、但還沒註冊驗證器 → 註冊頁（先改密碼再註冊，
/// 驗證器才不會綁在一個營運者金鑰持有人知道的密碼後面）；否則（帳號被停用、被刪、密碼或驗證器被重設）→ 說明頁。
/// 導向看 cookie 裡的 claim，但<b>不能導到自己</b>：被拒的就是那一頁（例如帳號在改密碼途中被停用），
/// 再導一次只會無限轉址，所以那種情況落到說明頁。
/// </summary>
internal static class UiRedirects
{
    public static Task ToAccessDenied(RedirectContext<CookieAuthenticationOptions> context)
    {
        var user = context.HttpContext.User;
        var here = context.Request.Path;
        var passwordPending = user.FindFirst(PlatformClaimTypes.PasswordChangeRequired) is not null;
        var enrollmentPending = user.FindFirst(PlatformClaimTypes.MfaEnrollmentRequired) is not null;

        context.Response.Redirect(
            passwordPending && !here.StartsWithSegments(UiEndpoints.PasswordPath) ? UiEndpoints.PasswordPath
            : !passwordPending && enrollmentPending && !here.StartsWithSegments(UiEndpoints.SecurityPath) ? UiEndpoints.SecurityPath
            : UiEndpoints.DeniedPath);
        return Task.CompletedTask;
    }
}