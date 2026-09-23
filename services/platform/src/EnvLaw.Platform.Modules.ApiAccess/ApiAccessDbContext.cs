using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;

using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>
/// API keys, filtered to the current tenant by Finbuckle's named query filter.
/// The tenant is captured when the context is constructed, so never resolve this
/// context before tenant resolution in a request (authentication uses its own scope).
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