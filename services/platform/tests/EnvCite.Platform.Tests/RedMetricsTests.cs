using System.Net;

using EnvCite.Platform.Modules.Consult;
using EnvCite.ServiceDefaults;

using OpenTelemetry;
using OpenTelemetry.Metrics;

namespace EnvCite.Platform.Tests;

/// <summary>RED 指標（rate、errors、duration）：HTTP 伺服器，以及對 consult 的 gRPC 用戶端。</summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class RedMetricsTests(PlatformFactory factory)
{
    private sealed record Point(string Metric, Dictionary<string, object?> Tags, long Count);

    private sealed class Capture : BaseExporter<Metric>
    {
        public List<Point> Points { get; } = [];

        public override ExportResult Export(in Batch<Metric> batch)
        {
            foreach (var metric in batch)
            {
                foreach (var point in metric.GetMetricPoints())
                {
                    if (metric.MetricType != MetricType.Histogram)
                        continue;

                    var tags = new Dictionary<string, object?>();
                    foreach (var tag in point.Tags)
                        tags[tag.Key] = tag.Value;

                    Points.Add(new Point(metric.Name, tags, point.GetHistogramCount()));
                }
            }

            return ExportResult.Success;
        }
    }

    private static Task<HttpResponseMessage> GroundAsync(HttpClient client, string question, string? pointInTime = null) =>
        client.PostAsJsonAsync(new Uri("/v2/ground", UriKind.Relative), new GroundRequestDto(question, pointInTime));

    [Fact]
    public async Task RequestsAndGrpcCallsAreMeasuredByRouteAndStatusButProbesAndTenantsAreNot()
    {
        var capture = new Capture();
        using var reader = new BaseExportingMetricReader(capture);
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureServices(services =>
            services.ConfigureOpenTelemetryMeterProvider(provider => provider.AddReader(reader))));
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("red");
        using var client = host.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", key.Key);

        (await GroundAsync(client, FakeConsultServer.AnswerableQuestion)).Dispose();
        (await GroundAsync(client, FakeConsultServer.AnswerableQuestion, "yesterday")).Dispose(); // 400
        (await GroundAsync(client, FakeConsultServer.UnavailableQuestion)).Dispose(); // 503
        using var probe = await client.GetAsync(new Uri("/health/live", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, probe.StatusCode);
        reader.Collect();

        var http = capture.Points.Where(p => p.Metric == "http.server.request.duration").ToList();
        Assert.Equal([200, 400, 503], StatusesOf(http, "/v2/ground"));
        Assert.DoesNotContain(http, p => ((string?)p.Tags.GetValueOrDefault("http.route"))?.StartsWith("/health", StringComparison.Ordinal) == true);

        var grpc = capture.Points.Where(p => p.Metric == GrpcClientMetrics.DurationMetric).ToList();
        Assert.Equal(
            ["INVALID_ARGUMENT", "OK", "UNAVAILABLE"],
            grpc.Where(p => p.Tags.GetValueOrDefault("rpc.method") as string == "Ground")
                .Select(p => (string)p.Tags["rpc.response.status_code"]!).Order().ToArray());
        Assert.All(grpc, p => Assert.Equal("envcite.consult.v2.ConsultationService", p.Tags["rpc.service"]));

        // 租戶只在追蹤上：基數不受控，不進任何指標的標籤。
        Assert.DoesNotContain(capture.Points, p => p.Tags.Keys.Any(k => k.Contains("tenant", StringComparison.OrdinalIgnoreCase))
            || p.Tags.Values.Any(v => v as string == tenant.Identifier));
    }

    private static int[] StatusesOf(List<Point> points, string route) =>
        [.. points
            .Where(p => p.Tags.GetValueOrDefault("http.route") as string == route)
            .Select(p => Convert.ToInt32(p.Tags["http.response.status_code"], System.Globalization.CultureInfo.InvariantCulture))
            .Distinct()
            .Order()];
}