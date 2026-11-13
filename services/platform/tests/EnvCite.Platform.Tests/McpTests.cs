using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;

using EnvCite.Platform.Modules.Consult;
using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Ontology;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Logging;

using ModelContextProtocol.Client;
using ModelContextProtocol.Protocol;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 給 AI 代理的 MCP 入口（<c>/v2/mcp</c>）：用真的 MCP 用戶端走完整個協定。
/// 它掛在 <c>/v2</c> 的資料群組裡，所以 API key、方案、限流與計量與 REST 是同一份；工具與 REST 共用同一份實作。
/// </summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class McpTests(PlatformFactory factory)
{
    private static readonly Uri Endpoint = new("http://localhost/v2/mcp");

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static async Task<McpClient> ConnectAsync(HttpClient http) =>
        await McpClient.CreateAsync(
            new HttpClientTransport(new HttpClientTransportOptions { Endpoint = Endpoint, TransportMode = HttpTransportMode.StreamableHttp }, http, ownsHttpClient: false),
            cancellationToken: Ct);

    private static async Task<UsageSummary[]> UsageAsync(HttpClient http) =>
        (await http.GetFromJsonAsync<UsageSummary[]>("/v2/usage", Ct))!;

    private static string TextOf(CallToolResult result) => string.Concat(result.Content.OfType<TextContentBlock>().Select(c => c.Text));

    private static JsonElement Structured(CallToolResult result) => result.StructuredContent!.Value;

    private static Dictionary<string, object?> Arguments(params (string Name, object? Value)[] arguments) =>
        arguments.ToDictionary(a => a.Name, a => a.Value);

    [Fact]
    public async Task TheServerDescribesItselfWithToolsAnnotationsResourcesAndPrompts()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-list");
        using var http = factory.CreateTenantClient(key.Key);
        await using var mcp = await ConnectAsync(http);

        var tools = await mcp.ListToolsAsync(cancellationToken: Ct);

        Assert.Equal("envcite", mcp.ServerInfo?.Name);
        Assert.False(string.IsNullOrWhiteSpace(mcp.ServerInstructions));
        Assert.Equal(
            ["evaluate_reasoning", "get_law", "ground_question", "list_articles", "search_articles"],
            tools.Select(t => t.Name).Order(StringComparer.Ordinal));
        // 全部唯讀、確定性、不碰外部世界，而且有結構化輸出的描述：用戶端據此決定要不要先問使用者、怎麼解析結果。
        Assert.All(tools, tool =>
        {
            Assert.Equal((true, true, false, false), (tool.ProtocolTool.Annotations?.ReadOnlyHint, tool.ProtocolTool.Annotations?.IdempotentHint, tool.ProtocolTool.Annotations?.DestructiveHint, tool.ProtocolTool.Annotations?.OpenWorldHint));
            Assert.NotNull(tool.ProtocolTool.OutputSchema);
            Assert.False(string.IsNullOrWhiteSpace(tool.Description));
            Assert.False(string.IsNullOrWhiteSpace(tool.ProtocolTool.Title));
        });

        var templates = await mcp.ListResourceTemplatesAsync(cancellationToken: Ct);
        Assert.Equal("envcite://laws/{jurisdiction}/{idScheme}/{localId}", Assert.Single(templates).UriTemplate);
        Assert.Equal("grounded_answer", Assert.Single(await mcp.ListPromptsAsync(cancellationToken: Ct)).Name);
    }

    [Fact]
    public async Task SearchReturnsTheArticlesAndIsMeteredOnceLikeTheRestEndpoint()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-search");
        using var http = factory.CreateTenantClient(key.Key);
        await using var mcp = await ConnectAsync(http);

        var result = await mcp.CallToolAsync("search_articles", Arguments(("query", "噪音管制標準"), ("pointInTime", "2021-01-01")), cancellationToken: Ct);

        Assert.NotEqual(true, result.IsError);
        var article = Assert.Single(Structured(result).GetProperty("articles").EnumerateArray());
        Assert.False(string.IsNullOrEmpty(article.GetProperty("uri").GetString()));
        Assert.Equal("2021-01-01", factory.Ontology.LastSearch!.AsOf);
        Assert.Equal([new UsageSummary(OntologyEndpoints.SearchOperation, 1, 1)], await UsageAsync(http));
    }

    [Fact]
    public async Task ABadInputIsAToolErrorTheModelCanReadAndIsNeverMetered()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-errors");
        using var http = factory.CreateTenantClient(key.Key);
        await using var mcp = await ConnectAsync(http);

        var badDate = await mcp.CallToolAsync("search_articles", Arguments(("query", "噪音"), ("pointInTime", "yesterday")), cancellationToken: Ct);
        var unavailable = await mcp.CallToolAsync("ground_question", Arguments(("question", FakeConsultServer.UnavailableQuestion)), cancellationToken: Ct);
        var notInForce = await mcp.CallToolAsync("get_law", Arguments(("jurisdiction", "TW-CENTRAL"), ("idScheme", "moj_pcode"), ("localId", FakeOntologyServer.KnownLawId), ("pointInTime", FakeOntologyServer.NotInForceDate)), cancellationToken: Ct);

        Assert.All(new[] { badDate, unavailable, notInForce }, r => Assert.True(r.IsError));
        Assert.Contains("ISO 8601", TextOf(badDate), StringComparison.Ordinal);
        Assert.Contains("Consult service unavailable.", TextOf(unavailable), StringComparison.Ordinal); // 框架在前面加工具名，後面是我們的穩定說明
        Assert.Contains("Law not found.", TextOf(notInForce), StringComparison.Ordinal);
        Assert.Empty(await UsageAsync(http)); // 錯誤不計量
    }

    [Fact]
    public async Task GroundingReturnsAuthoritiesOrARefusalAndBothAreMetered()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-ground");
        using var http = factory.CreateTenantClient(key.Key);
        await using var mcp = await ConnectAsync(http);

        var grounded = await mcp.CallToolAsync("ground_question", Arguments(("question", FakeConsultServer.AnswerableQuestion)), cancellationToken: Ct);
        var refused = await mcp.CallToolAsync("ground_question", Arguments(("question", FakeConsultServer.UnrelatedQuestion)), cancellationToken: Ct);

        Assert.Equal("grounded", Structured(grounded).GetProperty("outcome").GetString());
        var authority = Assert.Single(Structured(grounded).GetProperty("authorities").EnumerateArray());
        Assert.Equal("L1", authority.GetProperty("code").GetString());
        Assert.Equal("refused", Structured(refused).GetProperty("outcome").GetString());
        Assert.Equal("out_of_scope", Structured(refused).GetProperty("refusal").GetProperty("reason").GetString());
        Assert.Equal([new UsageSummary(ConsultEndpoints.GroundOperation, 2, 2)], await UsageAsync(http)); // 拒答是完成的呼叫
    }

    [Fact]
    public async Task LawsAreFetchedByToolAndByResourceAndReasoningRunsThroughTheSameGateway()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-laws");
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Plans:standard:PermitLimit", "100"));
        using var http = host.CreateClient();
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key.Key);
        await using var mcp = await ConnectAsync(http);
        var law = Arguments(("jurisdiction", "TW-CENTRAL"), ("idScheme", "moj_pcode"), ("localId", FakeOntologyServer.KnownLawId));

        var title = await mcp.CallToolAsync("get_law", law, cancellationToken: Ct);
        var articles = await mcp.CallToolAsync("list_articles", law, cancellationToken: Ct);
        var resource = await mcp.ReadResourceAsync(new Uri($"envcite://laws/TW-CENTRAL/moj_pcode/{FakeOntologyServer.KnownLawId}"), cancellationToken: Ct);
        var evaluated = await mcp.CallToolAsync("evaluate_reasoning", Arguments(("request", new { conductOn = "2020-01-01", decidedOn = "2020-06-01", facts = Array.Empty<object>() })), cancellationToken: Ct);

        Assert.Equal("空氣污染防制法", Structured(title).GetProperty("title").GetString());
        Assert.Equal(["1", "5-1"], Structured(articles).GetProperty("articles").EnumerateArray().Select(a => a.GetProperty("number").GetString()));
        using var content = JsonDocument.Parse(Assert.IsType<TextResourceContents>(Assert.Single(resource.Contents)).Text);
        Assert.Equal("空氣污染防制法", content.RootElement.GetProperty("law").GetProperty("title").GetString());
        Assert.NotEqual(true, evaluated.IsError);
        var usage = (await UsageAsync(http)).ToDictionary(u => u.Operation, u => u.Calls);
        Assert.Equal(
            (2, 2, 1),
            (usage[OntologyEndpoints.GetLawOperation], usage[OntologyEndpoints.ListArticlesOperation], usage[ReasoningEndpoints.EvaluateOperation]));
    }

    [Fact]
    public async Task ThePromptSendsTheModelThroughGroundingFirst()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-prompt");
        using var http = factory.CreateTenantClient(key.Key);
        await using var mcp = await ConnectAsync(http);

        var prompt = await mcp.GetPromptAsync("grounded_answer", new Dictionary<string, object?> { ["question"] = "露天燃燒如何處罰", ["pointInTime"] = "2020-01-01" }, cancellationToken: Ct);

        var text = Assert.IsType<TextContentBlock>(Assert.Single(prompt.Messages).Content).Text;
        Assert.Contains("ground_question", text, StringComparison.Ordinal);
        Assert.Contains("露天燃燒如何處罰", text, StringComparison.Ordinal);
        Assert.Contains("2020-01-01", text, StringComparison.Ordinal);
    }

    // 與 REST 同一道門：沒有 key 是 401，有 key 沒有方案是 402。
    [Fact]
    public async Task TheSameDoorAsRestNoKeyIs401AndNoPlanIs402()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-door");
        await using var unplanned = factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));
        using var anonymous = factory.CreateClient();
        using var withoutPlan = unplanned.CreateClient();
        withoutPlan.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key.Key);

        var initialize = new { jsonrpc = "2.0", id = 1, method = "ping" };
        using var noKey = await Post(anonymous, initialize);
        using var noPlan = await Post(withoutPlan, initialize);

        Assert.Equal(HttpStatusCode.Unauthorized, noKey.StatusCode);
        Assert.Equal(HttpStatusCode.PaymentRequired, noPlan.StatusCode);
    }

    // 無狀態：沒有 session 放在某一個副本的記憶體裡，多副本之間不需要連線黏著。
    [Fact]
    public async Task TheTransportIsStatelessSoNoReplicaHoldsASession()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-stateless");
        using var http = factory.CreateTenantClient(key.Key);

        using var response = await Post(http, new
        {
            jsonrpc = "2.0",
            id = 1,
            method = "initialize",
            @params = new { protocolVersion = "2025-06-18", capabilities = new { }, clientInfo = new { name = "test", version = "1" } },
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.False(response.Headers.Contains("Mcp-Session-Id"));
    }

    // MCP 規範要求檢查 Origin（防 DNS rebinding）：沒有列在允許清單的來源 403，列了的放行，不帶 Origin 的（CLI、桌面用戶端）不受影響。
    [Fact]
    public async Task AnOriginThatIsNotAllowedIsRefusedBeforeAnyIdentityIsChecked()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-origin");
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Mcp:AllowedOrigins:0", "https://inspector.example.com"));
        using var http = host.CreateClient();
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key.Key);
        var ping = new { jsonrpc = "2.0", id = 1, method = "ping" };

        using var evil = await Post(http, ping, origin: "https://evil.example.com");
        using var evilWithoutKey = await Post(host.CreateClient(), ping, origin: "https://evil.example.com");
        using var allowed = await Post(http, ping, origin: "https://inspector.example.com");
        using var noOrigin = await Post(http, ping);

        Assert.Equal(HttpStatusCode.Forbidden, evil.StatusCode);
        Assert.Equal(HttpStatusCode.Forbidden, evilWithoutKey.StatusCode); // 不是 401：來源檢查在驗證之前
        Assert.Equal(HttpStatusCode.OK, allowed.StatusCode);
        Assert.Equal(HttpStatusCode.OK, noOrigin.StatusCode);
    }

    [Fact]
    public async Task WhenItIsNotEnabledThereIsNoSuchEndpoint()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-off");
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Mcp:Enabled", "false"));
        using var http = host.CreateClient();
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key.Key);

        using var response = await Post(http, new { jsonrpc = "2.0", id = 1, method = "ping" });
        using var unknown = await http.PostAsync(new Uri("http://localhost/v2/no-such-endpoint"), new StringContent("{}"), Ct);

        // 沒掛就是不存在：與任何不存在的路徑同一種回應，也不是 MCP 的回應。
        Assert.Equal(unknown.StatusCode, response.StatusCode);
        Assert.NotEqual(HttpStatusCode.OK, response.StatusCode);
    }

    // 與 REST 同一個每租戶限流：握手、列舉與工具呼叫每個請求都算一次。
    [Fact]
    public async Task EveryMcpRequestCountsAgainstTheSamePerTenantLimitAsRest()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-limit");
        using var http = factory.CreateTenantClient(key.Key);
        var statuses = new List<HttpStatusCode>();

        for (var i = 0; i < PlatformFactory.RateLimitPermits + 1; i++)
        {
            using var response = await Post(http, new { jsonrpc = "2.0", id = i, method = "ping" });
            statuses.Add(response.StatusCode);
        }

        Assert.All(statuses.Take(PlatformFactory.RateLimitPermits), status => Assert.Equal(HttpStatusCode.OK, status));
        Assert.Equal(HttpStatusCode.TooManyRequests, statuses[^1]);
        using var rest = await http.GetAsync(new Uri("/v2/usage", UriKind.Relative), Ct);
        Assert.Equal(HttpStatusCode.TooManyRequests, rest.StatusCode); // REST 與 MCP 是同一個額度
    }

    // 問題與案件事實是客戶案件的內容：不進日誌（SDK 與我們的程式都一樣）。
    [Fact]
    public async Task TheQuestionNeverAppearsInTheLogs()
    {
        const string Secret = "某客戶新北某工廠的排放爭議";
        var logs = new CapturingLoggerProvider();
        var (_, key) = await factory.CreateTenantWithKeyAsync("mcp-logs");
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureLogging(l => l.SetMinimumLevel(LogLevel.Trace).AddProvider(logs)));
        using var http = host.CreateClient();
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", key.Key);
        await using var mcp = await ConnectAsync(http);

        await mcp.CallToolAsync("search_articles", Arguments(("query", Secret)), cancellationToken: Ct);
        await mcp.CallToolAsync("ground_question", Arguments(("question", Secret)), cancellationToken: Ct);

        Assert.NotEmpty(logs.Messages);
        Assert.DoesNotContain(logs.Messages, m => m.Contains(Secret, StringComparison.Ordinal));
    }

    // 用量記在呼叫者的租戶：換一個 key 看不到別人的呼叫。
    [Fact]
    public async Task UsageBelongsToTheTenantThatCalled()
    {
        var (_, keyA) = await factory.CreateTenantWithKeyAsync("mcp-a");
        var (_, keyB) = await factory.CreateTenantWithKeyAsync("mcp-b");
        using var a = factory.CreateTenantClient(keyA.Key);
        using var b = factory.CreateTenantClient(keyB.Key);
        await using var mcp = await ConnectAsync(a);

        await mcp.CallToolAsync("search_articles", Arguments(("query", "噪音")), cancellationToken: Ct);

        Assert.Single(await UsageAsync(a));
        Assert.Empty(await UsageAsync(b));
    }

    private static async Task<HttpResponseMessage> Post(HttpClient http, object body, string? origin = null)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, Endpoint) { Content = JsonContent.Create(body) };
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
        if (origin is not null)
            request.Headers.TryAddWithoutValidation("Origin", origin);

        return await http.SendAsync(request, Ct);
    }
}