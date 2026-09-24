using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;

using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Data;

/// <summary>
/// 租戶擁有的資料（API key、用量），由 Finbuckle 的具名 query filter 篩選至目前租戶。
/// 租戶在建構 context 時即被擷取，因此在請求中完成租戶解析之前，絕不可解析此
/// context（API key 驗證使用自己的 scope）。
/// </summary>
/// <remarks>
/// 租戶 store 不在這裡：Finbuckle 要求它的 context 衍生自 <c>EFCoreStoreDbContext</c>，
/// 故留在 Tenancy 模組（<c>TenancyDbContext</c>）。除此之外只有這一個 context。
/// </remarks>
public sealed class PlatformDbContext : MultiTenantDbContext
{
    internal const string Schema = "platform";

    public PlatformDbContext(IMultiTenantContextAccessor accessor, DbContextOptions<PlatformDbContext> options)
        : base(accessor, options)
    {
    }

    public DbSet<ApiKey> ApiKeys => Set<ApiKey>();

    public DbSet<UsageRecord> UsageRecords => Set<UsageRecord>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);
        modelBuilder.HasDefaultSchema(Schema);

        modelBuilder.Entity<ApiKey>(key =>
        {
            key.IsMultiTenant();
            key.Property(k => k.Name).HasMaxLength(100);
            key.Property(k => k.Prefix).HasMaxLength(ApiKey.PrefixLength);
            key.HasIndex(k => k.Prefix).IsUnique();
            key.HasIndex("TenantId");
        });

        modelBuilder.Entity<UsageRecord>(record =>
        {
            record.IsMultiTenant();
            record.Property(r => r.Operation).HasMaxLength(100);
            record.HasIndex("TenantId", nameof(UsageRecord.OccurredAt));
        });
    }
}