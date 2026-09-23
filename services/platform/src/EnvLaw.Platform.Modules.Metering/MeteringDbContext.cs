using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;

using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Modules.Metering;

/// <summary>Usage records, filtered to the current tenant.</summary>
public sealed class MeteringDbContext : MultiTenantDbContext
{
    internal const string Schema = "metering";

    public MeteringDbContext(IMultiTenantContextAccessor accessor, DbContextOptions<MeteringDbContext> options)
        : base(accessor, options)
    {
    }

    public DbSet<UsageRecord> UsageRecords => Set<UsageRecord>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);
        modelBuilder.HasDefaultSchema(Schema);

        modelBuilder.Entity<UsageRecord>(record =>
        {
            record.IsMultiTenant();
            record.Property(r => r.Operation).HasMaxLength(100);
            record.HasIndex("TenantId", nameof(UsageRecord.OccurredAt));
        });
    }
}