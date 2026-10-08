using EnvLaw.Platform.Modules.ApiAccess;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

namespace EnvLaw.Platform.Api;

/// <summary>租戶 store（Tenancy 模組）加方案目錄（ApiAccess 模組），由組合根接起來給營運後台。</summary>
internal sealed class TenantDirectory(IServiceScopeFactory scopes) : ITenantDirectory
{
    public async Task<IReadOnlyList<TenantSummary>> ListAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        var plans = scope.ServiceProvider.GetRequiredService<PlanCatalog>();

        var tenants = await store.GetAllAsync();
        return tenants
            .Select(t => Summary(t, plans))
            .OrderBy(t => t.Identifier, StringComparer.Ordinal)
            .ToList();
    }

    public async Task<TenantSummary?> FindAsync(string tenantId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        var plans = scope.ServiceProvider.GetRequiredService<PlanCatalog>();

        return await store.GetAsync(tenantId) is { } tenant ? Summary(tenant, plans) : null;
    }

    private static TenantSummary Summary(TenantInfo t, PlanCatalog plans) =>
        new(t.Id ?? string.Empty, t.Identifier ?? string.Empty, t.Name, plans.NameFor(t.Identifier ?? string.Empty));
}