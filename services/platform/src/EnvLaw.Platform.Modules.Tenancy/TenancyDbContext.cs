using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore.Stores;

using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Modules.Tenancy;

/// <summary>Finbuckle EF Core tenant store, isolated in the <c>tenancy</c> schema.</summary>
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