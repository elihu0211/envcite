using System.Net;
using System.Security.Claims;

using EnvCite.Platform.Web.Components;
using EnvCite.Platform.Web.Components.Portal.Workspace;
using EnvCite.Platform.Web.Components.Shared;
using EnvCite.Platform.Web.Resources;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.JSInterop;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 強型別回答文件（<see cref="AnswerDocument"/>）、確定性格式化器（<see cref="AnswerDocumentFormatter"/>）
/// 與前端元件映射（<see cref="FormattedAnswerView"/>）的單元、XSS 安全與整合渲染測試。
/// </summary>
public sealed class StructuredAnswerViewTests
{
    private sealed class FixedRetention : IConversationHistorySettings
    {
        public int RetentionDays => 30;
    }

    private sealed class NoJs : IJSRuntime
    {
        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, object?[]? args) =>
            throw new JSDisconnectedException("no browser");

        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, CancellationToken cancellationToken, object?[]? args) =>
            throw new JSDisconnectedException("no browser");
    }

    private sealed class FakeCitationArticleService : ICitationArticleService
    {
        public Task<Outcome<PortalArticleDetail>> GetByIriAsync(
            ClaimsPrincipal user, string? iri, CancellationToken cancellationToken) =>
            Task.FromResult(Outcomes.NotFound<PortalArticleDetail>("NOT_FOUND"));
    }

    private sealed class TestNavigation : NavigationManager
    {
        public TestNavigation() => Initialize("https://localhost/", "https://localhost/portal/acme");
    }

    [Fact]
    public void AnswerJsonOptionsProvideReadOnlyTypeInfoResolverForStructuredOutput()
    {
        Assert.NotNull(AnswerJson.Options.TypeInfoResolver);
        Assert.True(AnswerJson.Options.IsReadOnly);

        var format = Assert.IsType<ChatResponseFormatJson>(
            ChatResponseFormat.ForJsonSchema<ModelAnswerOutput>(AnswerJson.Options));
        Assert.True(format.Schema.HasValue);
        Assert.Contains("\"anyOf\"", format.Schema.Value.GetRawText(), StringComparison.Ordinal);
    }

    [Fact]
    public void FormatterProducesSafeMermaidMemoryTextAndCopyText()
    {
        var doc = new AnswerDocument(
        [
            new CalloutBlock(CalloutTone.Summary, "結論", "不得從事燃燒行為。", ["L1"]),
            new WorkflowBlock(
                "審查流程",
                WorkflowDirection.LeftRight,
                [
                    new WorkflowNode("end", "收到\"檢舉\"<1>#a;\\", WorkflowNodeKind.Start, ["L1"]),
                    new WorkflowNode("next", "限期改善", WorkflowNodeKind.End, ["L2"]),
                ],
                [new WorkflowEdge("end", "next", "條件|確認;[a]")]),
        ]);

        Assert.Equal(["L1", "L2"], AnswerDocumentFormatter.CollectCitationCodes(doc));

        var memory = AnswerDocumentFormatter.ToMemoryText(
            doc,
            [
                new ConversationCitation("L1", "空氣污染防制法 第 32 條", "https://example.test/32"),
                new ConversationCitation("L2", "空氣污染防制法 第 67 條", "https://example.test/67"),
            ]);
        Assert.Contains("（空氣污染防制法 第 32 條）", memory, StringComparison.Ordinal);
        Assert.Contains("（空氣污染防制法 第 67 條）", memory, StringComparison.Ordinal);
        Assert.DoesNotContain("[L1]", memory, StringComparison.Ordinal);

        var copy = AnswerDocumentFormatter.ToCopyText(doc);
        Assert.Contains("[L1]", copy, StringComparison.Ordinal);
        Assert.Contains("[L2]", copy, StringComparison.Ordinal);

        var workflow = Assert.IsType<WorkflowBlock>(doc.Blocks[1]);
        var mermaid = AnswerDocumentFormatter.ToMermaidSource(workflow);
        Assert.StartsWith("flowchart LR\n", mermaid, StringComparison.Ordinal);
        Assert.Contains("S0([\"收到'檢舉'＜1＞＃a；＼\"])", mermaid, StringComparison.Ordinal);
        Assert.Contains("S0 -->|條件/確認；［a］| S1", mermaid, StringComparison.Ordinal);
        Assert.DoesNotContain("  end(", mermaid, StringComparison.Ordinal); // 避開 Mermaid 保留字
    }

    [Fact]
    public async Task FormattedAnswerViewRendersAllDomainBlocksSafelyWithClickableCitations()
    {
        var doc = new AnswerDocument(
        [
            new CalloutBlock(CalloutTone.Caution, "合規警示", "違反者將處罰鍰 <script>alert(1)</script>。", ["L1"]),
            new ProseBlock("條文說明", "指定地區內禁止燃燒。", ["L1", "L99"]),
            new ChecklistBlock("檢查項目", false, [new ChecklistItem("不得燃燒", "含露天燃燒", true, ["L1"])]),
            new ComparisonTableBlock("管制對照", ["項目", "規定"], [new ComparisonRow(["禁止行為", "不得從事燃燒"], ["L1"])]),
            new WorkflowBlock(
                "處分流程",
                WorkflowDirection.TopDown,
                [
                    new WorkflowNode("n1", "起點", WorkflowNodeKind.Start, ["L1"]),
                    new WorkflowNode("n2", "結束", WorkflowNodeKind.End, ["L1"]),
                ],
                [new WorkflowEdge("n1", "n2")]),
        ]);

        var localizer = new KeyLocalizer();
        var services = new ServiceCollection()
            .AddSingleton<IStringLocalizer<Ui>>(localizer)
            .AddSingleton<BrandText>(new BrandText(Options.Create(new BrandOptions()), localizer))
            .BuildServiceProvider();

        AnswerSource? selected = null;
        await using var renderer = new HtmlRenderer(services, NullLoggerFactory.Instance);
        var rawHtml = await renderer.Dispatcher.InvokeAsync(async () =>
        {
            var parameters = ParameterView.FromDictionary(new Dictionary<string, object?>
            {
                [nameof(FormattedAnswerView.Document)] = doc,
                [nameof(FormattedAnswerView.Sources)] = (IReadOnlyList<AnswerSource>)[FakeAsk.Source],
                [nameof(FormattedAnswerView.OnCitationSelect)] = EventCallback.Factory.Create<AnswerSource>(
                    services, source => { selected = source; return Task.CompletedTask; }),
            });
            var rendered = await renderer.RenderComponentAsync<FormattedAnswerView>(parameters);
            return rendered.ToHtmlString();
        });

        var html = WebUtility.HtmlDecode(rawHtml);
        Assert.Contains("class=\"ep-ans-callout ep-ans-callout-caution\"", html, StringComparison.Ordinal);
        Assert.Contains("<h2 class=\"ep-ans-heading\">合規警示</h2>", html, StringComparison.Ordinal);
        Assert.DoesNotContain("<h1", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-ans-task-marker\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("type=\"checkbox\"", html, StringComparison.Ordinal);
        Assert.Contains("<table class=\"ep-legal-table ep-ans-table\">", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-mermaid\" data-mermaid-source=\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-ans-workflow-steps\"", html, StringComparison.Ordinal);
        Assert.Contains("<button type=\"button\" class=\"ep-cite-inline\"", html, StringComparison.Ordinal);
        Assert.Contains("<span class=\"ep-cite-inline ep-cite-static\">[L99]</span>", html, StringComparison.Ordinal);
        Assert.DoesNotContain("<script>alert(1)</script>", rawHtml, StringComparison.OrdinalIgnoreCase);
        Assert.Null(selected);
    }

    [Fact]
    public async Task ChatPageRendersStructuredAnswerDocumentFromHistory()
    {
        var conversationId = Guid.NewGuid();
        var doc = new AnswerDocument(
        [
            new CalloutBlock(CalloutTone.Summary, "歷史管制摘要", "指定清除地區內不得燃燒。", ["L1"]),
            new ComparisonTableBlock(null, ["條件", "要求"], [new ComparisonRow(["清除地區", "不得燃燒"], ["L1"])]),
            new WorkflowBlock(
                null,
                WorkflowDirection.TopDown,
                [
                    new WorkflowNode("n1", "稽查確認", WorkflowNodeKind.Start, ["L1"]),
                    new WorkflowNode("n2", "依法處分", WorkflowNodeKind.End, ["L1"]),
                ],
                [new WorkflowEdge("n1", "n2")]),
        ]);

        var ask = new FakeAsk
        {
            Detail = new ConversationDetail(
                conversationId,
                "露天燃燒查詢",
                DateTimeOffset.UtcNow,
                [
                    new ConversationMessageView("user", DateTimeOffset.UtcNow.AddMinutes(-1), Content: "露天燃燒如何規範"),
                    new ConversationMessageView(
                        "assistant",
                        DateTimeOffset.UtcNow,
                        Document: doc,
                        Citations: [new ConversationCitation("L1", "空氣污染防制法 第 32 條", "https://example.test/law/32")],
                        Outcome: ConversationOutcome.Answered),
                ]),
        };

        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [new Claim(PlatformClaimTypes.PortalUserId, "u1"), new Claim(PlatformClaimTypes.TenantIdentifier, "acme")], "portal"));

        var localizer = new KeyLocalizer();
        var services = new ServiceCollection()
            .AddSingleton<IAskService>(ask)
            .AddSingleton<IStringLocalizer<Ui>>(localizer)
            .AddSingleton<IConversationHistorySettings>(new FixedRetention())
            .AddSingleton<ICitationArticleService>(new FakeCitationArticleService())
            .AddSingleton<IJSRuntime>(new NoJs())
            .AddSingleton<BrandText>(new BrandText(Options.Create(new BrandOptions()), localizer))
            .AddSingleton<NavigationManager>(new TestNavigation())
            .AddSingleton<WorkspaceState>()
            .AddSingleton<ChatSession>()
            .AddSingleton<ILogger<ChatSession>>(NullLogger<ChatSession>.Instance)
            .BuildServiceProvider();

        await using var renderer = new HtmlRenderer(services, NullLoggerFactory.Instance);
        var html = await renderer.Dispatcher.InvokeAsync(async () =>
        {
            var auth = Task.FromResult(new AuthenticationState(principal));
            var parameters = ParameterView.FromDictionary(new Dictionary<string, object?>
            {
                [nameof(CascadingValue<>.Value)] = auth,
                [nameof(CascadingValue<>.ChildContent)] = (RenderFragment)(page =>
                {
                    page.OpenComponent<ChatPage>(0);
                    page.AddComponentParameter(1, nameof(ChatPage.Tenant), "acme");
                    page.AddComponentParameter(2, nameof(ChatPage.Id), conversationId);
                    page.CloseComponent();
                }),
            });
            var rendered = await renderer.RenderComponentAsync<CascadingValue<Task<AuthenticationState>>>(parameters);
            return WebUtility.HtmlDecode(rendered.ToHtmlString());
        });

        Assert.Contains("class=\"ep-ans-body\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-ans-callout ep-ans-callout-summary\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-legal-table ep-ans-table\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-mermaid\" data-mermaid-source=\"", html, StringComparison.Ordinal);
        Assert.Contains("<button type=\"button\" class=\"ep-cite-inline\"", html, StringComparison.Ordinal);
    }
}

/// <summary>驗證全站靜態自託管圖表資源（按需延遲載入）皆正確掛載。</summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class StructuredAnswerIntegrationTests(PlatformFactory factory)
{
    [Fact]
    public async Task WebHostServesDiagramAssetsLazily()
    {
        using var client = factory.CreateClient(new Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        var loginHtml = await client.GetStringAsync(new Uri("/ui/login", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Contains("design/answer-diagrams", loginHtml, StringComparison.Ordinal);
        Assert.DoesNotContain("<script src=\"_content/EnvCite.Platform.Web/design/mermaid.min", loginHtml, StringComparison.Ordinal);

        using var loaderResponse = await client.GetAsync(
            new Uri("/_content/EnvCite.Platform.Web/design/answer-diagrams.js", UriKind.Relative),
            TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, loaderResponse.StatusCode);

        using var mermaidResponse = await client.GetAsync(
            new Uri("/_content/EnvCite.Platform.Web/design/mermaid.min.js", UriKind.Relative),
            TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, mermaidResponse.StatusCode);
    }
}