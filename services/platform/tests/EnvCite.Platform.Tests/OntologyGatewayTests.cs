using System.Net;
using System.Net.Http.Json;

using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Ontology;
using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class OntologyGatewayTests(PlatformFactory factory)
{
    private const string LawPath = $"{FakeOntologyServer.CentralLawsPath}{FakeOntologyServer.KnownLawId}";

    [Fact]
    public async Task GetLawProxiesWithTenantMetadata()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("gw");
        using var client = factory.CreateTenantClient(key.Key);

        var law = await client.GetFromJsonAsync<LawDto>(LawPath, cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal("空氣污染防制法", law!.Title);
        Assert.Contains(factory.Ontology.ReceivedMetadata, m =>
            m.GetValue("x-tenant-id") == tenant.Identifier
            && !string.IsNullOrEmpty(m.GetValue("x-request-id")));
    }

    [Fact]
    public async Task TheThreeSegmentsReachTheOntologyServiceAndTheKeyComesBackWhole()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("key");
        using var client = factory.CreateTenantClient(key.Key);

        var law = await client.GetFromJsonAsync<LawDto>(LawPath, cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(new LawIdentifierDto("TW-CENTRAL", "moj_pcode", FakeOntologyServer.KnownLawId), law!.Identifier);
        Assert.Equal("TW-CENTRAL", factory.Ontology.LastIdentifier!.Jurisdiction);
        Assert.Equal("moj_pcode", factory.Ontology.LastIdentifier.IdScheme);
        Assert.Equal(FakeOntologyServer.KnownLawId, factory.Ontology.LastIdentifier.LocalId);
    }

    [Theory]
    [InlineData("/v2/laws/O0020001")]
    [InlineData("/v2/laws/moj_pcode/O0020001")]
    [InlineData("/v2/laws/TW-CENTRAL:moj_pcode:O0020001")]
    public async Task ALawNamedWithoutAllThreeSegmentsIsNotGuessedAtAndNeverReachesTheOntologyService(string path)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("segments");
        using var client = factory.CreateTenantClient(key.Key);
        factory.Ontology.ReceivedMetadata.Clear();

        using var response = await client.GetAsync(new Uri(path, UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.Empty(factory.Ontology.ReceivedMetadata);
    }

    [Fact]
    public async Task ListArticlesIsProxied()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("articles");
        using var client = factory.CreateTenantClient(key.Key);

        var articles = await client.GetFromJsonAsync<ArticleDto[]>($"{LawPath}/articles", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(["1", "5-1"], articles!.Select(a => a.Number));
    }

    [Fact]
    public async Task ATransientUnavailableIsRetriedAndTheCallerNeverSeesIt()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("retry");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.GetAsync(new Uri($"{FakeOntologyServer.CentralLawsPath}{FakeOntologyServer.FlakyLawId}", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(2, factory.Ontology.GetLawCalls[FakeOntologyServer.FlakyLawId]);
    }

    [Theory]
    [InlineData("unknown-act", HttpStatusCode.NotFound)]
    [InlineData(FakeOntologyServer.UnavailableLawId, HttpStatusCode.ServiceUnavailable)]
    public async Task GrpcErrorsMapToHttpStatus(string lawId, HttpStatusCode expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("errors");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.GetAsync(new Uri($"{FakeOntologyServer.CentralLawsPath}{lawId}", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(expected, response.StatusCode);
    }

    [Theory]
    [InlineData(LawPath)]
    [InlineData($"{LawPath}/articles")]
    public async Task AsOfIsPassedToTheOntologyService(string path)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("asof-a");
        using var client = factory.CreateTenantClient(key.Key);

        using var current = await client.GetAsync(new Uri(path, UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(string.Empty, factory.Ontology.LastAsOf); // 省略 = 現行

        using var dated = await client.GetAsync(new Uri($"{path}?asOf=2021-01-01", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, dated.StatusCode);
        Assert.Equal("2021-01-01", factory.Ontology.LastAsOf);
    }

    [Theory]
    [InlineData(LawPath, "yesterday", HttpStatusCode.BadRequest)]
    [InlineData(LawPath, FakeOntologyServer.NotInForceDate, HttpStatusCode.NotFound)] // 該日沒有有效版本，不退回現行
    [InlineData($"{LawPath}/articles", "yesterday", HttpStatusCode.BadRequest)]
    [InlineData($"{LawPath}/articles", FakeOntologyServer.NotInForceDate, HttpStatusCode.NotFound)]
    public async Task ABadOrNotInForceDateIsAnErrorAndIsNotMetered(string path, string asOf, HttpStatusCode expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("asof-b");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.GetAsync(new Uri($"{path}?asOf={asOf}", UriKind.Relative), TestContext.Current.CancellationToken);
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(expected, response.StatusCode);
        Assert.Empty(usage!);
    }

    [Fact]
    public async Task OntologyEndpointsRequireApiKey()
    {
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri(LawPath, UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task SuccessfulCallsAreMeteredPerTenant()
    {
        var (_, metered) = await factory.CreateTenantWithKeyAsync("metered");
        var (_, idle) = await factory.CreateTenantWithKeyAsync("idle");
        using var meteredClient = factory.CreateTenantClient(metered.Key);
        using var idleClient = factory.CreateTenantClient(idle.Key);

        (await meteredClient.GetAsync(new Uri(LawPath, UriKind.Relative), TestContext.Current.CancellationToken)).Dispose();
        (await meteredClient.GetAsync(new Uri(LawPath, UriKind.Relative), TestContext.Current.CancellationToken)).Dispose();
        (await meteredClient.GetAsync(new Uri($"{LawPath}/articles", UriKind.Relative), TestContext.Current.CancellationToken)).Dispose();
        (await meteredClient.GetAsync(new Uri($"{FakeOntologyServer.CentralLawsPath}unknown-act", UriKind.Relative), TestContext.Current.CancellationToken)).Dispose(); // not metered

        var usage = await meteredClient.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);
        var idleUsage = await idleClient.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(
            [
                new UsageSummary(OntologyEndpoints.GetLawOperation, 2, 2),
                new UsageSummary(OntologyEndpoints.ListArticlesOperation, 1, 1),
            ],
            usage!);
        Assert.Empty(idleUsage!);
    }
}