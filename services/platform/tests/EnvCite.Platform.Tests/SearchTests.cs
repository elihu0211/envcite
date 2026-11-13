using System.Net;
using System.Text.Json;

using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Ontology;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group4)]
public sealed class SearchTests(PlatformFactory factory)
{
    private const string SearchPath = "/v2/search";
    private const string Secret = "某客戶的案件：新北某工廠的排放爭議";

    [Fact]
    public async Task SearchReturnsHitsAndPassesTheParametersThrough()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-a");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(SearchPath, new SearchRequest("噪音管制標準", "2021-01-01", 5), cancellationToken: TestContext.Current.CancellationToken);
        var hits = await response.Content.ReadFromJsonAsync<SearchHitDto[]>(cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var hit = Assert.Single(hits!);
        Assert.Equal(("空氣污染防制法", "5-1"), (hit.LawTitle, hit.Number));
        Assert.Equal(
            ("噪音管制標準", "2021-01-01", 5),
            (factory.Ontology.LastSearch!.Query, factory.Ontology.LastSearch.AsOf, factory.Ontology.LastSearch.Limit));
    }

    [Theory]
    [InlineData("weak-match", 0)]
    [InlineData("fragment", 0)]
    [InlineData("whole-term", 1)]
    [InlineData("whole-term-without-lexical", 0)]
    [InlineData("semantic-only", 1)]
    public async Task OnlyHitsAboveTheRelevanceFloorAreReturned(string query, int expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync($"search-floor-{query}");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(SearchPath, new SearchRequest(query, null, null), cancellationToken: TestContext.Current.CancellationToken);
        var hits = await response.Content.ReadFromJsonAsync<SearchHitDto[]>(cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(expected, hits!.Length);
    }

    [Fact]
    public async Task AGuidanceArticleIsReturnedWithItsCountAndTheRequestAsksForGuidance()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-guidance");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(SearchPath, new SearchRequest("guidance-only", null, null), cancellationToken: TestContext.Current.CancellationToken);
        var hits = await response.Content.ReadFromJsonAsync<SearchHitDto[]>(cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(2, Assert.Single(hits!).GuidanceDocuments);
        Assert.True(factory.Ontology.LastSearch!.IncludeGuidance);
    }

    [Theory]
    [InlineData(null, 10)]
    [InlineData(3, 3)]
    public async Task TheResponseNeverExceedsTheRequestedLimit(int? limit, int expected)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync($"search-limit-{limit}");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(SearchPath, new SearchRequest("many", null, limit), cancellationToken: TestContext.Current.CancellationToken);
        var hits = await response.Content.ReadFromJsonAsync<SearchHitDto[]>(cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(expected, hits!.Length);
    }

    [Theory]
    [InlineData(null, null, null)]
    [InlineData("   ", null, null)]
    [InlineData("噪音", "yesterday", null)]
    public async Task BadInputIsA400WithAReasonAndIsNotMetered(string? query, string? asOf, int? limit)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-b");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(SearchPath, new SearchRequest(query, asOf, limit), cancellationToken: TestContext.Current.CancellationToken);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken);
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(400, problem.GetProperty("status").GetInt32());
        Assert.Empty(usage!);
    }

    [Fact]
    public async Task ABusyOntologyIsUnavailable()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-busy");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(
            SearchPath, new SearchRequest("pool-busy", null, null), cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
    }

    [Fact]
    public async Task ASuccessfulSearchIsMeteredOnceUnderItsOwnOperation()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-c");
        using var client = factory.CreateTenantClient(key.Key);

        (await client.PostAsJsonAsync(SearchPath, new SearchRequest("噪音", null, null), cancellationToken: TestContext.Current.CancellationToken)).Dispose();
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal([new UsageSummary(OntologyEndpoints.SearchOperation, 1, 1)], usage!);
    }

    [Fact]
    public async Task SearchRequiresAKeyAndAPlan()
    {
        using var anonymous = factory.CreateClient();
        using var unauthenticated = await anonymous.PostAsJsonAsync(SearchPath, new SearchRequest("噪音", null, null), cancellationToken: TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Unauthorized, unauthenticated.StatusCode);

        var (_, key) = await factory.CreateTenantWithKeyAsync("search-d");
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));
        using var unplanned = host.CreateClient();
        unplanned.DefaultRequestHeaders.Authorization = new("Bearer", key.Key);
        using var response = await unplanned.PostAsJsonAsync(SearchPath, new SearchRequest("噪音", null, null), cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.PaymentRequired, response.StatusCode);
    }

    // 顧問查的往往是客戶的案件：查詢全文不能出現在本服務的任何日誌裡（含請求日誌）。
    [Fact]
    public async Task TheQueryTextIsNeverWrittenToTheLogs()
    {
        var logs = new CapturingLoggerProvider();
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureLogging(l => l.AddProvider(logs)));
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-e");
        using var client = host.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", key.Key);

        (await client.PostAsJsonAsync(SearchPath, new SearchRequest(Secret, null, null), cancellationToken: TestContext.Current.CancellationToken)).Dispose();

        Assert.NotEmpty(logs.Messages); // 捕捉器確實收得到東西，否則下面的斷言沒有意義
        Assert.DoesNotContain(logs.Messages, m => m.Contains("排放爭議", StringComparison.Ordinal));
    }
}