using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;
using EnvCite.ServiceDefaults;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Identity.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// <c>Platform:Portal</c> 區段中屬於成員模型的部分（<c>Enabled</c> 與限流屬於 Web）。
/// </summary>
public sealed class MembersOptions : ISectionOptions
{
    public static string SectionName => "Platform:Portal";

    /// <summary>
    /// 是否強制 Owner 與 Admin 啟用兩步驟驗證。預設 false：兩步驟驗證由使用者自己決定是否開啟；
    /// 設為 true 時，沒啟用的人登入後只進得了註冊頁。
    /// </summary>
    public bool RequireMfaForAdmins { get; set; }
}

public static class MembersModule
{
    /// <summary>密碼最短長度。依 NIST SP 800-63B：長度比組成規則（大小寫、符號）重要，不強制組成。</summary>
    public const int MinimumPasswordLength = 12;

    /// <summary>連續失敗幾次後鎖定，與鎖定多久。</summary>
    public const int MaxFailedAccessAttempts = 5;

    public static readonly TimeSpan LockoutDuration = TimeSpan.FromMinutes(15);

    /// <summary>
    /// 客戶入口的成員：ASP.NET Core Identity 的使用者與密碼雜湊（PBKDF2），存在 <c>PlatformDbContext</c>，
    /// 隨 Finbuckle 屬於單一租戶。登入 cookie 是自己的 scheme，帶 tenant、使用者 Id、角色 claim；
    /// 授權只讀 claim。沒有外部登入、寄信與邀請（見 07-01）。
    /// </summary>
    public static IServiceCollection AddMembersModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddSectionOptions<MembersOptions>();

        services.AddIdentityCore<ApplicationUser>(ConfigureIdentity)
            .AddEntityFrameworkStores<PlatformDbContext>()
            .AddSignInManager()
            .AddTokenProvider<AuthenticatorTokenProvider<ApplicationUser>>(TokenOptions.DefaultAuthenticatorProvider)
            .AddClaimsPrincipalFactory<PortalClaimsPrincipalFactory>();

        services.AddAuthentication()
            .AddCookie(IdentityConstants.TwoFactorUserIdScheme, cookie =>
            {
                // Identity 原生的「密碼已通過、等第二步」cookie：五分鐘、不滑動，不是登入 cookie（入口的授權不認這個 scheme）。
                cookie.Cookie.Name = "EnvCite.PortalMfa";
                cookie.Cookie.HttpOnly = true;
                cookie.Cookie.SameSite = SameSiteMode.Lax;
                cookie.Cookie.SecurePolicy = CookieSecurePolicy.Always;
                cookie.ExpireTimeSpan = TimeSpan.FromMinutes(5);
                cookie.SlidingExpiration = false;
            })
            .AddCookie(PlatformAuth.PortalCookieScheme, cookie =>
            {
                cookie.Cookie.Name = "EnvCite.Portal";
                cookie.Cookie.HttpOnly = true;
                cookie.Cookie.SameSite = SameSiteMode.Lax;
                cookie.Cookie.SecurePolicy = CookieSecurePolicy.Always;
                cookie.ExpireTimeSpan = TimeSpan.FromHours(8);
                cookie.SlidingExpiration = true;
                cookie.Events.OnValidatePrincipal = PortalSessionValidator.ValidateAsync;
                cookie.Events.OnRedirectToLogin = PortalRedirects.ToLogin;
                cookie.Events.OnRedirectToAccessDenied = PortalRedirects.ToAccessDenied;
            });

