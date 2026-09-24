using System.Threading.RateLimiting;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>What a plan allows. Only the request rate for now; entitlements join it when a feature is gated.</summary>
public sealed class PlanOptions
{
    public int PermitLimit { get; set; } = 60;

    public TimeSpan Window { get; set; } = TimeSpan.FromMinutes(1);
}

/// <summary>
/// Plans and who is on which (<c>Platform:Plans</c>, <c>Platform:TenantPlans</c>). Assigned by hand in
/// configuration until billing exists; a tenant not listed is on <see cref="DefaultPlan"/>.
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

        // A typo in a plan name must fail at startup, not quietly put a paying tenant on the free tier.
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
    /// <summary>Registers API key storage, the ApiKey/AdminKey schemes, policies and per-tenant rate limiting.</summary>
    public static IServiceCollection AddApiAccessModule(
        this IServiceCollection services, string connectionString, IConfiguration platformConfig)
    {
        services.TryAddSingleton(TimeProvider.System);

        services.AddDbContext<ApiAccessDbContext>(options => options.UseNpgsql(
            connectionString,
            npgsql => npgsql.MigrationsHistoryTable("__EFMigrationsHistory", ApiAccessDbContext.Schema)));

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
            // Partition on the verified tenant claim, never on raw request headers.
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