using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore.Stores;

using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Modules.Tenancy;

/// <summary>Finbuckle 的 EF Core 租戶 store，隔離在 <c>tenancy</c> schema 中。</summary>
public sealed class TenancyDbContext(DbContextOptions<TenancyDbContext> options)
    : EFCoreStoreDbContext<TenantInfo>(options)
{
    internal const string Schema = "tenancy";

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);
        modelBuilder.HasDefaultSchema(Schema);
    }
}