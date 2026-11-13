using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>
/// 營運者針對個別租戶動態開關功能模組。覆寫列與稽核事件在同一個交易裡寫入；
/// 寫成功後本機快照立即更新，其他副本在下一次重新載入內看到。
/// </summary>
internal sealed class ModuleAdmin(
    IServiceScopeFactory scopes,
    PlanCatalog plans,
    TenantModuleCatalog modules,
    TimeProvider time) : IModuleAdmin
{
    public async Task<AdminOutcome<IReadOnlyList<TenantModuleStatus>>> ListAsync(string tenantId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        if (await store.GetAsync(tenantId) is not { Identifier: { } identifier })
            return AdminOutcome.NotFound<IReadOnlyList<TenantModuleStatus>>(OutcomeCodes.TenantNotFound);

        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        var overrides = await db.Set<TenantModuleOverride>()
            .AsNoTracking()
            .Where(o => o.TenantId == tenantId)
            .ToDictionaryAsync(o => o.ModuleKey, o => o.Enabled, StringComparer.Ordinal, cancellationToken);

        return AdminOutcome.Ok<IReadOnlyList<TenantModuleStatus>>(BuildStatuses(identifier, overrides));
    }

    public async Task<AdminOutcome<TenantModuleStatus>> SetAsync(
        ClaimsPrincipal actor, string tenantId, string moduleKey, bool? enabled, CancellationToken cancellationToken)
    {
        if (ModuleCatalog.Find(moduleKey) is not { } descriptor)
            return AdminOutcome.Invalid<TenantModuleStatus>(OutcomeCodes.ModuleUnknown, moduleKey);

        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        if (await store.GetAsync(tenantId) is not { Identifier: { } identifier } tenant)
            return AdminOutcome.NotFound<TenantModuleStatus>(OutcomeCodes.TenantNotFound);

        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        var entitlement = await db.Set<TenantEntitlement>().SingleOrDefaultAsync(e => e.TenantId == tenant.Id, cancellationToken);
        if (entitlement is null)
            return AdminOutcome.Invalid<TenantModuleStatus>(OutcomeCodes.PlanRequiredForModule);

        var overrides = await db.Set<TenantModuleOverride>()
            .Where(o => o.TenantId == tenant.Id)
            .ToDictionaryAsync(o => o.ModuleKey, StringComparer.Ordinal, cancellationToken);

        // 若要開啟，檢查前置依賴是否已在目前租戶啟用。
        if (enabled == true)
        {
            var effectiveBefore = modules.EnabledModules(identifier);
            var unmet = descriptor.Dependencies.FirstOrDefault(dep => !effectiveBefore.Contains(dep));
            if (unmet is not null)
                return AdminOutcome.Invalid<TenantModuleStatus>(OutcomeCodes.ModuleDependencyMissing, moduleKey, unmet);
        }

        var hadOverride = overrides.TryGetValue(moduleKey, out var existing);
        var changed = false;
        var now = time.GetUtcNow();

        if (enabled is null)
        {
            if (hadOverride)
            {
                db.Remove(existing!);
                overrides.Remove(moduleKey);
                changed = true;
            }
        }
        else if (!hadOverride || existing!.Enabled != enabled.Value)
        {
            if (!hadOverride)
            {
                var created = new TenantModuleOverride
                {
                    TenantId = tenant.Id!,
                    ModuleKey = moduleKey,
                    Enabled = enabled.Value,
                    UpdatedAt = now,
                };
                db.Add(created);
                overrides[moduleKey] = created;
            }
            else
            {
                existing!.Enabled = enabled.Value;
                existing.UpdatedAt = now;
            }

            changed = true;
        }

        if (changed)
        {
            if (moduleKey == ModuleKeys.GroundedAnswer)
            {
                entitlement.AiAnswerEnabled = enabled == true;
                entitlement.UpdatedAt = now;
            }

            var action = enabled switch
            {
                true => AuditActions.TenantModuleEnabled,
                false => AuditActions.TenantModuleDisabled,
                null => AuditActions.TenantModuleReset,
            };

            db.StageAudit(
                actor,
                new AuditEntry(
                    action,
                    tenant.Id,
                    "tenant",
                    tenant.Id,
                    new Dictionary<string, object?>
                    {
                        ["identifier"] = identifier,
                        ["module"] = moduleKey,
                        ["enabled"] = enabled,
                    }),
                time);

            await db.SaveChangesAsync(cancellationToken);
            modules.SetOverride(identifier, moduleKey, enabled);
        }

        var statuses = BuildStatuses(identifier, overrides.ToDictionary(kv => kv.Key, kv => kv.Value.Enabled, StringComparer.Ordinal));
        return AdminOutcome.Ok(statuses.Single(s => s.Key == moduleKey));
    }

    private List<TenantModuleStatus> BuildStatuses(string identifier, Dictionary<string, bool> overrides)
    {
        var plan = plans.For(identifier);
        var defaults = plan is null ? [] : TenantModuleCatalog.PlanDefaults(plan).ToHashSet(StringComparer.Ordinal);
        var effective = modules.EnabledModules(identifier);

        var result = new List<TenantModuleStatus>(ModuleCatalog.All.Count);
        foreach (var descriptor in ModuleCatalog.All)
        {
            var source = overrides.TryGetValue(descriptor.Key, out var ov)
                ? (ov ? TenantModuleSource.OperatorEnabled : TenantModuleSource.OperatorDisabled)
                : TenantModuleSource.PlanDefault;

            var globallyAvailable = modules.IsGloballyAvailable(descriptor.Key);
            var unmet = plan is null
                ? descriptor.Dependencies
                : [.. descriptor.Dependencies.Where(d => !effective.Contains(d))];

            var enabled = plan is not null
                && globallyAvailable
                && unmet.Count == 0
                && (overrides.TryGetValue(descriptor.Key, out var explicitValue) ? explicitValue : defaults.Contains(descriptor.Key));

            result.Add(new TenantModuleStatus(descriptor.Key, enabled, source, globallyAvailable, unmet));
        }

        return result;
    }
}