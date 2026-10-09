using System.Net.Http.Json;

using EnvLaw.Platform.Modules.Metering;
using EnvLaw.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Logging;

using static EnvLaw.Platform.Tests.PortalTestSupport;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// 入口的回答：先向 consult 取封閉證據（沒有就拒答、不呼叫模型），模型只根據證據作答，回答引用的每個代號都要在證據裡，
/// 否則作廢改為拒答。模型以假的 <see cref="IChatClient"/> 取代，所以測的是我們的流程與檢查，不是某個模型的行為。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class PortalAnswerTests(PlatformFactory factory)
{
    private static string SearchPage(string tenant) => $"/portal/{tenant}/search";

    private static Dictionary<string, string> Ask(string text) => new() { ["Input.Query"] = text };

    private sealed class FakeChat(Func<IReadOnlyList<ChatMessage>, string> reply) : IChatClient
    {
        public List<IReadOnlyList<ChatMessage>> Calls { get; } = [];

        public ChatOptions? LastOptions { get; private set; }

        public Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default)
        {
            var list = messages.ToList();
            Calls.Add(list);
            LastOptions = options;
            return Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, reply(list))));
        }

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages, ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose()
        {
        }
    }

    private (WebApplicationFactory<Program> Host, FakeChat Chat) Host(Func<IReadOnlyList<ChatMessage>, string> reply, ILoggerProvider? logs = null)
    {
        var chat = new FakeChat(reply);
        var host = factory.WithWebHostBuilder(b => b
            .UseSetting("Platform:Answer:Enabled", "true")
            .UseSetting("Platform:Answer:Endpoint", "https://models.example.test/v1")
            .UseSetting("Platform:Answer:ApiKey", "test-key")
            .ConfigureLogging(l =>
            {
                if (logs is not null)
                {
                    l.AddProvider(logs);
                }
            })
            .ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<IChatClient>(chat))));
        return (host, chat);
    }

    private async Task<(PortalBrowser Browser, TenantResponse Tenant)> SignedInAsync(WebApplicationFactory<Program> host)
    {
        var (tenant, email, initial) = await MemberAsync(factory, "ans");
        var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (browser, tenant);
    }

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
        var usage = await api.GetFromJsonAsync<UsageSummary[]>("/v2/usage");
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
        Assert.Contains("超出環境法規的範圍", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("不該被呼叫", page.Html, StringComparison.Ordinal);
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
}