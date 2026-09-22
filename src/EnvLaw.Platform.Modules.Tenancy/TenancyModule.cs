using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;
using Finbuckle.MultiTenant.Extensions;

using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Tenancy;

public static class TenancyModule
{
    /// <summary>
    /// Registers the tenant store and resolves the current tenant from the
    /// <see cref="PlatformClaimTypes.TenantIdentifier"/> claim set by API key authentication.
    /// Requires <c>UseAuthentication()</c> before <c>UseMultiTenant()</c>.
    /// </summary>
    public static IServiceCollection AddTenancyModule(this IServiceCollection services, string connectionString)
    {
        services.AddDbContext<TenancyDbContext>(options => options.UseNpgsql(
            connectionString,
            npgsql => npgsql.MigrationsHistoryTable("__EFMigrationsHistory", TenancyDbContext.Schema)));

        services.AddMultiTenant<TenantInfo>()
            .WithEFCoreStore<TenancyDbContext, TenantInfo>()
            .WithDelegateStrategy<HttpContext, TenantInfo>(context =>
                Task.FromResult(context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value));

        return services;
    }
}