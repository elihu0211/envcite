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
    /// 註冊租戶 store，並從 API key 驗證所設定的
    /// <see cref="PlatformClaimTypes.TenantIdentifier"/> claim 解析目前租戶。
    /// 必須先 <c>UseAuthentication()</c> 再 <c>UseMultiTenant()</c>。
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