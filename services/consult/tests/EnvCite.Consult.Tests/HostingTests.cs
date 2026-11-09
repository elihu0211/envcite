using System.Diagnostics.Metrics;

using EnvCite.Consult.V2;

using Grpc.Core;
using Grpc.Health.V1;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Diagnostics.Metrics.Testing;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using Microsoft.Extensions.Time.Testing;

using OpenTelemetry;
using OpenTelemetry.Trace;

namespace EnvCite.Consult.Tests;

/// <summary>容器裡的行為：健康檢查與排空、RED 指標、遙測的服務名稱。</summary>
[Collection(ConsultTestGroup.Name)]
public sealed class HostingTests(ConsultFactory factory)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static async Task<HealthCheckResponse.Types.ServingStatus> StatusAsync(Health.HealthClient health, string service) =>
        (await health.CheckAsync(new HealthCheckRequest { Service = service }, cancellationToken: Ct)).Status;

    [Theory]
    [InlineData("")]
    [InlineData("envcite.consult.v2.ConsultationService")]
    public async Task LivenessAndReadinessAreServing(string service)
    {
        var health = new Health.HealthClient(ConsultFactory.ChannelFor(factory));

        Assert.Equal(HealthCheckResponse.Types.ServingStatus.Serving, await StatusAsync(health, service));
    }

    [Fact]
    public async Task OnShutdownItReportsNotServingAndDrainsBeforeStopping()
    {
        var time = new FakeTimeProvider();
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureServices(services =>
            services.Replace(ServiceDescriptor.Singleton<TimeProvider>(time))));
        var health = new Health.HealthClient(ConsultFactory.ChannelFor(host));
        Assert.Equal(HealthCheckResponse.Types.ServingStatus.Serving, await StatusAsync(health, ""));

        var stopping = host.Services.GetRequiredService<IHost>().StopAsync(Ct);

        // 排空期間伺服器仍在接受呼叫，但已回報 NOT_SERVING；等滿排空的時間才往下停。
        var deadline = DateTime.UtcNow.AddSeconds(5);
        var status = HealthCheckResponse.Types.ServingStatus.Serving;
        while (status == HealthCheckResponse.Types.ServingStatus.Serving && DateTime.UtcNow < deadline)
        {
            status = await StatusAsync(health, "");
        }

        Assert.Equal(HealthCheckResponse.Types.ServingStatus.NotServing, status);
        Assert.Equal(HealthCheckResponse.Types.ServingStatus.NotServing, await StatusAsync(health, ConsultationGrpcService.ServiceName));
        Assert.False(stopping.IsCompleted);
        time.Advance(Draining.Delay);
        await stopping;
    }

    [Fact]
    public async Task EachCallIsMeasuredByMethodAndStatusButNotProbesTenantsOrContent()
    {
        using var collector = new MetricCollector<double>(
            factory.Services.GetRequiredService<IMeterFactory>(), GrpcServerMetrics.MeterName, GrpcServerMetrics.DurationMetric);
        var client = factory.CreateClient();
        var health = new Health.HealthClient(ConsultFactory.ChannelFor(factory));
        var request = new GroundRequest { Question = FakeOntologyServer.AnswerableQuery };

        await client.GroundAsync(request, ConsultFactory.CallMetadata, cancellationToken: Ct);
        await client.GroundAsync(request, ConsultFactory.CallMetadata, cancellationToken: Ct);
        await Assert.ThrowsAsync<RpcException>(async () => await client.GroundAsync(request, cancellationToken: Ct)); // 沒帶租戶
        await health.CheckAsync(new HealthCheckRequest(), cancellationToken: Ct);

        var points = collector.GetMeasurementSnapshot();
        Assert.Equal(["INVALID_ARGUMENT", "OK", "OK"], points.Select(p => (string)p.Tags["rpc.response.status_code"]!).Order());
        Assert.All(points, p =>
        {
            Assert.Equal("grpc", p.Tags["rpc.system.name"]);
            Assert.Equal("envcite.consult.v2.ConsultationService", p.Tags["rpc.service"]);
            Assert.Equal("Ground", p.Tags["rpc.method"]);
            // 標籤全是有限集合：租戶與問句不在其中。
            Assert.Equal(4, p.Tags.Count);
        });
    }

    [Fact]
    public async Task EachGroundResultIsCountedByOutcomeAndLocalMarkerButFailedCallsAreNot()
    {
        using var collector = new MetricCollector<long>(
            factory.Services.GetRequiredService<IMeterFactory>(), GroundingMetrics.MeterName, GroundingMetrics.OutcomesMetric);
        var client = factory.CreateClient();

        foreach (var question in new[]
        {
            FakeOntologyServer.AnswerableQuery,
            FakeOntologyServer.DualTrackQuery,
            FakeOntologyServer.IrrelevantQuery,
            FakeOntologyServer.EmptyQuery,
            "臺北市有哪些空氣污染自治條例",
            "  ",
        })
        {
            await client.GroundAsync(new GroundRequest { Question = question }, ConsultFactory.CallMetadata, cancellationToken: Ct);
        }

        // 沒有結果的呼叫：沒帶租戶、本體不可用；它們看 RED 指標，不進這個計數。
        await Assert.ThrowsAsync<RpcException>(async () => await client.GroundAsync(
            new GroundRequest { Question = FakeOntologyServer.AnswerableQuery }, cancellationToken: Ct));
        await Assert.ThrowsAsync<RpcException>(async () => await client.GroundAsync(
            new GroundRequest { Question = FakeOntologyServer.UnavailableQuery }, ConsultFactory.CallMetadata, cancellationToken: Ct));

        var points = collector.GetMeasurementSnapshot();
        Assert.Equal(
            [
                ("grounded", "jurisdiction"),
                ("grounded", "none"),
                ("local_law_uncovered", "ordinance"),
                ("missing_input", "none"),
                ("no_evidence", "none"),
                ("out_of_scope", "none"),
            ],
            [.. points.Select(p => ((string)p.Tags["outcome"]!, (string)p.Tags["local_marker"]!)).Order()]);
        // 標籤全是有限集合：租戶與問句不在其中。
        Assert.All(points, p => Assert.Equal(2, p.Tags.Count));
    }

    [Fact]
    public void AnOutOfRangeRelevanceFloorStopsTheHostFromStarting()
    {
        using var host = factory.WithWebHostBuilder(b => b.UseSetting("Relevance:SemanticFloor", "0"));

        var error = Assert.Throws<OptionsValidationException>(() => host.Services);
        Assert.Contains("Relevance:SemanticFloor", error.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void TheServiceNameDefaultsFromAppsettings()
    {
        var name = factory.Services.GetRequiredService<TracerProvider>().GetResource().Attributes
            .FirstOrDefault(a => a.Key == "service.name").Value;

        Assert.Equal("envcite-consult", name);
    }
}