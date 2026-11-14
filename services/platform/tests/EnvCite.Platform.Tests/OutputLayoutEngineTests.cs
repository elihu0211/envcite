using System.Net;
using System.Security.Claims;

using EnvCite.Platform.Web.Components;
using EnvCite.Platform.Web.Components.Portal.Workspace;
using EnvCite.Platform.Web.Components.Shared;
using EnvCite.Platform.Web.Presentation.Output;
using EnvCite.Platform.Web.Resources;

using Markdig.Syntax;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.JSInterop;

namespace EnvCite.Platform.Tests;

/// <summary>
/// AI 輸出排版引擎與 <see cref="FormattedAnswerView"/> 的單元、DI 擴充性、XSS 安全與元件渲染測試。
/// </summary>
public sealed class OutputLayoutEngineTests
{
    private sealed class CustomHeadingConverter : IOutputBlockConverter
    {
        public int Order => 50;

        public bool CanConvert(Block block, OutputLayoutContext context) => block is HeadingBlock;

        public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
        {
            var heading = (HeadingBlock)block;
            var inlines = context.Inlines.ParseMarkdigInlines(heading.Inline, context.Options);
            target.Add(new OutputQuoteBlock([new OutputParagraphBlock(inlines)]));
        }
    }

    private sealed class HistoryOn : IConversationHistoryAvailability
    {
        public bool Enabled => true;

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
        public Task<AdminOutcome<PortalArticleDetail>> GetByIriAsync(
            ClaimsPrincipal user, string? iri, CancellationToken cancellationToken) =>
            Task.FromResult(AdminOutcome.NotFound<PortalArticleDetail>("NOT_FOUND"));
    }

    private sealed class TestNavigation : NavigationManager
    {
        public TestNavigation() => Initialize("https://localhost/", "https://localhost/portal/acme");
    }

    private static IOutputLayoutEngine CreateEngine(Action<OutputLayoutOptions>? configure = null)
    {
        var provider = new ServiceCollection()
            .AddOutputLayoutEngine(configure)
            .BuildServiceProvider();
        return provider.GetRequiredService<IOutputLayoutEngine>();
    }

    [Fact]
    public void DiRegistrationResolvesStatelessSingletonsAndRespectsConverterPriority()
    {
        var services = new ServiceCollection().AddOutputLayoutEngine();
        services.AddSingleton<IOutputBlockConverter, CustomHeadingConverter>();
        var provider = services.BuildServiceProvider();

        var first = provider.GetRequiredService<IOutputLayoutEngine>();
        var second = provider.GetRequiredService<IOutputLayoutEngine>();
        Assert.Same(first, second);

        var converters = provider.GetServices<IOutputBlockConverter>().ToList();
        Assert.True(converters.Count >= 10);

        var doc = first.Parse("# 法規摘要 [L1]");
        var quote = Assert.IsType<OutputQuoteBlock>(Assert.Single(doc.Blocks));
        var para = Assert.IsType<OutputParagraphBlock>(Assert.Single(quote.Blocks));
        Assert.Contains(para.Inlines, static i => i is OutputCitationInline { Code: "L1" });
    }

    [Fact]
    public void ParseSupportsHeadingsOrderedListsTasksQuotesCodeAndInlineStyles()
    {
        const string markdown = """
            # 管制重點

            依 **噪音管制標準** 與 *法定程序* 檢視 [L1]，參見 `第 5 條`：

            1. 完成量測申報 [L2]
            2. 限期改善複查

            - [x] 已完成排放登錄 [L1]
            - [ ] 尚未補正文件

            > 主管機關得命限期改善 [L2]。

            ```json
            { "article": "5" }
            ```

            ---
            """;

        var engine = CreateEngine();
        var doc = engine.Parse(markdown);

        Assert.Equal(7, doc.Blocks.Count);

        var heading = Assert.IsType<OutputHeadingBlock>(doc.Blocks[0]);
        Assert.Equal(1, heading.Level);
        Assert.Contains(heading.Inlines, static i => i is OutputTextInline { Text: "管制重點" });

        var paragraph = Assert.IsType<OutputParagraphBlock>(doc.Blocks[1]);
        Assert.Contains(paragraph.Inlines, static i => i is OutputStrongInline);
        Assert.Contains(paragraph.Inlines, static i => i is OutputEmphasisInline);
        Assert.Contains(paragraph.Inlines, static i => i is OutputCitationInline { Code: "L1" });
        Assert.Contains(paragraph.Inlines, static i => i is OutputCodeInline { Code: "第 5 條" });

        var ordered = Assert.IsType<OutputListBlock>(doc.Blocks[2]);
        Assert.True(ordered.Ordered);
        Assert.Equal(2, ordered.Items.Count);
        Assert.Null(ordered.Items[0].IsChecked);

        var tasks = Assert.IsType<OutputListBlock>(doc.Blocks[3]);
        Assert.False(tasks.Ordered);
        Assert.Equal(true, tasks.Items[0].IsChecked);
        Assert.Equal(false, tasks.Items[1].IsChecked);

        var quote = Assert.IsType<OutputQuoteBlock>(doc.Blocks[4]);
        Assert.IsType<OutputParagraphBlock>(Assert.Single(quote.Blocks));

        var code = Assert.IsType<OutputCodeBlock>(doc.Blocks[5]);
        Assert.Equal("json", code.Language);
        Assert.Contains("\"article\"", code.Code, StringComparison.Ordinal);

        Assert.IsType<OutputThematicBreakBlock>(doc.Blocks[6]);
    }

