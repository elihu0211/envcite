using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;

using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>
/// API key，由 Finbuckle 的具名 query filter 篩選至目前租戶。
/// 租戶在建構 context 時即被擷取，因此在請求中完成租戶解析之前，絕不可解析此
/// context（驗證流程使用自己的 scope）。
/// </summary>
public sealed class ApiAccessDbContext : MultiTenantDbContext
{
    internal const string Schema = "api_access";

    public ApiAccessDbContext(IMultiTenantContextAccessor accessor, DbContextOptions<ApiAccessDbContext> options)
        : base(accessor, options)
    {
    }

    public DbSet<ApiKey> ApiKeys => Set<ApiKey>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);
        modelBuilder.HasDefaultSchema(Schema);

        modelBuilder.Entity<ApiKey>(key =>
        {
            key.IsMultiTenant();
            key.Property(k => k.Name).HasMaxLength(100);
            key.Property(k => k.Prefix).HasMaxLength(ApiKeyFormat.PrefixLength);
            key.HasIndex(k => k.Prefix).IsUnique();
            key.HasIndex("TenantId");
        });
    }
}