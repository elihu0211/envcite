using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvLaw.Platform.Modules.Consult;
using EnvLaw.Platform.Modules.Metering;

namespace EnvLaw.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class ConsultGatewayTests(PlatformFactory factory)
{
    private static readonly Uri GroundPath = new("/v1/ground", UriKind.Relative);

    [Fact]
    public async Task EvidenceIsReturnedWithTextAndProxiedWithTenantMetadata()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ground");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath,
            new GroundRequestDto(FakeConsultServer.AnswerableQuestion, "2020-01-01"));
        var result = await response.Content.ReadFromJsonAsync<GroundResultDto>();

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("evidence", result!.Outcome);
        Assert.Equal("high", result.Confidence);
        var citation = Assert.Single(result.Citations!);
        Assert.Equal(("L1", "空氣污染防制法 第 32 條"), (citation.Code, citation.Label));
        Assert.False(string.IsNullOrEmpty(citation.Text));
        Assert.Contains(factory.Consult.Received, r =>
            r.Request.AsOf == "2020-01-01"
            && r.Headers.GetValue("x-tenant-id") == tenant.Identifier
            && r.Headers.GetValue("authorization") == $"Bearer {FakeConsultServer.InternalToken}");
    }

    [Fact]
    public async Task RefusalIsAResultNotAnError()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("refused");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath,
            new GroundRequestDto(FakeConsultServer.UnrelatedQuestion, null));
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync());

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("refused", json.RootElement.GetProperty("outcome").GetString());
        Assert.Equal("out_of_scope", json.RootElement.GetProperty("refusal").GetProperty("reason").GetString());
        Assert.False(json.RootElement.TryGetProperty("citations", out _));
    }

    [Theory]
    [InlineData(FakeConsultServer.AnswerableQuestion, "yesterday", HttpStatusCode.BadRequest)]
    [InlineData(FakeConsultServer.UnavailableQuestion, null, HttpStatusCode.ServiceUnavailable)]
    public async Task GrpcErrorsMapToHttpStatus(string question, string? asOf, HttpStatusCode expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("ground-errors");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(question, asOf));

        Assert.Equal(expected, response.StatusCode);
    }

    [Fact]
    public async Task GroundRequiresApiKey()
    {
        using var client = factory.CreateClient();

        using var response = await client.PostAsJsonAsync(GroundPath,
            new GroundRequestDto(FakeConsultServer.AnswerableQuestion, null));

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task EvidenceAndRefusalsAreMeteredButErrorsAreNot()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("ground-metered");
        using var client = factory.CreateTenantClient(key.Key);

        foreach (var (question, asOf) in new[]
        {
            (FakeConsultServer.AnswerableQuestion, (string?)null),
            (FakeConsultServer.UnrelatedQuestion, null),
            (FakeConsultServer.UnavailableQuestion, null), // not metered
            (FakeConsultServer.AnswerableQuestion, "yesterday"), // not metered
        })
        {
            (await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(question, asOf))).Dispose();
        }

        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v1/usage");

        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 2, 2)], usage!);
    }
}