    [Fact]
    public void ParseSupportsGfmPipeTablesWithAlignmentsAndInlineCitations()
    {
        const string markdown = """
            | 管制區 | 日間音量 | 依據 |
            | :--- | :---: | ---: |
            | 第一類 | **55** dB | [L1] |
            | 第二類 | 57 dB | [L2] |
            """;

        var doc = CreateEngine().Parse(markdown);
        var table = Assert.IsType<OutputTableBlock>(Assert.Single(doc.Blocks));

        var header = Assert.Single(table.HeaderRows);
        Assert.Equal(3, header.Count);
        Assert.Equal(OutputCellAlignment.Left, header[0].Alignment);
        Assert.Equal(OutputCellAlignment.Center, header[1].Alignment);
        Assert.Equal(OutputCellAlignment.Right, header[2].Alignment);

        Assert.Equal(2, table.BodyRows.Count);
        Assert.Contains(table.BodyRows[0][1].Inlines, static i => i is OutputStrongInline);
        Assert.Contains(table.BodyRows[0][2].Inlines, static i => i is OutputCitationInline { Code: "L1" });
        Assert.Contains(table.BodyRows[1][2].Inlines, static i => i is OutputCitationInline { Code: "L2" });
    }

    [Fact]
    public void ParsePromotesMermaidFencesAndRespectsDisabledDiagramOption()
    {
        const string markdown = """
            ```mermaid
            flowchart TD
              A[申報] --> B[核定]
            ```
            """;

        var docWithDiagrams = CreateEngine().Parse(markdown);
        var diagram = Assert.IsType<OutputDiagramBlock>(Assert.Single(docWithDiagrams.Blocks));
        Assert.Equal("mermaid", diagram.Language);
        Assert.Contains("flowchart TD", diagram.Source, StringComparison.Ordinal);

        var docWithoutDiagrams = CreateEngine(o => o.EnableMermaidDiagrams = false).Parse(markdown);
        var fallbackCode = Assert.IsType<OutputCodeBlock>(Assert.Single(docWithoutDiagrams.Blocks));
        Assert.Equal("mermaid", fallbackCode.Language);
    }

    [Fact]
    public void ParseRehydratesLegacyBoxDrawingTablesWithInlineCitations()
    {
        const string text = """
            依表列標準判定 [L1]：
            ┌───┬───┐
            │ 類別 │ 基準 │
            ├───┼───┤
            │ 一類 │ 50dB │
            └───┴───┘
            """;

        var doc = CreateEngine().Parse(text);

        Assert.Equal(2, doc.Blocks.Count);
        var intro = Assert.IsType<OutputParagraphBlock>(doc.Blocks[0]);
        Assert.Contains(intro.Inlines, static i => i is OutputCitationInline { Code: "L1" });

        var table = Assert.IsType<OutputTableBlock>(doc.Blocks[1]);
        Assert.Single(table.HeaderRows);
        Assert.Single(table.BodyRows);
        Assert.Contains(table.BodyRows[0][1].Inlines, static i => i is OutputTextInline t && t.Text.Contains("50dB", StringComparison.Ordinal));
    }

    [Fact]
    public void ParseEncodesRawHtmlAndRejectsUnsafeLinkSchemes()
    {
        const string payload = """
            <script>alert(1)</script>

            [危險連結](javascript:alert(1)) 與 [資料連結](data:text/html;base64,PHNjcmlwdD4=)

            [法規資料庫](https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=O0030006) 與 [工作區](/portal/acme)
            """;

        var doc = CreateEngine().Parse(payload);
        var links = doc.Blocks
            .OfType<OutputParagraphBlock>()
            .SelectMany(static p => p.Inlines)
            .OfType<OutputLinkInline>()
            .ToList();

        Assert.Equal(2, links.Count);
        Assert.All(links, static link =>
            Assert.True(
                link.Href.StartsWith("https://", StringComparison.Ordinal) || link.Href.StartsWith('/'),
                link.Href));
        Assert.DoesNotContain(doc.Blocks, static b => b is OutputDiagramBlock);
    }

