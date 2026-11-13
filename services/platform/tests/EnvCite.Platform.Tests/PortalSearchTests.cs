using System.Net;

using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Ontology;
using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

/// <summary>客戶入口的條文搜尋：與 <c>POST /v2/search</c> 共用同一個服務，所以計量、錯誤與「查詢不進日誌」一致。</summary>
[Collection(PlatformTestGroup.Group4)]
public sealed class PortalSearchTests(PlatformFactory factory)
{
    private static string SearchPage(string tenant) => $"/portal/{tenant}/search";

    private static Dictionary<string, string> Query(string text, string asOf = "") =>
        new() { ["Input.Query"] = text, ["Input.AsOf"] = asOf };

    private async Task<(TenantResponse Tenant, string Email, string Password)> SignedUpAsync(string prefix, PortalBrowser browser)
    {
        var (tenant, email, initial) = await MemberAsync(factory, prefix);
        var password = await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (tenant, email, password);
    }

    [Fact]
    public async Task AMemberSearchesAndGetsCitableArticlesForTheDateTheyAskedAbout()
    {
        using var browser = new PortalBrowser(factory);
        var (tenant, _, _) = await SignedUpAsync("ps-a", browser);

        var page = await browser.GetAsync(SearchPage(tenant.Identifier));
        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("<label for=\"query\"", page.Html, StringComparison.Ordinal);

        var results = await browser.SubmitAsync(SearchPage(tenant.Identifier), Query("噪音管制標準", "2021-01-01"));

        Assert.Equal(HttpStatusCode.OK, results.Status);
        Assert.Contains("《空氣污染防制法》第 5-1 條", results.Html, StringComparison.Ordinal);
        Assert.Contains("2021-01-01 有效", results.Html, StringComparison.Ordinal); // 引用裡寫明查的是哪一天
        Assert.Contains("找到 1 條", results.Html, StringComparison.Ordinal);
        Assert.Equal(("噪音管制標準", "2021-01-01"), (factory.Ontology.LastSearch!.Query, factory.Ontology.LastSearch.AsOf));
    }

