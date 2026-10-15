using System.Diagnostics;
using System.Net;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;

using OpenTelemetry;
using OpenTelemetry.Trace;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class ObservabilityTests(PlatformFactory factory)
{
    private static string? ServiceName(IServiceProvider services) =>
        services.GetRequiredService<TracerProvider>().GetResource().Attributes
            .FirstOrDefault(a => a.Key == "service.name").Value as string;

    [Theory]
    [InlineData("/health")]
    [InlineData("/health/live")]
    [InlineData("/health/ready")]
    public async Task ProbesAnswerOkWhenTheDatabaseIsUp(string path)
    {
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri(path, UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public void TheServiceNameDefaultsFromAppsettings()
    {
        Assert.Equal("envcite-platform", ServiceName(factory.Services));
    }

    // k8s 以環境變數給設定；環境變數與這裡的 UseSetting 同屬設定來源，優先於 appsettings.json。
    [Fact]
    public void TheServiceNameCanBeOverriddenFromTheEnvironment()
    {
        using var other = factory.WithWebHostBuilder(b => b.UseSetting("OTEL_SERVICE_NAME", "from-environment"));

        Assert.Equal("from-environment", ServiceName(other.Services));
    }

    // 伺服器的請求追蹤在回應送出之後才結束，所以要等，不能回應一到就斷言。
    [Fact]
    public async Task TheRequestTraceCarriesTheTenantButTheProbeIsNotTraced()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("trace");
        var seen = new List<Activity>();
        using var listener = new ActivityListener
        {
            ShouldListenTo = source => source.Name == "Microsoft.AspNetCore",
            Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
            ActivityStopped = activity =>
            {
                lock (seen)
                {
                    seen.Add(activity);
                }
            },
        };
        ActivitySource.AddActivityListener(listener);

        using var client = factory.CreateTenantClient(key.Key);
        using var probe = await client.GetAsync(new Uri("/health/live", UriKind.Relative), TestContext.Current.CancellationToken);
        using var me = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, probe.StatusCode);
        Assert.Equal(HttpStatusCode.OK, me.StatusCode);

        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (DateTime.UtcNow < deadline && !HasTenantTrace(seen, tenant.Identifier))
        {
            await Task.Delay(20, TestContext.Current.CancellationToken);
        }

        lock (seen)
        {
            Assert.True(HasTenantTrace(seen, tenant.Identifier));
            Assert.DoesNotContain(seen, a => ((string?)a.GetTagItem("url.path"))?.StartsWith("/health", StringComparison.Ordinal) == true);
        }
    }

    private static bool HasTenantTrace(List<Activity> seen, string tenant)
    {
        lock (seen)
        {
            return seen.Exists(a => (string?)a.GetTagItem("tenant.id") == tenant);
        }
    }
}