    [Fact]
    public async Task FormattedAnswerViewRendersTablesMermaidAndClickableInlineCitations()
    {
        const string content = """
            # 審查結論

            請依規定辦理 [L1]；未附清單參見 [L99] 與 <script>alert(1)</script> 與 [點我](javascript:alert(1))。

            | 項目 | 說明 | 來源 |
            | :--- | :---: | ---: |
            | 禁止行為 | **不得燃燒** | [L1] |

            - [x] 檢查排放規定

            ```mermaid
            flowchart LR
              A[起點] --> B[結束]
            ```
            """;

        var localizer = new KeyLocalizer();
        var services = new ServiceCollection()
            .AddSingleton<IStringLocalizer<Ui>>(localizer)
            .AddSingleton<BrandText>(new BrandText(Options.Create(new BrandOptions()), localizer))
            .AddOutputLayoutEngine()
            .BuildServiceProvider();

        AnswerSource? selected = null;
        await using var renderer = new HtmlRenderer(services, NullLoggerFactory.Instance);
        var rawHtml = await renderer.Dispatcher.InvokeAsync(async () =>
        {
            var parameters = ParameterView.FromDictionary(new Dictionary<string, object?>
            {
                [nameof(FormattedAnswerView.Content)] = content,
                [nameof(FormattedAnswerView.Sources)] = (IReadOnlyList<AnswerSource>)[FakeAsk.Source],
                [nameof(FormattedAnswerView.OnCitationSelect)] = EventCallback.Factory.Create<AnswerSource>(
                    services, source => { selected = source; return Task.CompletedTask; }),
            });
            var rendered = await renderer.RenderComponentAsync<FormattedAnswerView>(parameters);
            return rendered.ToHtmlString();
        });

        var html = WebUtility.HtmlDecode(rawHtml);
        Assert.Contains("<h2 class=\"ep-md-heading\">審查結論</h2>", html, StringComparison.Ordinal);
        Assert.DoesNotContain("<h1", html, StringComparison.Ordinal);
        Assert.Contains("<div class=\"ep-legal-table-wrap\"", html, StringComparison.Ordinal);
        Assert.Contains("<table class=\"ep-legal-table ep-md-table\">", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-align-center\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-align-right\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-md-task-marker\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("type=\"checkbox\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-mermaid\" data-mermaid-source=\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-mermaid-fallback\"", html, StringComparison.Ordinal);
        Assert.Contains("<button type=\"button\" class=\"ep-cite-inline\"", html, StringComparison.Ordinal);
        Assert.Contains("<span class=\"ep-cite-inline ep-cite-static\">", html, StringComparison.Ordinal);
        Assert.Contains("[L99]", html, StringComparison.Ordinal);
        Assert.DoesNotContain("<script>alert(1)</script>", rawHtml, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("javascript:", rawHtml, StringComparison.OrdinalIgnoreCase);
        Assert.Null(selected);
    }

    [Fact]
    public async Task ChatPageRendersStoredMarkdownTableAndMermaidFromHistory()
    {
        var conversationId = Guid.NewGuid();
        const string storedMarkdown = """
            ## 歷史管制比較 [L1]

            | 條件 | 要求 |
            | :--- | :--- |
            | 清除地區 | 不得燃燒 [L1] |

            ```mermaid
            sequenceDiagram
              稽查員->>業者: 確認違反禁止行為
            ```
            """;

        var ask = new FakeAsk
        {
            Detail = new ConversationDetail(
                conversationId,
                "露天燃燒查詢",
                DateTimeOffset.UtcNow,
                [
                    new ConversationMessageView("user", "露天燃燒如何規範", DateTimeOffset.UtcNow.AddMinutes(-1)),
                    new ConversationMessageView(
                        "assistant",
                        storedMarkdown,
                        DateTimeOffset.UtcNow,
                        [new ConversationCitation("L1", "空氣污染防制法 第 32 條", "https://example.test/law/32")],
                        Outcome: ConversationOutcome.Answered),
                ]),
        };

        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [new Claim(PlatformClaimTypes.PortalUserId, "u1"), new Claim(PlatformClaimTypes.TenantIdentifier, "acme")], "portal"));

        var localizer = new KeyLocalizer();
        var services = new ServiceCollection()
            .AddSingleton<IAskService>(ask)
            .AddSingleton<IStringLocalizer<Ui>>(localizer)
            .AddOutputLayoutEngine()
            .AddSingleton<IConversationHistoryAvailability>(new HistoryOn())
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

        Assert.Contains("class=\"ep-md-body\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-legal-table ep-md-table\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-mermaid\" data-mermaid-source=\"", html, StringComparison.Ordinal);
        Assert.Contains("sequenceDiagram", html, StringComparison.Ordinal);
        Assert.Contains("<button type=\"button\" class=\"ep-cite-inline\"", html, StringComparison.Ordinal);
    }
}

/// <summary>驗證全站 DI 與靜態自託管圖表資源（按需延遲載入）皆正確掛載。</summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class OutputLayoutIntegrationTests(PlatformFactory factory)
{
    [Fact]
    public async Task WebHostRegistersOutputLayoutEngineAndServesDiagramAssetsLazily()
    {
        Assert.NotNull(factory.Services.GetService<IOutputLayoutEngine>());

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