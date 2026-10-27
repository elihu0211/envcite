using System.Security.Cryptography.X509Certificates;

using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;
using Finbuckle.MultiTenant.Extensions;

using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Tenancy;

public static class TenancyModule
{
    /// <summary>
    /// 註冊租戶 store，並解析目前租戶：客戶入口取自路徑（<c>/portal/{租戶}/…</c>），其餘從 API key 驗證所設定的
    /// <see cref="PlatformClaimTypes.TenantIdentifier"/> claim。
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
                // 客戶入口的租戶在路徑裡（登入前就要知道），而且路徑優先於 cookie 的 claim：
                // 別的租戶的 cookie 帶到這個路徑，租戶仍是這個路徑的租戶，授權再發現不符。
                Task.FromResult(PortalPaths.TenantFrom(context.Request.Path)
                    ?? context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value));

        services.AddSingleton<ITenantProvisioner, TenantProvisioner>();

        return services;
    }

    /// <summary>
    /// 把 Data Protection 金鑰存進資料庫，所有副本共用同一份。沒有這個，每個副本各自產生金鑰：
    /// A 副本簽的登入 cookie 與 antiforgery token，B 副本解不開（登入失效、表單被拒）。
    /// 固定 ApplicationName，金鑰環才不會因安裝路徑不同而分開。
    /// </summary>
    /// <remarks>
    /// 預設金鑰在資料庫裡是明文 XML；保護資料庫就是保護金鑰。設了 <c>Platform:DataProtection:CertificatePath</c> 與
    /// <c>CertificatePassword</c>（見 <see cref="DataProtectionCertificateOptions"/>）則新產生的金鑰以該憑證加密，
    /// 之前寫入的明文金鑰照常可讀（不改寫）。
    /// </remarks>
    public static IServiceCollection AddPlatformDataProtection(this IServiceCollection services, IConfiguration configuration)
    {
        // 校驗（成對、檔案存在）走一般的選項，啟動時失敗並指名鍵。
        services.AddPlatformOptions<DataProtectionCertificateOptions>();
        services.AddSingleton<IValidateOptions<DataProtectionCertificateOptions>, DataProtectionCertificateOptionsValidator>();

        var protection = services.AddDataProtection()
            .SetApplicationName("envcite-platform")
            .PersistKeysToDbContext<TenancyDbContext>();

        // 這是唯一在註冊時就要讀設定的地方：框架登記「解密用憑證」的選項是內部型別，只有
        // ProtectKeysWithCertificate(憑證物件) 能設，所以憑證必須在這裡載入。不合格的設定留給上面的校驗報錯。
        var options = configuration.GetSection(DataProtectionCertificateOptions.SectionName).Get<DataProtectionCertificateOptions>();
        if (options is { IsConfigured: true } && File.Exists(options.CertificatePath))
        {
            protection.ProtectKeysWithCertificate(
                X509CertificateLoader.LoadPkcs12FromFile(options.CertificatePath, options.CertificatePassword));
        }

        return services;
    }

    /// <summary>應用程式與 design-time factory 共用，理由同 <c>PlatformDataModule.Configure</c>。</summary>
    internal static void Configure(DbContextOptionsBuilder options, string connectionString) =>
        options.UseNpgsql(
            connectionString,
            npgsql => npgsql.MigrationsHistoryTable("__EFMigrationsHistory", TenancyDbContext.Schema));
}