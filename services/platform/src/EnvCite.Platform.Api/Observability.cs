using System.Diagnostics;

using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.Diagnostics.HealthChecks;

using Npgsql;

using OpenTelemetry;
using OpenTelemetry.Metrics;
using OpenTelemetry.Trace;

namespace EnvCite.Platform.Api;

/// <summary>
/// 追蹤、指標、記錄與健康檢查。設定全部走 OpenTelemetry 標準的 <c>OTEL_*</c> 設定鍵
/// （k8s 的 ConfigMap 或 Downward API 以環境變數給即可）；只有設了
/// <c>OTEL_EXPORTER_OTLP_ENDPOINT</c> 才匯出，沒設就只在程序內，不連任何地方。
/// </summary>
internal static class Observability
{
    /// <summary>就緒探針的標籤：只有會影響「能不能接流量」的檢查才加。</summary>
    public const string ReadyTag = "ready";

    public static WebApplicationBuilder AddPlatformObservability(this WebApplicationBuilder builder)
    {
        var telemetry = builder.Services.AddOpenTelemetry()
            .WithTracing(tracing => tracing
                // 探針每幾秒打一次，不值得進追蹤。
                .AddAspNetCoreInstrumentation(o => o.Filter = context =>
                    !context.Request.Path.StartsWithSegments("/health"))
                // gRPC 用戶端走 HttpClient，這裡一併涵蓋 ontology 與 consult 的呼叫。
                .AddHttpClientInstrumentation()
                .AddNpgsql())
            .WithMetrics(metrics => metrics
                // RED：http.server.request.duration 一個直方圖，次數是 rate、http.response.status_code 的 5xx 是 errors、
                // 分布是 duration；標籤只有方法、路由模板、狀態碼，租戶不在其中（見 UseTenantTrace）。
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                // 對 ontology／consult 的 gRPC 用戶端：rpc.client.call.duration 以 rpc.response.status_code 區分結果。
                // 走 HttpClient 的 http.client.request.duration 看不出 gRPC 錯誤（gRPC 失敗仍是 HTTP 200）。
                .AddMeter(GrpcClientMetrics.MeterName)
                .AddRuntimeInstrumentation())
            .WithLogging();

        if (!string.IsNullOrEmpty(builder.Configuration["OTEL_EXPORTER_OTLP_ENDPOINT"]))
        {
            telemetry.UseOtlpExporter();
        }

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