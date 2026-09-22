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

public sealed class RateLimitOptions
{
    public int PermitLimit { get; set; } = 60;

    public TimeSpan Window { get; set; } = TimeSpan.FromMinutes(1);
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

        var rateLimit = platformConfig.GetSection("RateLimit").Get<RateLimitOptions>() ?? new RateLimitOptions();
        services.AddRateLimiter(limiter =>
        {
            limiter.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
            // Partition on the verified tenant claim, never on raw request headers.
            limiter.AddPolicy(PlatformAuth.PerTenantRateLimit, context => RateLimitPartition.GetFixedWindowLimiter(
                context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value ?? "anonymous",
                _ => new FixedWindowRateLimiterOptions
                {
                    PermitLimit = rateLimit.PermitLimit,
                    Window = rateLimit.Window,
                    QueueLimit = 0,
                }));
        });

        return services;
    }
}