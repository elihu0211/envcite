using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口的回答：先向 consult 取封閉證據（沒有就拒答、不呼叫模型），模型只根據證據作答，回答引用的每個代號都要在證據裡，
/// 否則作廢改為拒答。模型以假的 <see cref="IChatClient"/> 取代，所以測的是我們的流程與檢查，不是某個模型的行為。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class PortalAnswerTests(PlatformFactory factory)
{
    private static string SearchPage(string tenant) => $"/portal/{tenant}/search";

    private static Dictionary<string, string> Ask(string text) => new() { ["Input.Query"] = text };

    private sealed class FakeChat(Func<IReadOnlyList<ChatMessage>, string> reply, UsageDetails? usage = null) : IChatClient
    {
        public List<IReadOnlyList<ChatMessage>> Calls { get; } = [];

        public ChatOptions? LastOptions { get; private set; }

        public Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
        {
            var list = messages.ToList();
            Calls.Add(list);
            LastOptions = options;
            return Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, reply(list))) { Usage = usage });
        }

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }

    private (WebApplicationFactory<Program> Host, FakeChat Chat) Host(
        Func<IReadOnlyList<ChatMessage>, string> reply, ILoggerProvider? logs = null, UsageDetails? usage = null)
    {
        var chat = new FakeChat(reply, usage);
        var host = factory.WithWebHostBuilder(b => b
            .UseSetting("Platform:Answer:Enabled", "true")
            .UseSetting("Platform:Answer:Endpoint", "https://models.example.test/v1")
            .UseSetting("Platform:Answer:ApiKey", "test-key")
            .ConfigureLogging(l =>
            {
                if (logs is not null)
                    l.AddProvider(logs);
            })
            .ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<IChatClient>(chat))));
        return (host, chat);
    }

    private async Task<(PortalBrowser Browser, TenantResponse Tenant)> SignedInAsync(WebApplicationFactory<Program> host, bool consent = true)
    {
        var (tenant, email, initial) = await MemberAsync(factory, "ans");
        if (consent)
            await ConsentAsync(tenant.Id);

        var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (browser, tenant);
    }

    // 同意跟著資料庫裡的方案那一列：先指派方案，再開。
    private async Task ConsentAsync(string tenantId)
    {
        var plans = factory.Services.GetRequiredService<IPlanAdmin>();
        Assert.True((await plans.SetAsync(OperatorSession.Principal(), tenantId, "standard", CancellationToken.None)).IsOk);
        Assert.True((await plans.SetAiAnswerAsync(OperatorSession.Principal(), tenantId, true, CancellationToken.None)).IsOk);
    }

    private async Task<List<AuditRecord>> EventsAsync(string tenantId, string action) =>
        [.. (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenantId, Action: action), CancellationToken.None)).Items];

    [Fact]
    public async Task AGroundedQuestionIsAnsweredFromTheEvidenceAndCitesIt()
    {
        var (host, chat) = Host(_ => "在指定清除地區內不得燃燒 [L1]。");
        using var _ = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var __ = browser;

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.AnswerableQuestion));

        Assert.Contains("在指定清除地區內不得燃燒 [L1]。", page.Html, StringComparison.Ordinal);
        Assert.Contains("空氣污染防制法 第 32 條", page.Html, StringComparison.Ordinal); // 出處卡片
        Assert.Contains("這段回答由 AI 只根據下列法條產生", page.Html, StringComparison.Ordinal); // 告知
        Assert.Contains("你的問題與相關條文會送到模型服務", page.Html, StringComparison.Ordinal); // 隱私說明：問題會離開我們的系統
        var call = Assert.Single(chat.Calls);
        Assert.Contains(call, m => m.Role == ChatRole.System && m.Text.Contains("只能根據", StringComparison.Ordinal));
        Assert.Contains(call, m => m.Role == ChatRole.User
            && m.Text.Contains("[L1] 空氣污染防制法 第 32 條：在指定清除地區內，不得從事燃燒行為。", StringComparison.Ordinal)
            && m.Text.Contains(FakeConsultServer.AnswerableQuestion, StringComparison.Ordinal));
        Assert.Equal(0f, chat.LastOptions!.Temperature);

        // 取證據與模型呼叫各計一次。
        var (_, key) = (tenant, await factory.IssueKeyAsync(tenant.Id));
        using var api = factory.CreateTenantClient(key.Key);
        var usage = await api.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);
        Assert.Equal(["consult.answer", "consult.ground"], usage!.Select(u => u.Operation).Order());
    }

    [Fact]
    public async Task WhenConsultRefusesTheModelIsNeverCalled()
    {
        var (host, chat) = Host(_ => "不該被呼叫 [L1]");
        using var _ = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var __ = browser;

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.UnrelatedQuestion));

        Assert.Empty(chat.Calls);
        Assert.Contains("超出目前涵蓋的知識範圍", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("不該被呼叫", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ALocalLawQuestionIsRefusedAsOutsideTheCorpusAndTheModelIsNeverCalled()
    {
        var (host, chat) = Host(_ => "不該被呼叫 [L1]");
        using var _ = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var __ = browser;

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.LocalLawQuestion));

        Assert.Empty(chat.Calls);
        Assert.Contains("地方自治法規", page.Html, StringComparison.Ordinal);
        Assert.Contains("只涵蓋中央法規", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("超出目前涵蓋的知識範圍", page.Html, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("在指定清除地區內不得燃燒 [L9]。", "不存在的代號")]
    [InlineData("在指定清除地區內不得燃燒 [L1] 也不得露天焚燒 [L2]。", "有一個代號不存在")]
    [InlineData("在指定清除地區內不得燃燒。", "完全沒有引用")]
    [InlineData("", "空回答")]
    public async Task AnAnswerThatDoesNotMapToTheEvidenceIsDiscardedAndShownAsARefusal(string modelSaid, string why)
    {
        var (host, chat) = Host(_ => modelSaid);
        using var _ = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var __ = browser;

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.AnswerableQuestion));

        Assert.Single(chat.Calls);
        Assert.DoesNotContain("不得燃燒", page.Html, StringComparison.Ordinal); // 模型說的話不會顯示
        Assert.DoesNotContain("這段回答由 AI", page.Html, StringComparison.Ordinal);
        Assert.True(page.Html.Contains("沒有顯示", StringComparison.Ordinal) || page.Html.Contains("沒有產生", StringComparison.Ordinal), why);
    }

    [Fact]
    public async Task AModelThatSaysTheEvidenceIsNotEnoughBecomesARefusalWithAReason()
    {
        var (host, _) = Host(_ => "證據不足");
        using var h = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var b = browser;

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.AnswerableQuestion));

        Assert.Contains("找不到足以回答這個問題的法條", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("這段回答由 AI", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AModelOutageIsReportedAsUnavailableWithoutLeakingTheQuestionIntoLogs()
    {
        var logs = new CapturingLoggerProvider();
        var (host, _) = Host(_ => throw new HttpRequestException("upstream echoed: 某客戶新北某工廠的排放爭議"), logs);
        using var h = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var b = browser;

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.AnswerableQuestion));

        Assert.Contains("回答服務暫時無法使用", page.Html, StringComparison.Ordinal);
        Assert.Contains(logs.Messages, m => m.Contains("Answer model call failed", StringComparison.Ordinal));
        Assert.DoesNotContain(logs.Messages, m => m.Contains("排放爭議", StringComparison.Ordinal)); // 例外內文可能回顯提示詞，不記
        Assert.DoesNotContain(logs.Messages, m => m.Contains(FakeConsultServer.AnswerableQuestion, StringComparison.Ordinal));
    }

    [Fact]
    public async Task WithoutAModelEndpointThePortalStillListsArticlesAndNeverCallsAModel()
    {
        using var browser = new PortalBrowser(factory);
        var (tenant, email, initial) = await MemberAsync(factory, "ans-off");
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask("噪音管制標準"));

        Assert.Contains("找到 1 條", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("這段回答由 AI", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public void EnablingTheAnswerServiceWithoutItsEndpointAndKeyStopsTheHostAtStartup()
    {
        var ex = Assert.ThrowsAny<Exception>(() =>
        {
            using var bad = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Answer:Enabled", "true"));
            _ = bad.Services;
        });

        Assert.Contains("Platform:Answer:Endpoint", ex.ToString(), StringComparison.Ordinal);
        Assert.Contains("Platform:Answer:ApiKey", ex.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ATenantThatHasNotConsentedNeverReachesTheModelEvenWhenTheServiceIsOn()
    {
        var (host, chat) = Host(_ => "不該被呼叫 [L1]");
        using var h = host;
        var (browser, tenant) = await SignedInAsync(host, consent: false);
        using var b = browser;

        var page = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask("噪音管制標準"));

        Assert.Empty(chat.Calls);
        Assert.Contains("找到 1 條", page.Html, StringComparison.Ordinal); // 退回只列條文
        Assert.DoesNotContain("會送到模型服務", page.Html, StringComparison.Ordinal); // 沒送出去，就不說會送
    }

    [Fact]
    public async Task ConsentIsAnOperatorActionThatIsAuditedIdempotentAndNeedsAPlan()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ans-c");
        var plans = factory.Services.GetRequiredService<IPlanAdmin>();
        var actor = OperatorSession.Principal();

        Assert.Equal(AdminStatus.Invalid, (await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None)).Status); // 同意跟著方案走
        Assert.Equal(AdminStatus.NotFound, (await plans.SetAiAnswerAsync(actor, "no-such", true, CancellationToken.None)).Status);

        Assert.True((await plans.SetAsync(actor, tenant.Id, "standard", CancellationToken.None)).IsOk);
        Assert.True((await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None)).IsOk);
        Assert.True((await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None)).IsOk);

        Assert.Single(await EventsAsync(tenant.Id, AuditActions.TenantAiAnswerEnabled)); // 重複開不重複記
        Assert.True((await factory.Services.GetRequiredService<ITenantDirectory>().FindAsync(tenant.Id, CancellationToken.None))!.AiAnswer);

        Assert.True((await plans.SetAiAnswerAsync(actor, tenant.Id, false, CancellationToken.None)).IsOk);
        Assert.Single(await EventsAsync(tenant.Id, AuditActions.TenantAiAnswerDisabled));

        // 再開一次，然後移除方案：同意跟著那一列一起消失，不會殘留到下一個方案。
        await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None);
        Assert.True((await plans.SetAsync(actor, tenant.Id, null, CancellationToken.None)).IsOk);
        Assert.True((await plans.SetAsync(actor, tenant.Id, "standard", CancellationToken.None)).IsOk);
        Assert.False((await factory.Services.GetRequiredService<ITenantDirectory>().FindAsync(tenant.Id, CancellationToken.None))!.AiAnswer);
    }

    [Fact]
    public async Task TheTokensTheEndpointReportsAreMeteredSeparatelyFromTheCalls()
    {
        var (host, _) = Host(_ => "在指定清除地區內不得燃燒 [L1]。", usage: new UsageDetails { InputTokenCount = 321, OutputTokenCount = 45 });
        using var h = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var b = browser;

        await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.AnswerableQuestion));

        var (_, key) = (tenant, await factory.IssueKeyAsync(tenant.Id));
        using var api = factory.CreateTenantClient(key.Key);
        var usage = (await api.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken))!.ToDictionary(u => u.Operation);
        Assert.Equal(321, usage["llm.tokens.input"].Units);
        Assert.Equal(45, usage["llm.tokens.output"].Units);
        Assert.Equal(1, usage["consult.answer"].Units);
    }

    [Fact]
    public async Task AnEndpointThatReportsNoUsageIsNotGuessedAt()
    {
        var (host, _) = Host(_ => "在指定清除地區內不得燃燒 [L1]。");
        using var h = host;
        var (browser, tenant) = await SignedInAsync(host);
        using var b = browser;

        await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(FakeConsultServer.AnswerableQuestion));

        var (_, key) = (tenant, await factory.IssueKeyAsync(tenant.Id));
        using var api = factory.CreateTenantClient(key.Key);
        var operations = (await api.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken))!.Select(u => u.Operation).ToList();
        Assert.DoesNotContain("llm.tokens.input", operations);
        Assert.DoesNotContain("llm.tokens.output", operations);
    }
}