using System.Net;

using EnvCite.Platform.Modules.Canon;
using EnvCite.Platform.Modules.Metering;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group4)]
public sealed class CanonGatewayTests(PlatformFactory factory)
{
    private const string LawPath = $"{FakeCanonServer.CentralLawsPath}{FakeCanonServer.KnownLawId}";

    [Fact]
    public async Task GetLawProxiesWithTenantMetadata()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("gw");
        using var client = factory.CreateTenantClient(key.Key);

        var law = await client.GetFromJsonAsync<LawDto>(LawPath, cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal("空氣污染防制法", law!.Title);
        Assert.Contains(factory.Canon.ReceivedMetadata, m =>
            m.GetValue("x-tenant-id") == tenant.Identifier
            && !string.IsNullOrEmpty(m.GetValue("x-request-id")));
    }

    [Fact]
    public async Task TheThreeSegmentsReachTheCanonServiceAndTheKeyComesBackWhole()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("key");
        using var client = factory.CreateTenantClient(key.Key);

        var law = await client.GetFromJsonAsync<LawDto>(LawPath, cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(new LawIdentifierDto("TW-CENTRAL", "moj_pcode", FakeCanonServer.KnownLawId), law!.Identifier);
        Assert.Equal("TW-CENTRAL", factory.Canon.LastIdentifier!.Jurisdiction);
        Assert.Equal("moj_pcode", factory.Canon.LastIdentifier.IdScheme);
        Assert.Equal(FakeCanonServer.KnownLawId, factory.Canon.LastIdentifier.LocalId);
    }

    [Theory]
    [InlineData("/v2/laws/O0020001")]
    [InlineData("/v2/laws/moj_pcode/O0020001")]
    [InlineData("/v2/laws/TW-CENTRAL:moj_pcode:O0020001")]
    public async Task ALawNamedWithoutAllThreeSegmentsIsNotGuessedAtAndNeverReachesTheCanonService(string path)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("segments");
        using var client = factory.CreateTenantClient(key.Key);
        factory.Canon.ReceivedMetadata.Clear();

        using var response = await client.GetAsync(new Uri(path, UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.Empty(factory.Canon.ReceivedMetadata);
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

        using var response = await client.GetAsync(new Uri($"{FakeCanonServer.CentralLawsPath}{FakeCanonServer.FlakyLawId}", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(2, factory.Canon.GetLawCalls[FakeCanonServer.FlakyLawId]);
    }

    [Theory]
    [InlineData("unknown-act", HttpStatusCode.NotFound)]
    [InlineData(FakeCanonServer.UnavailableLawId, HttpStatusCode.ServiceUnavailable)]
    [InlineData(FakeCanonServer.BusyLawId, HttpStatusCode.ServiceUnavailable)]
    public async Task GrpcErrorsMapToHttpStatus(string lawId, HttpStatusCode expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("errors");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.GetAsync(new Uri($"{FakeCanonServer.CentralLawsPath}{lawId}", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(expected, response.StatusCode);
    }

    [Theory]
    [InlineData(LawPath)]
    [InlineData($"{LawPath}/articles")]
    public async Task AsOfIsPassedToTheCanonService(string path)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("asof-a");
        using var client = factory.CreateTenantClient(key.Key);

        using var current = await client.GetAsync(new Uri(path, UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(string.Empty, factory.Canon.LastAsOf); // 省略 = 現行

        using var dated = await client.GetAsync(new Uri($"{path}?asOf=2021-01-01", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, dated.StatusCode);
        Assert.Equal("2021-01-01", factory.Canon.LastAsOf);
    }

    [Theory]
    [InlineData(LawPath, "yesterday", HttpStatusCode.BadRequest)]
    [InlineData(LawPath, FakeCanonServer.NotInForceDate, HttpStatusCode.NotFound)] // 該日沒有有效版本，不退回現行
    [InlineData($"{LawPath}/articles", "yesterday", HttpStatusCode.BadRequest)]
    [InlineData($"{LawPath}/articles", FakeCanonServer.NotInForceDate, HttpStatusCode.NotFound)]
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
    public async Task CanonEndpointsRequireApiKey()
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
        (await meteredClient.GetAsync(new Uri($"{FakeCanonServer.CentralLawsPath}unknown-act", UriKind.Relative), TestContext.Current.CancellationToken)).Dispose(); // not metered

        var usage = await meteredClient.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);
        var idleUsage = await idleClient.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(
            [
                new UsageSummary(CanonEndpoints.GetLawOperation, 2, 2),
                new UsageSummary(CanonEndpoints.ListArticlesOperation, 1, 1),
            ],
            usage!);
        Assert.Empty(idleUsage!);
    }
}