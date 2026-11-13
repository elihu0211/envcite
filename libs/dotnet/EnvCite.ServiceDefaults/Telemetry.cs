using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

using OpenTelemetry;
using OpenTelemetry.Metrics;
using OpenTelemetry.Trace;

namespace EnvCite.ServiceDefaults;

/// <summary>
/// 追蹤、指標與記錄，各服務同一個約定：設定全部走 OpenTelemetry 標準的 <c>OTEL_*</c> 設定鍵
/// （k8s 的 ConfigMap 或 Downward API 以環境變數給即可；服務名稱的預設寫在各服務的 appsettings.json）；
/// 只有設了 <c>OTEL_EXPORTER_OTLP_ENDPOINT</c> 才匯出，沒設就只在程序內，不連任何地方。
/// </summary>
public static class Telemetry
{
    /// <summary>回傳的 builder 讓服務再加自己的來源（資料庫的追蹤、伺服器端的 gRPC 指標）。</summary>
    public static OpenTelemetryBuilder AddServiceTelemetry(this IHostApplicationBuilder builder)
    {
        var telemetry = builder.Services.AddOpenTelemetry()
            .WithTracing(tracing => tracing
                // 探針每幾秒打一次，不值得進追蹤。
                .AddAspNetCoreInstrumentation(o => o.Filter = context => !IsProbe(context.Request.Path))
                // gRPC 用戶端走 HttpClient，這裡一併涵蓋對內部服務的呼叫。
                .AddHttpClientInstrumentation())
            .WithMetrics(metrics => metrics
                // RED：http.server.request.duration 一個直方圖，次數是 rate、http.response.status_code 的 5xx 是 errors、
                // 分布是 duration；標籤只有方法、路由模板、狀態碼，租戶不在其中。探針的端點以 DisableHttpMetrics() 排除。
                .AddAspNetCoreInstrumentation()
                .AddHttpClientInstrumentation()
                // 對內部服務的 gRPC 用戶端：rpc.client.call.duration 以 rpc.response.status_code 區分結果。
                // 走 HttpClient 的 http.client.request.duration 看不出 gRPC 錯誤（gRPC 失敗仍是 HTTP 200）。
                .AddMeter(GrpcClientMetrics.MeterName)
                .AddRuntimeInstrumentation())
            .WithLogging();

        if (!string.IsNullOrEmpty(builder.Configuration["OTEL_EXPORTER_OTLP_ENDPOINT"]))
            telemetry.UseOtlpExporter();

        return telemetry;
    }

    /// <summary>k8s 的探針：HTTP 的 <c>/health</c>，與 gRPC 健康檢查協定的服務。</summary>
    private static bool IsProbe(PathString path) =>
        path.StartsWithSegments("/health") || path.StartsWithSegments("/grpc.health.v1.Health");
}