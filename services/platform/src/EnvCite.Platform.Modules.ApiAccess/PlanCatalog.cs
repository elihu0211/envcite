using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>
/// 租戶 → 方案。沒有方案就回 null，不是退回某個內建方案：完全付費制下，沒有方案就是沒有服務。
/// </summary>
/// <remarks>
/// 來源依序：<b>資料庫的指派</b>（營運後台與 <c>/admin</c> 改的；<see cref="EntitlementLoader"/> 定期載入）→
/// 設定的 <c>TenantPlans</c>（只讀的備援，給還沒有資料庫指派的租戶）→ 設定的 <c>DefaultPlan</c>。
/// 每個請求都要查，所以查詢是對記憶體快照的同步讀取；本機的變更立即生效，其他副本在下一次重新載入
/// （預設 10 秒）內看到。
/// </remarks>
public sealed class PlanCatalog
{
    /// <summary>
    /// 沒有方案的租戶打管理端點（<c>/v2/me</c>、用量、API key 管理）時的限流底線。不是服務額度：
    /// 資料端點對沒有方案的租戶一律 402。這條底線只是防止沒有方案的租戶（或被盜的 key）拿管理端點灌流量。
    /// </summary>
    public static PlanOptions UnplannedFloor { get; } = new() { PermitLimit = 20, Window = TimeSpan.FromMinutes(1) };

    private readonly Dictionary<string, PlanOptions> _plans;
    private readonly Dictionary<string, string> _configuredTenantPlans;
    private readonly string? _defaultPlan;
    private readonly Lock _writer = new();

    // 寫入時整份換掉（copy-on-write）；讀取不加鎖。
    private volatile Dictionary<string, string> _assigned = [];

    public PlanCatalog(IOptions<ApiAccessOptions> options)
    {
        _plans = new(options.Value.Plans, StringComparer.Ordinal);
        _configuredTenantPlans = options.Value.TenantPlans;
        _defaultPlan = string.IsNullOrEmpty(options.Value.DefaultPlan) ? null : options.Value.DefaultPlan;
    }

    public IReadOnlyDictionary<string, PlanOptions> Plans => _plans;

    /// <summary>租戶的方案名稱；沒有則 null。</summary>
    public string? NameFor(string tenantIdentifier) =>
        _assigned.GetValueOrDefault(tenantIdentifier)
        ?? _configuredTenantPlans.GetValueOrDefault(tenantIdentifier)
        ?? _defaultPlan;

    /// <summary>
    /// 租戶的方案；沒有則 null。資料庫裡的指派可能指向後來從設定移除的方案：當成沒有方案
    /// （fail closed），不是例外——一個過時的指派不能讓每個請求都 500。
    /// </summary>
    public PlanOptions? For(string tenantIdentifier) =>
        NameFor(tenantIdentifier) is { } name && _plans.TryGetValue(name, out var plan) ? plan : null;

    /// <summary>本機的變更立即生效（不等下一次重新載入）。</summary>
    internal void Set(string tenantIdentifier, string? plan)
    {
        lock (_writer)
        {
            var next = new Dictionary<string, string>(_assigned, StringComparer.Ordinal);
            if (plan is null)
            {
                next.Remove(tenantIdentifier);
            }
            else
            {
                next[tenantIdentifier] = plan;
            }

            _assigned = next;
        }
    }

    /// <summary>以資料庫的全部指派換掉快照。</summary>
    internal void Replace(IEnumerable<KeyValuePair<string, string>> assigned)
    {
        lock (_writer)
        {
            _assigned = new Dictionary<string, string>(assigned, StringComparer.Ordinal);
        }
    }
}