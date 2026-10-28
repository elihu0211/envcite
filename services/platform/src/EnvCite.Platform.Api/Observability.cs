using System.Diagnostics;

using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.SharedKernel;
using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Diagnostics.HealthChecks;

using Npgsql;

using OpenTelemetry.Trace;

namespace EnvCite.Platform.Api;

/// <summary>
/// 追蹤、指標、記錄與健康檢查。遙測的約定（<c>OTEL_*</c> 設定鍵、沒設端點就不匯出）見
/// <see cref="Telemetry"/>；這裡只加 platform 自己的：資料庫的追蹤、租戶的追蹤標籤與 HTTP 探針。
/// </summary>
internal static class Observability
{
    /// <summary>就緒探針的標籤：只有會影響「能不能接流量」的檢查才加。</summary>
    public const string ReadyTag = "ready";

    public static WebApplicationBuilder AddPlatformObservability(this WebApplicationBuilder builder)
    {
        // 共用的追蹤、指標與匯出約定在 ServiceDefaults；platform 另加資料庫的追蹤。
        builder.AddServiceTelemetry().WithTracing(tracing => tracing.AddNpgsql());

        // liveness 不查任何依賴（見 MapPlatformHealth）；readiness 查資料庫，連不上就不接流量。
        // 租戶資料庫不屬於任何租戶，租戶解析之前也能建立（PlatformDbContext 不行）。
        builder.Services.AddHealthChecks()
            .AddDbContextCheck<TenancyDbContext>("database", tags: [ReadyTag]);

        return builder;
    }

    /// <summary>
    /// 把已驗證的租戶記在這個請求的追蹤上，之後能依租戶查。只放在追蹤，不放在指標：
    /// 租戶數量不受控時，指標的標籤會爆量。接在 <c>UseAuthentication()</c> 之後。
    /// </summary>
    public static IApplicationBuilder UseTenantTrace(this IApplicationBuilder app) =>
        app.Use((context, next) =>
        {
            var tenant = context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value;
            if (tenant is not null)
            {
                Activity.Current?.SetTag("tenant.id", tenant);
            }

            return next(context);
        });

    /// <summary>
    /// k8s 探針：<c>/health/live</c> 只回答「程序活著」，不查依賴（依賴掛了重啟程序沒有用，
    /// 還會連鎖重啟）；<c>/health/ready</c> 查資料庫，失敗回 503，k8s 把這個 pod 移出 Service。
    /// <c>/health</c> 維持原樣（等同 liveness），既有的探針設定不必改。
    /// </summary>
    public static WebApplication MapPlatformHealth(this WebApplication app)
    {
        // 探針每幾秒打一次：不進請求指標，否則 rate 與延遲分布被它們稀釋（與追蹤一致，見上）。
        app.MapGet("/health", () => Results.Ok(new { status = "ok" })).WithName("Health").DisableHttpMetrics();
        app.MapHealthChecks("/health/live", new() { Predicate = _ => false }).DisableHttpMetrics();
        app.MapHealthChecks("/health/ready", new() { Predicate = check => check.Tags.Contains(ReadyTag) }).DisableHttpMetrics();
        return app;
    }
}