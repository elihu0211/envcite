using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 租戶頁的存取網址：沒有成員、沒有 key 的新租戶也看得到，主機取自請求（不是寄信用的 <c>Platform:Mail:BaseUrl</c>），
/// 複製鈕有標籤、可由鍵盤操作、成功與失敗都有回饋。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
public sealed partial class TenantUrlTests(PlatformFactory factory)
{
    private async Task<(string Identifier, string Html)> TenantPageAsync(Uri? origin = null, string language = "zh-TW")
    {
        var tenant = await factory.CreateTenantAsync($"url-{Guid.NewGuid():N}"[..30], "東海大學永續處");
        using var client = factory.SignedInClient(baseAddress: origin);
        client.DefaultRequestHeaders.AcceptLanguage.ParseAdd(language);
        return (tenant.Identifier, await client.PageAsync($"/ui/tenants/{tenant.Id}"));
    }

    private static string Value(string html, string id) => ValueElement(id).Match(html).Groups[1].Value;

    private static Regex ValueElement(string id) =>
        new($"""<code class="ep-mono ep-copy-value" id="{Regex.Escape(id)}-value">([^<]*)</code>""", RegexOptions.None, TimeSpan.FromSeconds(2));

    [Fact]
    public async Task ANewTenantWithNoMembersAndNoKeysStillShowsThePortalLoginUrl()
    {
        var (identifier, html) = await TenantPageAsync();

        Assert.Contains("還沒有成員", html, StringComparison.Ordinal); // 成員區是空狀態：網址不在那裡，所以不會被它收掉
        Assert.Contains("還沒有 API key", html, StringComparison.Ordinal);
        Assert.Equal($"http://localhost{PortalPaths.Login(identifier)}", Value(html, "portal-login-url"));
        Assert.Equal($"http://localhost/portal/{identifier}/login", Value(html, "portal-login-url"));
    }

