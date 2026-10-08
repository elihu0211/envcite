using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;
using Finbuckle.MultiTenant.Extensions;

using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.Tenancy;

public static class TenancyModule
{
    /// <summary>
    /// 註冊租戶 store，並從 API key 驗證所設定的
    /// <see cref="PlatformClaimTypes.TenantIdentifier"/> claim 解析目前租戶。
    /// 必須先 <c>UseAuthentication()</c> 再 <c>UseMultiTenant()</c>。
    /// </summary>
    public static IServiceCollection AddTenancyModule(this IServiceCollection services)
    {
        services.AddPlatformDatabaseOptions();
        services.AddDbContext<TenancyDbContext>((provider, options) =>
            Configure(options, provider.GetRequiredService<IOptions<DatabaseOptions>>().Value.Platform));

        services.AddMultiTenant<TenantInfo>()
            .WithEFCoreStore<TenancyDbContext, TenantInfo>()
            .WithDelegateStrategy<HttpContext, TenantInfo>(context =>
                Task.FromResult(context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value));

        services.AddSingleton<ITenantProvisioner, TenantProvisioner>();

        return services;
    }

    /// <summary>
    /// 把 Data Protection 金鑰存進資料庫，所有副本共用同一份。沒有這個，每個副本各自產生金鑰：
    /// A 副本簽的登入 cookie 與 antiforgery token，B 副本解不開（登入失效、表單被拒）。
    /// 固定 ApplicationName，金鑰環才不會因安裝路徑不同而分開。
    /// </summary>
    /// <remarks>金鑰在資料庫裡是明文 XML；保護資料庫就是保護金鑰。需要靜態加密時加 <c>ProtectKeysWith…</c>。</remarks>
    public static IServiceCollection AddPlatformDataProtection(this IServiceCollection services)
    {
        services.AddDataProtection()
            .SetApplicationName("env-law-platform")
            .PersistKeysToDbContext<TenancyDbContext>();
        return services;
    }

    /// <summary>應用程式與 design-time factory 共用，理由同 <c>PlatformDataModule.Configure</c>。</summary>
    internal static void Configure(DbContextOptionsBuilder options, string connectionString) =>
        options.UseNpgsql(
            connectionString,
            npgsql => npgsql.MigrationsHistoryTable("__EFMigrationsHistory", TenancyDbContext.Schema));
}