    [Fact]
    public async Task ASearchWithoutADateMeansCurrentAndIsMeteredOnce()
    {
        using var browser = new PortalBrowser(factory);
        var (tenant, _, _) = await SignedUpAsync("ps-b", browser);

        var results = await browser.SubmitAsync(SearchPage(tenant.Identifier), Query("噪音"));

        Assert.Contains("（現行）", results.Html, StringComparison.Ordinal);
        Assert.Equal(string.Empty, factory.Ontology.LastSearch!.AsOf);

        // 與 API 同一個計量：同一個租戶的用量裡看得到一次 ontology.search。
        var (_, key) = (tenant, await factory.IssueKeyAsync(tenant.Id));
        using var api = factory.CreateTenantClient(key.Key);
        var usage = await api.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);
        Assert.Equal([new UsageSummary(OntologyEndpoints.SearchOperation, 1, 1)], usage!);
    }

    [Fact]
    public async Task ATenantWithoutAPlanCannotSearchEvenByPostingTheFormDirectly()
    {
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));
        var (tenant, email, initial) = await MemberAsync(factory, "ps-c");
        using var browser = new PortalBrowser(host);
        _ = await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        var before = factory.Ontology.LastSearch;

        var page = await browser.GetAsync(SearchPage(tenant.Identifier));
        Assert.Contains("目前沒有方案", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("name=\"Input.Query\"", page.Html, StringComparison.Ordinal); // 沒有表單可填

        // 表單不顯示，但攻擊者可以直接 POST：服務端要再擋一次。這裡用有表單的頁面取得 token 再改送內容。
        var forced = Query("噪音");
        forced["_handler"] = "portal-search";
        var attempt = await browser.SubmitAsync(PasswordPage(tenant.Identifier), forced, SearchPage(tenant.Identifier));
        Assert.DoesNotContain("找到", attempt.Html, StringComparison.Ordinal);
        Assert.Same(before, factory.Ontology.LastSearch); // 內部服務根本沒被呼叫
    }

    [Fact]
    public async Task ABadDateGetsAHumanMessageAndIsNotMetered()
    {
        using var browser = new PortalBrowser(factory);
        var (tenant, _, _) = await SignedUpAsync("ps-d", browser);

        var results = await browser.SubmitAsync(SearchPage(tenant.Identifier), Query("噪音", "yesterday"));

        Assert.Equal(HttpStatusCode.OK, results.Status);
        Assert.Contains("查詢條件不正確", results.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("找到", results.Html, StringComparison.Ordinal);
        var key = await factory.IssueKeyAsync(tenant.Id);
        using var api = factory.CreateTenantClient(key.Key);
        Assert.Empty((await api.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken))!);
    }

    [Fact]
    public async Task SearchNeedsAFullSessionNotJustAnyOne()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "ps-e");
        using var anonymous = new PortalBrowser(factory);
        var redirected = await anonymous.GetAsync(SearchPage(tenant.Identifier));
        Assert.Equal(HttpStatusCode.Redirect, redirected.Status);
        Assert.StartsWith(Login(tenant.Identifier), redirected.Location, StringComparison.Ordinal);

        // 還沒改初始密碼的 session 只能進改密碼頁。
        using var pending = new PortalBrowser(factory);
        _ = await pending.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        var blocked = await pending.GetAsync(SearchPage(tenant.Identifier));
        Assert.Equal(PasswordPage(tenant.Identifier), blocked.Location);
    }

    // 顧問查的往往是客戶的案件：查詢全文不能出現在任何日誌裡。
    [Fact]
    public async Task TheQueryTextIsNeverLoggedWhenSearchingFromThePortal()
    {
        var logs = new CapturingLoggerProvider();
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureLogging(l => l.AddProvider(logs)));
        var (tenant, email, initial) = await MemberAsync(factory, "ps-f");
        using var browser = new PortalBrowser(host);
        _ = await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        _ = await browser.SubmitAsync(SearchPage(tenant.Identifier), Query("某客戶新北某工廠的排放爭議"));

        Assert.NotEmpty(logs.Messages);
        Assert.DoesNotContain(logs.Messages, m => m.Contains("排放爭議", StringComparison.Ordinal));
    }

    private static string Html(string html) => WebUtility.HtmlDecode(html);

    // 問答為主：首頁就是輸入框（置中的招呼、輸入框、範例），不再是一頁總覽。
    [Fact]
    public async Task TheHomePageIsTheAskPageWithAComposerAndExamples()
    {
        using var browser = new PortalBrowser(factory);
        var (tenant, _, _) = await SignedUpAsync("ps-g", browser);

        var home = await browser.GetAsync(Home(tenant.Identifier));
        var search = await browser.GetAsync(SearchPage(tenant.Identifier));

        foreach (var page in new[] { home, search }) // 兩個網址是同一個頁面
        {
            Assert.Equal(HttpStatusCode.OK, page.Status);
            var html = Html(page.Html);
            Assert.Contains("data-state=\"empty\"", html, StringComparison.Ordinal);
            Assert.Contains("class=\"ep-composer\"", html, StringComparison.Ordinal);
            Assert.Contains("想查哪一條法規", html, StringComparison.Ordinal);
            Assert.Contains("name=\"Input.Example\" value=\"噪音管制標準\"", html, StringComparison.Ordinal);
        }
    }

    // 範例鈕不需要先打字：直接送出那個詞。
    [Fact]
    public async Task AnExampleButtonSearchesWithoutTyping()
    {
        using var browser = new PortalBrowser(factory);
        var (tenant, _, _) = await SignedUpAsync("ps-h", browser);

        var results = await browser.SubmitAsync(Home(tenant.Identifier), new Dictionary<string, string>
        {
            ["Input.Query"] = string.Empty,
            ["Input.Example"] = "噪音管制標準",
            ["Input.AsOf"] = string.Empty,
        });

        Assert.Equal(HttpStatusCode.OK, results.Status);
        Assert.Equal("噪音管制標準", factory.Ontology.LastSearch!.Query);
        Assert.Contains("找到 1 條", results.Html, StringComparison.Ordinal);
    }

    // 像聊天：你的問題成了一個氣泡，答案在下面，輸入框清空（範例鈕不再出現）、日期留著接著問。
    [Fact]
    public async Task AskingShowsYourQuestionThenTheAnswerAndAClearedComposerThatKeepsTheDate()
    {
        using var browser = new PortalBrowser(factory);
        var (tenant, _, _) = await SignedUpAsync("ps-i", browser);

        var results = await browser.SubmitAsync(Home(tenant.Identifier), Query("噪音管制標準", "2021-01-01"));

        var html = Html(results.Html);
        Assert.Contains("data-state=\"result\"", html, StringComparison.Ordinal);
        Assert.Matches("<p class=\"ep-bubble\">\\s*噪音管制標準", html);
        Assert.Contains("《空氣污染防制法》第 5-1 條", html, StringComparison.Ordinal);
        Assert.DoesNotContain("value=\"噪音管制標準\"", html, StringComparison.Ordinal); // 輸入框清空了
        Assert.DoesNotContain("ep-examples", html, StringComparison.Ordinal);
        Assert.Contains("value=\"2021-01-01\"", html, StringComparison.Ordinal);
    }
}