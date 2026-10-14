using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>方案允許的內容。目前只有請求速率；等有功能需要分級時，功能權限再加入。</summary>
public sealed class PlanOptions
{
    public int PermitLimit { get; set; } = 60;

    public TimeSpan Window { get; set; } = TimeSpan.FromMinutes(1);
}

/// <summary>
/// <c>Platform</c> 區段中，存取控制的部分：營運者 key、方案、各租戶所屬的方案。
/// 區段名稱沿用 <c>Platform</c>，所以既有的環境變數（<c>Platform__AdminKeySha256</c> 等）不變。
/// </summary>
public sealed class ApiAccessOptions : IPlatformOptions
{
    public static string SectionName => "Platform";

    /// <summary>admin key 的十六進位 SHA-256（<c>Platform:AdminKeySha256</c>）。留空即停用 admin 存取。</summary>
    public string AdminKeySha256 { get; set; } = string.Empty;

    public Dictionary<string, PlanOptions> Plans { get; set; } = [];

    /// <summary>租戶識別碼 → 方案名稱。在計費整合之前於設定中手動指派。</summary>
    public Dictionary<string, string> TenantPlans { get; set; } = [];

    /// <summary>
    /// 沒有被指派方案的租戶所屬的方案。<b>留空 = 沒有預設方案</b>：服務是完全付費制，沒有方案的租戶
    /// 不能呼叫資料端點（回 402）。只有本機開發與測試才設這個值，正式環境不要設。
    /// </summary>
    public string? DefaultPlan { get; set; }

    /// <summary>
    /// 每個副本多久從資料庫重新載入一次方案指派。營運者在某個副本改了方案，本機立即生效，
    /// 其他副本最多晚這麼久才看到。
    /// </summary>
    public TimeSpan EntitlementRefresh { get; set; } = TimeSpan.FromSeconds(10);
}

/// <summary>
/// 方案名稱打錯必須在啟動時失敗，而不是讓付費租戶默默落在別的層級；admin key 不是 SHA-256 十六進位也一樣。
/// </summary>
public sealed class ApiAccessOptionsValidator : IValidateOptions<ApiAccessOptions>
{
    public ValidateOptionsResult Validate(string? name, ApiAccessOptions options)
    {
        var failures = new List<string>();
        if (options.AdminKeySha256.Length != 0
            && (options.AdminKeySha256.Length != 64 || !options.AdminKeySha256.All(Uri.IsHexDigit)))
        {
            failures.Add("Platform:AdminKeySha256 must be 64 hexadecimal characters (a SHA-256), or empty to disable admin access.");
        }

        var known = new HashSet<string>(options.Plans.Keys, StringComparer.Ordinal);
        failures.AddRange(options.TenantPlans
            .Where(t => !known.Contains(t.Value))
            .Select(t => $"Platform:TenantPlans:{t.Key} names unknown plan '{t.Value}'."));
        if (!string.IsNullOrEmpty(options.DefaultPlan) && !known.Contains(options.DefaultPlan))
        {
            failures.Add($"Platform:DefaultPlan names unknown plan '{options.DefaultPlan}'.");
        }

        if (options.EntitlementRefresh < TimeSpan.FromSeconds(1))
        {
            failures.Add("Platform:EntitlementRefresh must be at least one second.");
        }

        failures.AddRange(options.Plans
            .Where(p => p.Value.PermitLimit < 1 || p.Value.Window <= TimeSpan.Zero)
            .Select(p => $"Platform:Plans:{p.Key} needs a positive PermitLimit and Window."));

        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }
}