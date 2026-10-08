using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore.Stores;

using Microsoft.AspNetCore.DataProtection.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Modules.Tenancy;

/// <summary>
/// Finbuckle 的 EF Core 租戶 store，隔離在 <c>tenancy</c> schema 中。同一個 context 也存 Data Protection 金鑰：
/// 兩者都是不屬於任何租戶的基礎設施，而 <c>PlatformDbContext</c> 在租戶解析前不能建立。
/// </summary>
public sealed class TenancyDbContext(DbContextOptions<TenancyDbContext> options)
    : EFCoreStoreDbContext<TenantInfo>(options), IDataProtectionKeyContext
{
    internal const string Schema = "tenancy";

    public DbSet<DataProtectionKey> DataProtectionKeys { get; set; } = null!;

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);
        modelBuilder.HasDefaultSchema(Schema);
    }
}