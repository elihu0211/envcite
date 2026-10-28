using System.Threading.RateLimiting;

using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.ApiAccess;

public static class ApiAccessModule
{
    /// <summary>
    /// 啟動時載入方案指派並定期重新載入。獨立註冊：產生 OpenAPI 規格的模擬 host 沒有資料庫，不該啟動它。
    /// </summary>
    public static IServiceCollection AddEntitlementRefresh(this IServiceCollection services) =>
        services.AddHostedService(sp => sp.GetRequiredService<EntitlementLoader>());

    /// <summary>註冊 ApiKey/AdminKey scheme、policy 與逐租戶的 rate limiting（API key 存於 <c>PlatformDbContext</c>）。</summary>
    public static IServiceCollection AddApiAccessModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddPlatformOptions<ApiAccessOptions, ApiAccessOptionsValidator>();
        services.AddSingleton<PlanCatalog>();
        services.AddSingleton<EntitlementLoader>();
        services.AddSingleton<IEntitlementRefresher>(sp => sp.GetRequiredService<EntitlementLoader>());
        services.AddSingleton<IApiKeyAdmin, ApiKeyAdmin>();
        services.AddSingleton<IPlanAdmin, PlanAdmin>();
        services.AddSingleton<IAuthorizationHandler, PlanHandler>();
        services.AddSingleton<IAuthorizationMiddlewareResultHandler, PaymentRequiredResultHandler>();

        services.AddAuthentication(PlatformAuth.ApiKeyScheme)
            .AddScheme<Microsoft.AspNetCore.Authentication.AuthenticationSchemeOptions, ApiKeyAuthenticationHandler>(
                PlatformAuth.ApiKeyScheme, _ => { })
            .AddScheme<AdminKeyOptions, AdminKeyAuthenticationHandler>(PlatformAuth.AdminKeyScheme, null);
        services.AddOptions<AdminKeyOptions>(PlatformAuth.AdminKeyScheme)
            .Configure<IOptions<ApiAccessOptions>>((scheme, api) => scheme.AdminKeySha256 = api.Value.AdminKeySha256);

        services.AddAuthorizationBuilder()
            .AddPolicy(PlatformAuth.TenantPolicy, p => p
                .AddAuthenticationSchemes(PlatformAuth.ApiKeyScheme)
                .RequireClaim(PlatformClaimTypes.TenantIdentifier))
            // 資料端點另外要求租戶有方案（完全付費制）；與 TenantPolicy 疊加，兩者都要過。
            .AddPolicy(PlatformAuth.PaidPolicy, p => p.AddRequirements(new PlanRequirement()))
            .AddPolicy(PlatformAuth.AdminPolicy, p => p
                .AddAuthenticationSchemes(PlatformAuth.AdminKeyScheme)
                .RequireRole("platform-admin"));

        services.AddRateLimiter(limiter =>
        {
            limiter.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
            // 以已驗證的租戶 claim 分區，絕不使用原始 request header。
            limiter.AddPolicy(PlatformAuth.PerTenantRateLimit, context =>
            {
                var tenant = context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value ?? "anonymous";
                var plans = context.RequestServices.GetRequiredService<PlanCatalog>();
                // 分區鍵含方案名稱：換方案就是新的分區、新的額度，立即生效（不然舊的限流器會一直沿用舊額度）。
                return RateLimitPartition.GetFixedWindowLimiter($"{tenant}|{plans.NameFor(tenant)}", _ =>
                {
                    // 沒有方案的租戶只會走到管理端點（資料端點在授權就 402），用限流底線。
                    var plan = plans.For(tenant) ?? PlanCatalog.UnplannedFloor;
                    return new FixedWindowRateLimiterOptions
                    {
                        PermitLimit = plan.PermitLimit,
                        Window = plan.Window,
                        QueueLimit = 0,
                    };
                });
            });
        });

        return services;
    }
}