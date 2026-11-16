using System.Net;
using System.Text.Json;

using EnvCite.Consult.V3;
using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Metering;

using Grpc.Core;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group1)]
public sealed class ConsultGatewayTests(PlatformFactory factory)
{
    private static readonly Uri GroundPath = new("/v2/ground", UriKind.Relative);

    [Fact]
    public async Task AuthoritiesAreReturnedWithTextAndProxiedWithTenantMetadata()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ground");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(FakeConsultServer.AnswerableQuestion, "2020-01-01"), cancellationToken: TestContext.Current.CancellationToken);
        var result = await response.Content.ReadFromJsonAsync<GroundResultDto>(cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("grounded", result!.Outcome);
        Assert.Equal("direct", result.Basis);
        var authority = Assert.Single(result.Authorities!);
        Assert.Equal(("L1", "空氣污染防制法 第 32 條"), (authority.Code, authority.Citation));
        Assert.StartsWith("https://envcite/tw/", authority.Uri);
        Assert.False(string.IsNullOrEmpty(authority.Text));
        Assert.Contains(factory.Consult.Received, r =>
            r.Request.AsOf == "2020-01-01"
            && r.Headers.GetValue("x-tenant-id") == tenant.Identifier);
    }

    [Theory]
    [InlineData(FakeConsultServer.UnrelatedQuestion, "out_of_scope")]
    [InlineData(FakeConsultServer.LocalLawQuestion, "local_law_uncovered")]
    public async Task RefusalIsAResultNotAnError(string question, string reason)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("refused");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(question, null), cancellationToken: TestContext.Current.CancellationToken);
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("refused", json.RootElement.GetProperty("outcome").GetString());
        Assert.Equal(reason, json.RootElement.GetProperty("refusal").GetProperty("reason").GetString());
        Assert.False(json.RootElement.TryGetProperty("authorities", out _));
    }

    [Theory]
    [InlineData(FakeConsultServer.AnswerableQuestion, "yesterday", HttpStatusCode.BadRequest)]
    [InlineData(FakeConsultServer.UnavailableQuestion, null, HttpStatusCode.ServiceUnavailable)]
    public async Task GrpcErrorsMapToHttpStatus(string question, string? pointInTime, HttpStatusCode expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("ground-errors");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(question, pointInTime), cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(expected, response.StatusCode);
    }

    // 沒有明確 deadline 的呼叫由 client factory 補上設定的逾時，呼叫點不必各自計算。
    [Fact]
    public async Task ACallWithoutAnExplicitDeadlineGetsTheConfiguredTimeout()
    {
        const string probe = "deadline-probe";
        var timeout = factory.Services.GetRequiredService<IOptions<ConsultOptions>>().Value.Timeout;
        var consult = factory.Services.GetRequiredService<ConsultationService.ConsultationServiceClient>();
        var before = DateTime.UtcNow;

        // 沒帶租戶，fake 會拒絕；deadline 在拒絕之前已記下。
        await Assert.ThrowsAsync<RpcException>(async () =>
            await consult.GroundAsync(new GroundRequest { Question = probe }, cancellationToken: TestContext.Current.CancellationToken));

        var received = Assert.Single(factory.Consult.Received, r => r.Request.Question == probe);
        Assert.InRange(received.Deadline, before, DateTime.UtcNow + timeout);
    }

    [Fact]
    public async Task GroundRequiresApiKey()
    {
        using var client = factory.CreateClient();

        using var response = await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(FakeConsultServer.AnswerableQuestion, null), cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task GroundedAnswersAndRefusalsAreMeteredButErrorsAreNot()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("ground-metered");
        using var client = factory.CreateTenantClient(key.Key);

        foreach (var (question, pointInTime) in new[]
        {
            (FakeConsultServer.AnswerableQuestion, (string?)null),
            (FakeConsultServer.UnrelatedQuestion, null),
            (FakeConsultServer.UnavailableQuestion, null), // not metered
            (FakeConsultServer.AnswerableQuestion, "yesterday"), // not metered
        })
        {
            (await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(question, pointInTime), cancellationToken: TestContext.Current.CancellationToken)).Dispose();
        }

        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 2, 2)], usage!);
    }
}