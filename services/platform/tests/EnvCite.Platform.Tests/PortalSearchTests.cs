using System.Net;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 客戶入口的問答首頁（互動式工作區）的進入條件與第一個畫面：要有完整的 session、要有方案，首頁就是輸入框。
/// 搜尋與回答本身（計量、輸入錯誤、日期、查詢不進日誌）在 <see cref="AskServiceTests"/>，互動的流程在 <c>ChatSessionTests</c>。
/// 這裡只看伺服器預先渲染出來的第一個畫面；連線之後的互動不經 HTTP，不在這裡。
/// </summary>
[Collection(PlatformTestGroup.Group4)]
public sealed class PortalSearchTests(PlatformFactory factory)
{
    private static string Html(string html) => WebUtility.HtmlDecode(html);

    // 問答為主：首頁就是輸入框（置中的招呼、輸入框、範例），不再是一頁總覽。
    [Fact]
    public async Task TheHomePageIsTheAskPageWithAComposerAndExamples()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "ps-g");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync(Home(tenant.Identifier));

        Assert.Equal(HttpStatusCode.OK, page.Status);
        var html = Html(page.Html);
        Assert.Contains("data-state=\"empty\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-composer\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-ask-scope\"", html, StringComparison.Ordinal);
        Assert.Contains("<label for=\"query\"", html, StringComparison.Ordinal);
        // 通用的首頁（對標主流 AI 首頁）：只有一句問候與一個輸入框，不用「想查哪一條法規」這類話把用途框死成只能查法規。
        Assert.DoesNotContain("想查哪一條法規", html, StringComparison.Ordinal);
        Assert.Contains("placeholder=\"問問環境知識\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-ask-lead", html, StringComparison.Ordinal);
        Assert.Matches("<button[^>]*class=\"ep-chip\"[^>]*>噪音管制標準</button>", html); // 範例鈕

        // 現代的 AI 輸入：多行輸入框（Enter 送出由 forms.js 處理）、沒有手動日期、首頁只有一行極簡提示；
        // 完整說明收在原生 popover，資料外送的揭露不能消失。
        Assert.Matches("<textarea[^>]*id=\"query\"[^>]*data-composer", html);
        Assert.DoesNotContain("type=\"date\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("哪一天有效", html, StringComparison.Ordinal);
        Assert.DoesNotContain("不填日期", html, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-date-chip", html, StringComparison.Ordinal);
        Assert.Matches("<button[^>]*popovertarget=\"ask-info\"[^>]*>使用說明</button>", html);
        Assert.Matches("<div id=\"ask-info\" class=\"ep-sheet\" popover", html);
        Assert.Contains("用條文裡會出現的詞，空白分隔。", html, StringComparison.Ordinal);
    }

    // 從法規工作台點擊「問問 AI」帶 ?q= 進來時，預填於輸入框而不自動送出。
    [Fact]
    public async Task QueryStringPrefillsTheComposerWithoutAutoSubmitting()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "ps-prefill");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync($"{Home(tenant.Identifier)}?q={Uri.EscapeDataString("空氣污染防制法第32條")}");

        Assert.Equal(HttpStatusCode.OK, page.Status);
        var html = Html(page.Html);
        Assert.Contains("空氣污染防制法第32條", html, StringComparison.Ordinal);
        Assert.Contains("data-state=\"empty\"", html, StringComparison.Ordinal);
    }

    // 完全付費制：沒有方案不能用資料功能，畫面上沒有輸入框；服務層另外擋（AskServiceTests）。
    [Fact]
    public async Task ATenantWithoutAPlanSeesNoComposer()
    {
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));
        var (tenant, email, initial) = await MemberAsync(factory, "ps-c");
        using var browser = new PortalBrowser(host);
        _ = await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync(Home(tenant.Identifier));

        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("目前沒有方案", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-composer", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheHomePageNeedsAFullSessionNotJustAnyOne()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "ps-e");
        using var anonymous = new PortalBrowser(factory);
        var redirected = await anonymous.GetAsync(Home(tenant.Identifier));
        Assert.Equal(HttpStatusCode.Redirect, redirected.Status);
        Assert.StartsWith(Login(tenant.Identifier), redirected.Location, StringComparison.Ordinal);

        // 還沒改初始密碼的 session 只能進改密碼頁。
        using var pending = new PortalBrowser(factory);
        _ = await pending.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        var blocked = await pending.GetAsync(Home(tenant.Identifier));
        Assert.Equal(PasswordPage(tenant.Identifier), blocked.Location);
    }
}