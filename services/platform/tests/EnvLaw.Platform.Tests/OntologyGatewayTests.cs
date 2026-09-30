using System.Net;
using System.Net.Http.Json;

using EnvLaw.Platform.Modules.Metering;
using EnvLaw.Platform.Modules.Ontology;

namespace EnvLaw.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class OntologyGatewayTests(PlatformFactory factory)
{
    private const string LawPath = $"/v2/laws/{FakeOntologyServer.KnownLawId}";

    [Fact]
    public async Task GetLawProxiesWithInternalTokenAndTenantMetadata()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("gw");
        using var client = factory.CreateTenantClient(key.Key);

        var law = await client.GetFromJsonAsync<LawDto>(LawPath);

        Assert.Equal("空氣污染防制法", law!.Title);
        Assert.Contains(factory.Ontology.ReceivedMetadata, m =>
            m.GetValue("x-tenant-id") == tenant.Identifier
            && m.GetValue("authorization") == $"Bearer {FakeOntologyServer.InternalToken}"
            && !string.IsNullOrEmpty(m.GetValue("x-request-id")));
    }

    [Fact]
    public async Task ListArticlesIsProxied()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("articles");
        using var client = factory.CreateTenantClient(key.Key);

        var articles = await client.GetFromJsonAsync<ArticleDto[]>($"{LawPath}/articles");

        Assert.Equal(["1", "5-1"], articles!.Select(a => a.Number));
    }

    [Theory]
    [InlineData("unknown-act", HttpStatusCode.NotFound)]
    [InlineData(FakeOntologyServer.UnavailableLawId, HttpStatusCode.ServiceUnavailable)]
    public async Task GrpcErrorsMapToHttpStatus(string lawId, HttpStatusCode expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("errors");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.GetAsync(new Uri($"/v2/laws/{lawId}", UriKind.Relative));

        Assert.Equal(expected, response.StatusCode);
    }

    [Fact]
    public async Task OntologyEndpointsRequireApiKey()
    {
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri(LawPath, UriKind.Relative));

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task SuccessfulCallsAreMeteredPerTenant()
    {
        var (_, metered) = await factory.CreateTenantWithKeyAsync("metered");
        var (_, idle) = await factory.CreateTenantWithKeyAsync("idle");
        using var meteredClient = factory.CreateTenantClient(metered.Key);
        using var idleClient = factory.CreateTenantClient(idle.Key);

        (await meteredClient.GetAsync(new Uri(LawPath, UriKind.Relative))).Dispose();
        (await meteredClient.GetAsync(new Uri(LawPath, UriKind.Relative))).Dispose();
        (await meteredClient.GetAsync(new Uri($"{LawPath}/articles", UriKind.Relative))).Dispose();
        (await meteredClient.GetAsync(new Uri("/v2/laws/unknown-act", UriKind.Relative))).Dispose(); // not metered

        var usage = await meteredClient.GetFromJsonAsync<UsageSummary[]>("/v2/usage");
        var idleUsage = await idleClient.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal(
            [
                new UsageSummary(OntologyEndpoints.GetLawOperation, 2, 2),
                new UsageSummary(OntologyEndpoints.ListArticlesOperation, 1, 1),
            ],
            usage!);
        Assert.Empty(idleUsage!);
    }
}