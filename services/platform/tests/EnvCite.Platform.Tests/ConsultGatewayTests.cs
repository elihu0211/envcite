using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Metering;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class ConsultGatewayTests(PlatformFactory factory)
{
    private static readonly Uri GroundPath = new("/v2/ground", UriKind.Relative);

    [Fact]
    public async Task AuthoritiesAreReturnedWithTextAndProxiedWithTenantMetadata()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ground");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath,
            new GroundRequestDto(FakeConsultServer.AnswerableQuestion, "2020-01-01"));
        var result = await response.Content.ReadFromJsonAsync<GroundResultDto>();

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
        Assert.False(json.RootElement.TryGetProperty("authorities", out _));
    }

    [Theory]
    [InlineData(FakeConsultServer.AnswerableQuestion, "yesterday", HttpStatusCode.BadRequest)]
    [InlineData(FakeConsultServer.UnavailableQuestion, null, HttpStatusCode.ServiceUnavailable)]
    public async Task GrpcErrorsMapToHttpStatus(string question, string? pointInTime, HttpStatusCode expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("ground-errors");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(question, pointInTime));

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
            (await client.PostAsJsonAsync(GroundPath, new GroundRequestDto(question, pointInTime))).Dispose();
        }

        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 2, 2)], usage!);
    }
}