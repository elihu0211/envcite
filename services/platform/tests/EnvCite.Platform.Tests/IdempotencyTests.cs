using System.Net;
using System.Net.Http.Json;

using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Metering;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class IdempotencyTests(PlatformFactory factory)
{
    private static readonly Uri GroundPath = new("/v2/ground", UriKind.Relative);

    private static async Task<HttpResponseMessage> GroundAsync(
        HttpClient client, string? key, string question = FakeConsultServer.AnswerableQuestion, string? pointInTime = null)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, GroundPath)
        {
            Content = JsonContent.Create(new GroundRequestDto(question, pointInTime)),
        };
        if (key is not null)
        {
            request.Headers.TryAddWithoutValidation(ConsultEndpoints.IdempotencyKeyHeader, key);
        }

        return await client.SendAsync(request);
    }

    private int ConsultCalls(string tenantIdentifier) =>
        factory.Consult.Received.Count(r => r.Headers.GetValue("x-tenant-id") == tenantIdentifier);

    private static async Task<UsageSummary[]> UsageAsync(HttpClient client) =>
        (await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage"))!;

    [Fact]
    public async Task ResendingTheSameKeyReplaysTheFirstResponseWithoutConsultingOrMeteringAgain()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("idem-replay");
        using var client = factory.CreateTenantClient(key.Key);

        using var first = await GroundAsync(client, "order-1");
        using var second = await GroundAsync(client, "order-1");

        Assert.Equal(HttpStatusCode.OK, first.StatusCode);
        Assert.Equal(HttpStatusCode.OK, second.StatusCode);
        Assert.False(first.Headers.Contains(ConsultEndpoints.ReplayedHeader));
        Assert.Equal("true", Assert.Single(second.Headers.GetValues(ConsultEndpoints.ReplayedHeader)));
        Assert.Equal(await first.Content.ReadAsStringAsync(TestContext.Current.CancellationToken), await second.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
        Assert.Equal(1, ConsultCalls(tenant.Identifier));
        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 1, 1)], await UsageAsync(client));
    }

    [Fact]
    public async Task ARefusalIsReplayedToo()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("idem-refused");
        using var client = factory.CreateTenantClient(key.Key);

        using var first = await GroundAsync(client, "r-1", FakeConsultServer.UnrelatedQuestion);
        using var second = await GroundAsync(client, "r-1", FakeConsultServer.UnrelatedQuestion);

        Assert.Equal(await first.Content.ReadAsStringAsync(TestContext.Current.CancellationToken), await second.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 1, 1)], await UsageAsync(client));
    }

    [Theory]
    [InlineData(FakeConsultServer.UnrelatedQuestion, null)]
    [InlineData(FakeConsultServer.AnswerableQuestion, "2020-01-01")]
    public async Task TheSameKeyWithADifferentRequestIsRejectedAndNotMetered(string question, string? pointInTime)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("idem-mismatch");
        using var client = factory.CreateTenantClient(key.Key);
        (await GroundAsync(client, "k")).Dispose();

        using var response = await GroundAsync(client, "k", question, pointInTime);

        Assert.Equal(HttpStatusCode.UnprocessableEntity, response.StatusCode);
        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 1, 1)], await UsageAsync(client));
    }

    [Fact]
    public async Task KeysAreScopedToTheTenant()
    {
        var (_, keyA) = await factory.CreateTenantWithKeyAsync("idem-a");
        var (_, keyB) = await factory.CreateTenantWithKeyAsync("idem-b");
        using var a = factory.CreateTenantClient(keyA.Key);
        using var b = factory.CreateTenantClient(keyB.Key);

        (await GroundAsync(a, "shared")).Dispose();
        using var other = await GroundAsync(b, "shared", FakeConsultServer.UnrelatedQuestion);

        Assert.Equal(HttpStatusCode.OK, other.StatusCode);
        Assert.False(other.Headers.Contains(ConsultEndpoints.ReplayedHeader));
        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 1, 1)], await UsageAsync(b));
    }

    [Fact]
    public async Task ErrorsAreNotRememberedSoARetryIsProcessedAgain()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("idem-errors");
        using var client = factory.CreateTenantClient(key.Key);

        using var first = await GroundAsync(client, "e-1", FakeConsultServer.UnavailableQuestion);
        var afterFirst = ConsultCalls(tenant.Identifier);
        using var second = await GroundAsync(client, "e-1", FakeConsultServer.UnavailableQuestion);

        Assert.Equal(HttpStatusCode.ServiceUnavailable, first.StatusCode);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, second.StatusCode);
        Assert.True(ConsultCalls(tenant.Identifier) > afterFirst); // 重送真的又打到 consult（用戶端會自己重試 UNAVAILABLE，所以不比確切次數）
        Assert.Empty(await UsageAsync(client));
    }

    [Theory]
    [InlineData("has space")]
    [InlineData("中文")]
    public async Task AMalformedKeyIsRejected(string value)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("idem-bad");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await GroundAsync(client, value);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task AKeyLongerThanTheLimitIsRejected()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("idem-long");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await GroundAsync(client, new string('a', 256));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task ConcurrentDuplicatesAreMeteredOnce()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("idem-race");
        using var client = factory.CreateTenantClient(key.Key);

        var responses = await Task.WhenAll(Enumerable.Range(0, 4).Select(_ => GroundAsync(client, "race")));
        var bodies = await Task.WhenAll(responses.Select(r => r.Content.ReadAsStringAsync()));
        foreach (var r in responses)
        {
            r.Dispose();
        }

        Assert.All(responses, r => Assert.Equal(HttpStatusCode.OK, r.StatusCode));
        Assert.Single(bodies.Distinct());
        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 1, 1)], await UsageAsync(client));
    }

    [Fact]
    public async Task AfterTheWindowTheKeyIsProcessedAsNewAndTheRetentionSweepsOldRecords()
    {
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Idempotency:Window", "00:10:00"));
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("idem-window");
        using var client = host.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", key.Key);

        (await GroundAsync(client, "w")).Dispose();
        factory.Time.Advance(TimeSpan.FromMinutes(11));
        var purged = await host.Services.GetRequiredService<IdempotencyRetention>().PurgeAsync(CancellationToken.None);
        using var again = await GroundAsync(client, "w");

        Assert.True(purged >= 1);
        Assert.Equal(HttpStatusCode.OK, again.StatusCode);
        Assert.False(again.Headers.Contains(ConsultEndpoints.ReplayedHeader));
        Assert.Equal(2, ConsultCalls(tenant.Identifier));
        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 2, 2)], await UsageAsync(client));
    }

    [Fact]
    public async Task AnExpiredRecordThatWasNotYetSweptDoesNotBlockTheKey()
    {
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Idempotency:Window", "00:10:00"));
        var (_, key) = await factory.CreateTenantWithKeyAsync("idem-unswept");
        using var client = host.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", key.Key);

        (await GroundAsync(client, "u")).Dispose();
        factory.Time.Advance(TimeSpan.FromMinutes(11));
        using var again = await GroundAsync(client, "u");

        Assert.Equal(HttpStatusCode.OK, again.StatusCode);
        Assert.False(again.Headers.Contains(ConsultEndpoints.ReplayedHeader));
    }

    [Theory]
    [InlineData("00:00:30")]
    [InlineData("8.00:00:00")]
    public void TheWindowMustBeBetweenOneMinuteAndSevenDays(string window)
    {
        var ex = Assert.ThrowsAny<Exception>(() =>
        {
            using var bad = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Idempotency:Window", window));
            _ = bad.Services;
        });
        Assert.Contains("Platform:Idempotency:Window", ex.ToString(), StringComparison.Ordinal);
    }
}