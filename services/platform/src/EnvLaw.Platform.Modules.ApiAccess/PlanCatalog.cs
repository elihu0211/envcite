using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>
/// 租戶 → 方案。沒有方案就回 null，不是退回某個內建方案：完全付費制下，沒有方案就是沒有服務。
/// </summary>
public sealed class PlanCatalog
{
    /// <summary>
    /// 沒有方案的租戶打管理端點（<c>/v2/me</c>、用量、API key 管理）時的限流底線。不是服務額度：
    /// 資料端點對沒有方案的租戶一律 402。這條底線只是防止沒有方案的租戶（或被盜的 key）拿管理端點灌流量。
    /// </summary>
    public static PlanOptions UnplannedFloor { get; } = new() { PermitLimit = 20, Window = TimeSpan.FromMinutes(1) };

    private readonly Dictionary<string, PlanOptions> _plans;
    private readonly Dictionary<string, string> _tenantPlans;
    private readonly string? _defaultPlan;

    public PlanCatalog(IOptions<ApiAccessOptions> options)
    {
        _plans = new(options.Value.Plans, StringComparer.Ordinal);
        _tenantPlans = options.Value.TenantPlans;
        _defaultPlan = string.IsNullOrEmpty(options.Value.DefaultPlan) ? null : options.Value.DefaultPlan;
    }

    /// <summary>租戶的方案名稱；沒有則 null。方案名稱在啟動時已驗證存在。</summary>
    public string? NameFor(string tenantIdentifier) => _tenantPlans.GetValueOrDefault(tenantIdentifier) ?? _defaultPlan;

    /// <summary>租戶的方案；沒有則 null。</summary>
    public PlanOptions? For(string tenantIdentifier) => NameFor(tenantIdentifier) is { } name ? _plans[name] : null;
}