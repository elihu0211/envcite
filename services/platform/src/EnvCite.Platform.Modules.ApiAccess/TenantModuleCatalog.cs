using System.Collections.Frozen;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>
/// 租戶 → 已啟用模組的記憶體快照。結合 <see cref="PlanCatalog"/> 的方案基準、
/// <see cref="TenantModuleOverride"/> 的營運者動態覆寫、環境可用性與模組前置依賴。
/// </summary>
public sealed class TenantModuleCatalog(PlanCatalog plans, IAnswerAvailability answerAvailability) : ITenantModules
{
    private static readonly FrozenSet<string> Empty = FrozenSet<string>.Empty;
    private readonly Lock _writer = new();

    // tenantIdentifier -> (moduleKey -> enabled)
    private volatile Dictionary<string, Dictionary<string, bool>> _overrides = [];

    public bool IsEnabled(string tenantIdentifier, string moduleKey) =>
        EnabledModules(tenantIdentifier).Contains(moduleKey);

    public IReadOnlySet<string> EnabledModules(string tenantIdentifier)
    {
        if (string.IsNullOrEmpty(tenantIdentifier) || plans.For(tenantIdentifier) is not { } plan)
            return Empty;

        var raw = new HashSet<string>(PlanDefaults(plan), StringComparer.Ordinal);
        if (_overrides.TryGetValue(tenantIdentifier, out var tenantOverrides))
        {
            foreach (var (key, enabled) in tenantOverrides)
            {
                if (!ModuleCatalog.IsKnown(key))
                    continue;

                if (enabled)
                    raw.Add(key);
                else
                    raw.Remove(key);
            }
        }

        // 環境前提未就緒的模組一律關閉。
        foreach (var descriptor in ModuleCatalog.All)
        {
            if (descriptor.RequiresGlobalAvailability && !IsGloballyAvailable(descriptor.Key))
                raw.Remove(descriptor.Key);
        }

        // 依賴檢查：固定點迭代，確保前置模組未啟用時依賴方一併失效。
        bool changed;
        do
        {
            changed = false;
            foreach (var descriptor in ModuleCatalog.All)
            {
                if (!raw.Contains(descriptor.Key))
                    continue;

                if (descriptor.Dependencies.Any(dep => !raw.Contains(dep)))
                {
                    raw.Remove(descriptor.Key);
                    changed = true;
                }
            }
        }
        while (changed);

        return raw;
    }

    internal IReadOnlyDictionary<string, bool> OverridesFor(string tenantIdentifier) =>
        _overrides.TryGetValue(tenantIdentifier, out var map)
            ? map
            : FrozenDictionary<string, bool>.Empty;

    internal bool IsGloballyAvailable(string moduleKey) =>
        moduleKey != ModuleKeys.GroundedAnswer || answerAvailability.Available;

    internal static IEnumerable<string> PlanDefaults(PlanOptions plan) =>
        plan.Modules is not null
            ? plan.Modules.Where(ModuleCatalog.IsKnown)
            : ModuleCatalog.All.Where(m => !m.OptInByDefault).Select(m => m.Key);

    internal void SetOverride(string tenantIdentifier, string moduleKey, bool? enabled)
    {
        lock (_writer)
        {
            var next = Clone(_overrides);
            if (!next.TryGetValue(tenantIdentifier, out var tenantMap))
            {
                if (enabled is null)
                    return;

                tenantMap = new Dictionary<string, bool>(StringComparer.Ordinal);
                next[tenantIdentifier] = tenantMap;
            }
            else
            {
                tenantMap = new Dictionary<string, bool>(tenantMap, StringComparer.Ordinal);
                next[tenantIdentifier] = tenantMap;
            }

            if (enabled is { } value)
                tenantMap[moduleKey] = value;
            else
                tenantMap.Remove(moduleKey);

            if (tenantMap.Count == 0)
                next.Remove(tenantIdentifier);

            _overrides = next;
        }
    }

    internal void ClearTenant(string tenantIdentifier)
    {
        lock (_writer)
        {
            if (!_overrides.ContainsKey(tenantIdentifier))
                return;

            var next = Clone(_overrides);
            next.Remove(tenantIdentifier);
            _overrides = next;
        }
    }

    internal void Replace(IEnumerable<(string TenantIdentifier, string ModuleKey, bool Enabled)> rows)
    {
        lock (_writer)
        {
            var next = new Dictionary<string, Dictionary<string, bool>>(StringComparer.Ordinal);
            foreach (var (tenantIdentifier, moduleKey, enabled) in rows)
            {
                if (!next.TryGetValue(tenantIdentifier, out var map))
                {
                    map = new Dictionary<string, bool>(StringComparer.Ordinal);
                    next[tenantIdentifier] = map;
                }

                map[moduleKey] = enabled;
            }

            _overrides = next;
        }
    }

    private static Dictionary<string, Dictionary<string, bool>> Clone(Dictionary<string, Dictionary<string, bool>> source) =>
        source.ToDictionary(
            kv => kv.Key,
            kv => new Dictionary<string, bool>(kv.Value, StringComparer.Ordinal),
            StringComparer.Ordinal);
}