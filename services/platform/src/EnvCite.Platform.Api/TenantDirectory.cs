using EnvCite.Platform.Modules.ApiAccess;

using Finbuckle.MultiTenant.Abstractions;

namespace EnvCite.Platform.Api;

/// <summary>租戶 store（Tenancy 模組）加方案目錄（ApiAccess 模組），由組合根接起來給營運後台。</summary>
internal sealed class TenantDirectory(IServiceScopeFactory scopes, PlanCatalog plans) : ITenantDirectory
{
    public async Task<IReadOnlyList<TenantSummary>> ListAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();

        var consented = await ConsentedAsync(scope, cancellationToken);
        var tenants = await store.GetAllAsync();
        return tenants
            .Select(t => Summary(t, consented))
            .OrderBy(t => t.Identifier, StringComparer.Ordinal)
            .ToList();
    }

    public async Task<TenantSummary?> FindAsync(string tenantId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();

        return await store.GetAsync(tenantId) is { } tenant ? Summary(tenant, await ConsentedAsync(scope, cancellationToken)) : null;
    }

    public async Task<TenantSummary?> FindByIdentifierAsync(string tenantIdentifier, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();

        return await store.GetByIdentifierAsync(tenantIdentifier) is { } tenant ? Summary(tenant, await ConsentedAsync(scope, cancellationToken)) : null;
    }

    /// <summary>同意 AI 回答的租戶 Id（權益那一列的旗標；那張表不是租戶實體，所以可以不帶租戶讀）。</summary>
    private static async Task<HashSet<string>> ConsentedAsync(AsyncServiceScope scope, CancellationToken cancellationToken) =>
        [.. await scope.ServiceProvider.GetRequiredService<PlatformDbContext>().Set<TenantEntitlement>()
            .Where(e => e.AiAnswerEnabled).Select(e => e.TenantId).ToListAsync(cancellationToken)];

    private TenantSummary Summary(TenantInfo t, HashSet<string> consented)
    {
        var plan = plans.NameFor(t.Identifier ?? string.Empty);
        return new(t.Id ?? string.Empty, t.Identifier ?? string.Empty, t.Name, plan, plan is not null && t.Id is not null && consented.Contains(t.Id));
    }
}