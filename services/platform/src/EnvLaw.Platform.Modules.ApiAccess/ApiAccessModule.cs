using System.Threading.RateLimiting;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>方案允許的內容。目前只有請求速率；等有功能需要分級時，功能權限再加入。</summary>
public sealed class PlanOptions
{
    public int PermitLimit { get; set; } = 60;

    public TimeSpan Window { get; set; } = TimeSpan.FromMinutes(1);
}

/// <summary>
/// 方案與各租戶所屬的方案（<c>Platform:Plans</c>、<c>Platform:TenantPlans</c>）。在計費整合之前
/// 於設定中手動指派；未列出的租戶屬於 <see cref="DefaultPlan"/>。
/// </summary>
public sealed class PlanCatalog
{
    public const string DefaultPlan = "free";

    private readonly Dictionary<string, PlanOptions> _plans;
    private readonly Dictionary<string, string> _tenantPlans;

    public PlanCatalog(Dictionary<string, PlanOptions> plans, Dictionary<string, string> tenantPlans)
    {
        _plans = new(plans, StringComparer.Ordinal);
        _plans.TryAdd(DefaultPlan, new PlanOptions());
        _tenantPlans = tenantPlans;

        // 方案名稱打錯必須在啟動時失敗，而不是讓付費租戶默默落在免費層。
        if (_tenantPlans.FirstOrDefault(t => !_plans.ContainsKey(t.Value)) is { Key: not null } unknown)
        {
            throw new InvalidOperationException(
                $"Platform:TenantPlans:{unknown.Key} names unknown plan '{unknown.Value}'.");
        }
    }

    public PlanOptions For(string tenantIdentifier) =>
        _plans[_tenantPlans.GetValueOrDefault(tenantIdentifier, DefaultPlan)];
}

public static class ApiAccessModule
{
    /// <summary>註冊 ApiKey/AdminKey scheme、policy 與逐租戶的 rate limiting（API key 存於 <c>PlatformDbContext</c>）。</summary>
    public static IServiceCollection AddApiAccessModule(this IServiceCollection services, IConfiguration platformConfig)
    {
        services.TryAddSingleton(TimeProvider.System);

        services.AddAuthentication(PlatformAuth.ApiKeyScheme)
            .AddScheme<Microsoft.AspNetCore.Authentication.AuthenticationSchemeOptions, ApiKeyAuthenticationHandler>(
                PlatformAuth.ApiKeyScheme, _ => { })
            .AddScheme<AdminKeyOptions, AdminKeyAuthenticationHandler>(
                PlatformAuth.AdminKeyScheme, o => o.AdminKeySha256 = platformConfig["AdminKeySha256"] ?? string.Empty);

        services.AddAuthorizationBuilder()
            .AddPolicy(PlatformAuth.TenantPolicy, p => p
                .AddAuthenticationSchemes(PlatformAuth.ApiKeyScheme)
                .RequireClaim(PlatformClaimTypes.TenantIdentifier))
            .AddPolicy(PlatformAuth.AdminPolicy, p => p
                .AddAuthenticationSchemes(PlatformAuth.AdminKeyScheme)
                .RequireRole("platform-admin"));

        var plans = new PlanCatalog(
            platformConfig.GetSection("Plans").Get<Dictionary<string, PlanOptions>>() ?? [],
            platformConfig.GetSection("TenantPlans").Get<Dictionary<string, string>>() ?? []);
        services.AddRateLimiter(limiter =>
        {
            limiter.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
            // 以已驗證的租戶 claim 分區，絕不使用原始 request header。
            limiter.AddPolicy(PlatformAuth.PerTenantRateLimit, context =>
            {
                var tenant = context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value ?? "anonymous";
                return RateLimitPartition.GetFixedWindowLimiter(tenant, _ =>
                {
                    var plan = plans.For(tenant);
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