        services.AddSingleton<IAuthorizationHandler, PortalMemberHandler>();
        services.AddAuthorizationBuilder()
            .AddPolicy(PlatformAuth.PortalSessionPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.PortalCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new PortalMemberRequirement(AllowPasswordChangePending: true, AllowMfaEnrollmentPending: true)))
            .AddPolicy(PlatformAuth.PortalEnrollmentPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.PortalCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new PortalMemberRequirement(AllowPasswordChangePending: false, AllowMfaEnrollmentPending: true)))
            .AddPolicy(PlatformAuth.PortalPolicy, policy => policy
                .AddAuthenticationSchemes(PlatformAuth.PortalCookieScheme)
                .RequireAuthenticatedUser()
                .AddRequirements(new PortalMemberRequirement(AllowPasswordChangePending: false, AllowMfaEnrollmentPending: false)));

        services.AddHttpContextAccessor();
        services.AddScoped<TotpVerifier>();
        services.AddScoped<IPortalAccount, PortalAccount>();
        services.AddScoped<IPortalTwoFactor, PortalTwoFactor>();
        services.AddScoped<MemberCore>();
        services.AddScoped<IMemberSelfService, MemberSelfService>();
        services.AddSingleton<IMemberAdmin, MemberAdmin>();

        services.AddOperatorAccounts();

        return services;
    }

    /// <summary>
    /// 密碼與鎖定規則。<c>IdentityOptions</c> 是全域的一份，客戶入口的成員與後台的營運者本來就適用同一套
    /// （最短 12 字元、不強制組成、5 次失敗鎖 15 分鐘），所以兩邊共用這個方法。
    /// </summary>
    internal static void ConfigureIdentity(IdentityOptions options)
    {
        options.User.RequireUniqueEmail = true;
        options.Password.RequiredLength = MinimumPasswordLength;
        options.Password.RequireDigit = false;
        options.Password.RequireLowercase = false;
        options.Password.RequireUppercase = false;
        options.Password.RequireNonAlphanumeric = false;
        options.Lockout.AllowedForNewUsers = true;
        options.Lockout.MaxFailedAccessAttempts = MaxFailedAccessAttempts;
        options.Lockout.DefaultLockoutTimeSpan = LockoutDuration;
    }

    /// <summary>
    /// 後台的營運者：同一套 Identity，<b>另一個 user store</b>（<c>OperatorUser</c>，表 <c>Operators</c>，不隨租戶）。
    /// <c>AddEntityFrameworkStores</c> 會從 <c>PlatformDbContext</c> 的基底推出成員的 claim／login／token 型別，
    /// 套給營運者就會讀寫成員的表，所以 store 自己指定型別。登入 cookie 與暫存憑證的 scheme 在 Web／這裡各自註冊。
    /// </summary>
    private static void AddOperatorAccounts(this IServiceCollection services)
    {
        services.AddIdentityCore<OperatorUser>(ConfigureIdentity);
        services.AddScoped<IUserStore<OperatorUser>,
            UserOnlyStore<OperatorUser, PlatformDbContext, Guid, IdentityUserClaim<Guid>, IdentityUserLogin<Guid>, IdentityUserToken<Guid>>>();
        services.AddScoped<IUserClaimsPrincipalFactory<OperatorUser>, OperatorClaimsPrincipalFactory>();

        services.AddAuthentication()
            .AddCookie(OperatorMfaPendingCookie.Scheme, cookie =>
            {
                // 「密碼已通過、等第二步」：五分鐘、不滑動，不是登入 cookie（後台的授權不認這個 scheme）。
                cookie.Cookie.Name = "EnvCite.UiMfa";
                cookie.Cookie.HttpOnly = true;
                cookie.Cookie.SameSite = SameSiteMode.Lax;
                cookie.Cookie.SecurePolicy = CookieSecurePolicy.Always;
                cookie.ExpireTimeSpan = OperatorMfaPendingCookie.Lifetime;
                cookie.SlidingExpiration = false;
            });

        services.AddScoped<OperatorMfaPendingCookie>();
        services.AddScoped<OperatorAccount>();
        services.AddScoped<IOperatorAccount>(provider => provider.GetRequiredService<OperatorAccount>());
        services.AddScoped<IOperatorTwoFactor, OperatorTwoFactor>();
        services.AddSingleton<IOperatorSessions, OperatorSessions>();
        services.AddSingleton<IOperatorAdmin, OperatorAdmin>();
    }
}