using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvLaw.Platform.Modules.Metering;
using EnvLaw.Platform.Modules.Ontology;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvLaw.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class SearchTests(PlatformFactory factory)
{
    private const string SearchPath = "/v2/search";
    private const string Secret = "某客戶的案件：新北某工廠的排放爭議";

    [Fact]
    public async Task SearchReturnsHitsAndPassesTheParametersThrough()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-a");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(SearchPath, new SearchRequest("噪音管制標準", "2021-01-01", 5));
        var hits = await response.Content.ReadFromJsonAsync<SearchHitDto[]>();

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var hit = Assert.Single(hits!);
        Assert.Equal(("空氣污染防制法", "5-1"), (hit.LawTitle, hit.Number));
        Assert.Equal(
            ("噪音管制標準", "2021-01-01", 5),
            (factory.Ontology.LastSearch!.Query, factory.Ontology.LastSearch.AsOf, factory.Ontology.LastSearch.Limit));
    }

    [Theory]
    [InlineData(null, null, null)]
    [InlineData("   ", null, null)]
    [InlineData("噪音", "yesterday", null)]
    public async Task BadInputIsA400WithAReasonAndIsNotMetered(string? query, string? asOf, int? limit)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-b");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(SearchPath, new SearchRequest(query, asOf, limit));
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>();
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(400, problem.GetProperty("status").GetInt32());
        Assert.Empty(usage!);
    }

    [Fact]
    public async Task ASuccessfulSearchIsMeteredOnceUnderItsOwnOperation()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("search-c");
        using var client = factory.CreateTenantClient(key.Key);

        (await client.PostAsJsonAsync(SearchPath, new SearchRequest("噪音", null, null))).Dispose();
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal([new UsageSummary(OntologyEndpoints.SearchOperation, 1, 1)], usage!);
    }

    [Fact]
    public async Task SearchRequiresAKeyAndAPlan()
    {
        using var anonymous = factory.CreateClient();
        using var unauthenticated = await anonymous.PostAsJsonAsync(SearchPath, new SearchRequest("噪音", null, null));
        Assert.Equal(HttpStatusCode.Unauthorized, unauthenticated.StatusCode);

        var (_, key) = await factory.CreateTenantWithKeyAsync("search-d");
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));
        using var unplanned = host.CreateClient();
        unplanned.DefaultRequestHeaders.Authorization = new("Bearer", key.Key);
        using var response = await unplanned.PostAsJsonAsync(SearchPath, new SearchRequest("噪音", null, null));

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

        (await client.PostAsJsonAsync(SearchPath, new SearchRequest(Secret, null, null))).Dispose();

        Assert.NotEmpty(logs.Messages); // 捕捉器確實收得到東西，否則下面的斷言沒有意義
        Assert.DoesNotContain(logs.Messages, m => m.Contains("排放爭議", StringComparison.Ordinal));
    }

    private sealed class CapturingLoggerProvider : ILoggerProvider
    {
        private readonly ConcurrentQueue<string> _messages = new();

        public IReadOnlyCollection<string> Messages => _messages;

        public ILogger CreateLogger(string categoryName) => new Logger(_messages);

        public void Dispose()
        {
        }

        private sealed class Logger(ConcurrentQueue<string> sink) : ILogger
        {
            public IDisposable? BeginScope<TState>(TState state)
                where TState : notnull => null;

            public bool IsEnabled(LogLevel logLevel) => true;

            public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter) =>
                sink.Enqueue(formatter(state, exception));
        }
    }
}