    [Fact]
    public async Task TheUrlsAreASecondaryGroupBesideTheTenantIdentityNotACardAndNotInATab()
    {
        // 空的表格卡片會把裡面的 .ep-status 收掉（視覺隱藏）；網址曾經放在那裡，所以沒有成員時看不到。
        // 網址是附屬的小按鈕，貼在租戶識別碼旁的身分列，不是獨立的卡片，也不屬於任何分頁。
        var (_, html) = await TenantPageAsync();

        var urls = html.IndexOf("id=\"portal-login-url-value\"", StringComparison.Ordinal);
        Assert.True(urls > 0, "portal URL is rendered");
        Assert.True(urls < html.IndexOf("class=\"ep-tabs\"", StringComparison.Ordinal), "above the tabs, so it belongs to no tab");
        Assert.True(urls < html.IndexOf("id=\"members-heading\"", StringComparison.Ordinal), "above the members card");
        var group = html.LastIndexOf("<div class=\"ep-copy-group\"", urls, StringComparison.Ordinal);
        Assert.True(group > 0, "the buttons are one group");
        Assert.Contains("role=\"group\" aria-label=\"存取網址\"", html[group..urls], StringComparison.Ordinal);
        Assert.Contains("class=\"ep-subbar\"", html[..group][^400..], StringComparison.Ordinal);
        Assert.DoesNotContain("ep-card", html[group..urls], StringComparison.Ordinal);
        Assert.DoesNotContain("ep-table-card", html[group..urls], StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheApiUrlIsShownTheSameWay()
    {
        var (_, html) = await TenantPageAsync();

        Assert.Equal("http://localhost/v2", Value(html, "api-base-url"));
        Assert.Equal("/v2", ApiPaths.V2);
    }

    [Theory]
    [InlineData("https://console-a.example.test/", "https://console-a.example.test")]
    [InlineData("https://console-b.example.test:8443/", "https://console-b.example.test:8443")]
    [InlineData("http://localhost:5280/", "http://localhost:5280")]
    public async Task TheHostComesFromTheRequestNotFromTheMailSetting(string address, string origin)
    {
        var (identifier, html) = await TenantPageAsync(new Uri(address));

        Assert.Equal($"{origin}/portal/{identifier}/login", Value(html, "portal-login-url"));
        Assert.Equal($"{origin}/v2", Value(html, "api-base-url"));
        // 測試主機把 Platform:Mail:BaseUrl 設成 https://platform.test：寄信用的位址不得出現在存取網址裡。
        Assert.DoesNotContain("platform.test", Value(html, "portal-login-url"), StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheCopyButtonHasALabelAnAccessibleNameAndALiveRegion()
    {
        var (_, html) = await TenantPageAsync();

        var button = Regex.Match(html, """<button type="button" class="ep-btn ep-btn-quiet ep-btn-icon ep-js-only" aria-label="複製登入網址"[^>]*>""", RegexOptions.None, TimeSpan.FromSeconds(2));
        Assert.True(button.Success, "a real <button> with the label in its accessible name");
        Assert.Contains("data-copy-target=\"portal-login-url-value\"", button.Value, StringComparison.Ordinal);
        Assert.Contains("data-copy-status=\"portal-login-url-status\"", button.Value, StringComparison.Ordinal);
        Assert.Contains("data-done=\"已複製\"", button.Value, StringComparison.Ordinal);
        Assert.Contains("data-failed=\"無法自動複製", button.Value, StringComparison.Ordinal);
        Assert.Contains("<span>複製登入網址</span>", html, StringComparison.Ordinal); // 可見文字就是無障礙名稱（WCAG 2.5.3）
        Assert.Contains("id=\"portal-login-url-status\" role=\"status\" aria-live=\"polite\"", html, StringComparison.Ordinal);
        Assert.Contains("aria-label=\"複製 API Base URL\"", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheEnglishPageNamesTheButtonsInEnglish()
    {
        var (_, html) = await TenantPageAsync(language: "en");

        Assert.Contains("Access URLs", html, StringComparison.Ordinal);
        Assert.Contains("aria-label=\"Copy sign-in URL\"", html, StringComparison.Ordinal);
        Assert.Contains("data-done=\"Copied\"", html, StringComparison.Ordinal);
        Assert.Contains("<span>Copy sign-in URL</span>", html, StringComparison.Ordinal);
        Assert.Contains("Copy API base URL", html, StringComparison.Ordinal);
    }

    // 複製在瀏覽器端：同一支委派腳本處理所有複製鈕。沒有瀏覽器可跑，所以守住契約的字面：
    // 複製的是元素裡的完整文字、成功時念出「已複製」、剪貼簿不可用時選取整段並顯示提示。
    [Fact]
    public async Task TheSharedScriptCopiesTheFullTextAnnouncesItAndFallsBackToSelectingIt()
    {
        var script = await File.ReadAllTextAsync(Path.Combine(WebPath(), "wwwroot", "design", "forms.js"), TestContext.Current.CancellationToken);

        Assert.Contains("[data-copy-target]", script, StringComparison.Ordinal);
        Assert.Contains("dataset.copyStatus", script, StringComparison.Ordinal);
        Assert.Contains("navigator.clipboard.writeText", script, StringComparison.Ordinal);
        Assert.Contains("execCommand(\"copy\")", script, StringComparison.Ordinal);
        Assert.Contains("selectNodeContents(target)", script, StringComparison.Ordinal);
        Assert.Contains("dataset.failed", script, StringComparison.Ordinal);
    }

    // 只列按鈕：網址在文件裡當複製來源，有 JavaScript 時平常收起來；剪貼簿失敗時由腳本設 data-reveal 顯示並選取。
    [Fact]
    public async Task OnlyTheButtonsAreShownAndTheUrlAppearsOnlyWhenCopyingFails()
    {
        var css = await File.ReadAllTextAsync(Path.Combine(WebPath(), "wwwroot", "design", "platform.css"), TestContext.Current.CancellationToken);
        var script = await File.ReadAllTextAsync(Path.Combine(WebPath(), "wwwroot", "design", "forms.js"), TestContext.Current.CancellationToken);

        Assert.Contains("html[data-js] .ep-copy-value:not([data-reveal]) {", css, StringComparison.Ordinal);
        Assert.Contains("setAttribute(\"data-reveal\"", script, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-copyable", css, StringComparison.Ordinal); // 舊的「列出網址」版型不留
    }

    // 長網址：在任何位置換行（不撐出橫向捲軸），整段可一次選取；用設計變數，不寫死顏色。
    [Fact]
    public async Task ALongUrlWrapsInsteadOfScrollingSidewaysAndUsesTokens()
    {
        var css = await File.ReadAllTextAsync(Path.Combine(WebPath(), "wwwroot", "design", "platform.css"), TestContext.Current.CancellationToken);
        var value = CssRule(".ep-copy-value").Match(css).Groups[1].Value;

        Assert.NotEmpty(value);
        Assert.Contains("overflow-wrap: anywhere", value, StringComparison.Ordinal);
        Assert.Contains("min-inline-size: 0", value, StringComparison.Ordinal);
        Assert.Contains("user-select: all", value, StringComparison.Ordinal);
        Assert.DoesNotMatch(HardCodedColour(), value);
    }

    private static Regex CssRule(string selector) =>
        new($@"^{Regex.Escape(selector)} \{{(.*?)^\}}", RegexOptions.Multiline | RegexOptions.Singleline, TimeSpan.FromSeconds(2));

    private static string WebPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web"));

    [GeneratedRegex(@"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex HardCodedColour();
}