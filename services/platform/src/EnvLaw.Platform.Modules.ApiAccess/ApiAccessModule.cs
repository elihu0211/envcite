using System.Threading.RateLimiting;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.ApiAccess;

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

    /// <summary>租戶識別碼 → 方案名稱。在計費整合之前於設定中手動指派；未列出的租戶屬於 <see cref="PlanCatalog.DefaultPlan"/>。</summary>
    public Dictionary<string, string> TenantPlans { get; set; } = [];
}

/// <summary>
/// 方案名稱打錯必須在啟動時失敗，而不是讓付費租戶默默落在免費層；admin key 不是 SHA-256 十六進位也一樣。
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

        var known = new HashSet<string>(options.Plans.Keys, StringComparer.Ordinal) { PlanCatalog.DefaultPlan };
        failures.AddRange(options.TenantPlans
            .Where(t => !known.Contains(t.Value))
            .Select(t => $"Platform:TenantPlans:{t.Key} names unknown plan '{t.Value}'."));
        failures.AddRange(options.Plans
            .Where(p => p.Value.PermitLimit < 1 || p.Value.Window <= TimeSpan.Zero)
            .Select(p => $"Platform:Plans:{p.Key} needs a positive PermitLimit and Window."));

        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }
}

/// <summary>方案目錄，由已驗證的 <see cref="ApiAccessOptions"/> 建立。</summary>
public sealed class PlanCatalog
{
    public const string DefaultPlan = "free";

    private readonly Dictionary<string, PlanOptions> _plans;
    private readonly Dictionary<string, string> _tenantPlans;

    public PlanCatalog(IOptions<ApiAccessOptions> options)
    {
        _plans = new(options.Value.Plans, StringComparer.Ordinal);
        _plans.TryAdd(DefaultPlan, new PlanOptions());
        _tenantPlans = options.Value.TenantPlans;
    }

    public PlanOptions For(string tenantIdentifier) =>
        _plans[_tenantPlans.GetValueOrDefault(tenantIdentifier, DefaultPlan)];
}

public static class ApiAccessModule
{
    /// <summary>註冊 ApiKey/AdminKey scheme、policy 與逐租戶的 rate limiting（API key 存於 <c>PlatformDbContext</c>）。</summary>
    public static IServiceCollection AddApiAccessModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddPlatformOptions<ApiAccessOptions>();
        services.AddSingleton<IValidateOptions<ApiAccessOptions>, ApiAccessOptionsValidator>();
        services.AddSingleton<PlanCatalog>();

        services.AddAuthentication(PlatformAuth.ApiKeyScheme)
            .AddScheme<Microsoft.AspNetCore.Authentication.AuthenticationSchemeOptions, ApiKeyAuthenticationHandler>(
                PlatformAuth.ApiKeyScheme, _ => { })
            .AddScheme<AdminKeyOptions, AdminKeyAuthenticationHandler>(PlatformAuth.AdminKeyScheme, null);
        services.AddOptions<AdminKeyOptions>(PlatformAuth.AdminKeyScheme)
            .Configure<IOptions<ApiAccessOptions>>((scheme, api) => scheme.AdminKeySha256 = api.Value.AdminKeySha256);

        services.AddAuthorizationBuilder()
            .AddPolicy(PlatformAuth.TenantPolicy, p => p
                .AddAuthenticationSchemes(PlatformAuth.ApiKeyScheme)
                .RequireClaim(PlatformClaimTypes.TenantIdentifier))
            .AddPolicy(PlatformAuth.AdminPolicy, p => p
                .AddAuthenticationSchemes(PlatformAuth.AdminKeyScheme)
                .RequireRole("platform-admin"));

        services.AddRateLimiter(limiter =>
        {
            limiter.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
            // 以已驗證的租戶 claim 分區，絕不使用原始 request header。
            limiter.AddPolicy(PlatformAuth.PerTenantRateLimit, context =>
            {
                var tenant = context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value ?? "anonymous";
                var plans = context.RequestServices.GetRequiredService<PlanCatalog>();
                return RateLimitPartition.GetFixedWindowLimiter(tenant, _ =>
                {
                    var plan = plans.For(tenant);
                    return new FixedWindowRateLimiterOptions
                    {
                        PermitLimit = plan.PermitLimit,
                        Window = plan.Window,
                        QueueLimit = 0,
                    };
                });
            });
        });

        return services;
    }
}