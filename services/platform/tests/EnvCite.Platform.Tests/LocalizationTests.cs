using System.Net;
using System.Net.Http.Headers;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using System.Xml.Linq;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>資源檔：兩個語言的鍵一致，標籤依介面語言取用。</summary>
public static partial class LabelResourceTests
{
    private static string ResourcePath(string file, [CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Resources", file));

    private static Dictionary<string, string> Load(string file) =>
        XDocument.Load(ResourcePath(file)).Root!.Elements("data")
            .ToDictionary(e => (string)e.Attribute("name")!, e => (string)e.Element("value")!, StringComparer.Ordinal);

    // 少了一個鍵，那個語言的畫面就會悄悄顯示鍵名或退回預設語言；這裡讓它在 CI 就失敗。
    [Fact]
    public static void BothLanguagesDefineTheSameKeys()
    {
        var zh = Load("Labels.resx");
        var en = Load("Labels.en.resx");

        Assert.Empty(zh.Keys.Except(en.Keys));
        Assert.Empty(en.Keys.Except(zh.Keys));
        Assert.All(zh.Concat(en), pair => Assert.False(string.IsNullOrWhiteSpace(pair.Value), pair.Key));
    }

    // 格式字串的參數個數兩邊要一樣，否則一個語言會少填、另一個會例外。
    [Fact]
    public static void TheSamePlaceholdersAppearInBothLanguages()
    {
        var zh = Load("Labels.resx");
        var en = Load("Labels.en.resx");

        foreach (var (key, value) in zh)
        {
            Assert.Equal(
                [.. Placeholder().Matches(value).Select(m => m.Value).Order()],
                [.. Placeholder().Matches(en[key]).Select(m => m.Value).Order()]);
        }
    }

    [Fact]
    public static void EveryAuditEventHasAnEnglishNameThatIsNotItsKey()
    {
        using var _ = TestCulture.Use("en");

        Assert.All(AuditActions.All, action =>
        {
            var label = AuditLabels.Action(action);
            Assert.NotEqual(action, label);
            Assert.DoesNotContain("，", label, StringComparison.Ordinal); // 英文名稱裡沒有殘留的中文標點
        });
    }

    [Theory]
    [InlineData("zh-TW", "簽發 API key", "法規本體", "擁有者", "使用中")]
    [InlineData("en", "API key issued", "Legal ontology", "Owner", "Active")]
    public static void LabelsFollowTheCurrentUiCulture(string culture, string action, string category, string role, string status)
    {
        using var _ = TestCulture.Use(culture);

        Assert.Equal(action, AuditLabels.Action(AuditActions.ApiKeyIssued));
        Assert.Equal(category, UsageLabels.Category("ontology.search"));
        Assert.Equal(role, PortalLabels.Role(CustomerRoles.Owner));
        Assert.Equal(status, OperatorLabels.Status(new OperatorInfo("1", "a@example.com", null, false, false, true, false)));
    }

    [Fact]
    public static void EnglishActorsAndUnitsReadNaturally()
    {
        using var _ = TestCulture.Use("en");

        Assert.Equal("Tenant API key …0a1b2c3d", AuditLabels.Actor(AuditActors.TenantKey, "0192-0a1b2c3d"));
        Assert.Equal("Tenant member", AuditLabels.Actor(AuditActors.TenantUser, null));
        Assert.Equal("System", AuditLabels.Actor(AuditActors.System, null));
        Assert.Equal("calls", UsageLabels.UnitName("ontology.search"));
        Assert.Equal("tokens", UsageLabels.UnitName("consult.tokens.input"));
    }

    // 契約的鍵（事件鍵、操作鍵、角色鍵）不翻譯：不認得的值原樣顯示，而不是隱藏或出錯。
    [Fact]
    public static void UnknownKeysAreShownAsTheyAreInEveryLanguage()
    {
        foreach (var culture in new[] { "zh-TW", "en" })
        {
            using var _ = TestCulture.Use(culture);
            Assert.Equal("some.future_event", AuditLabels.Action("some.future_event"));
            Assert.Equal("some.future_op", UsageLabels.Operation("some.future_op"));
            Assert.Equal("FutureRole", PortalLabels.Role("FutureRole"));
        }
    }

    [GeneratedRegex(@"\{\d+\}", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Placeholder();
}

/// <summary>決定語言、換語言的端點、<c>&lt;html lang&gt;</c>。</summary>
[Collection(PlatformTestGroup.Group3)]
public sealed partial class LanguageSwitchTests(PlatformFactory factory)
{
    private const string CultureCookie = ".AspNetCore.Culture";

    private async Task<string> LangOfAsync(string path, string? acceptLanguage = null, string? cookie = null)
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(path, UriKind.Relative));
        if (acceptLanguage is not null)
            request.Headers.AcceptLanguage.ParseAdd(acceptLanguage);

        if (cookie is not null)
            request.Headers.Add("Cookie", $"{CultureCookie}={cookie}");

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
        var html = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        return HtmlLang().Match(html) is { Success: true } match ? match.Groups[1].Value : $"no lang ({(int)response.StatusCode})";
    }

    [Theory]
    [InlineData(null, "zh-TW")] // 沒有偏好：預設語言
    [InlineData("en-US,en;q=0.9", "en")]
    [InlineData("zh-TW,zh;q=0.9,en;q=0.8", "zh-TW")]
    [InlineData("zh-Hant-TW", "zh-TW")] // 內建的比對認不得這個寫法
    [InlineData("zh-HK", "zh-TW")]
    [InlineData("fr-FR,en;q=0.5", "en")] // 不支援的略過，取下一個
    [InlineData("en;q=0.4,zh-TW;q=0.9", "zh-TW")] // 依品質值，不是依出現順序
    [InlineData("fr,de;q=0.9", "zh-TW")] // 都不支援：預設語言
    public async Task TheLanguageFollowsTheBrowserWhenThereIsNoChoice(string? acceptLanguage, string expected) =>
        Assert.Equal(expected, await LangOfAsync("/ui/login", acceptLanguage));

    [Fact]
    public async Task TheChoiceInTheCookieBeatsTheBrowser()
    {
        Assert.Equal("en", await LangOfAsync("/ui/login", "zh-TW", "c%3Den%7Cuic%3Den"));
        Assert.Equal("zh-TW", await LangOfAsync("/ui/login", "en", "c%3Dzh-TW%7Cuic%3Dzh-TW"));
        // cookie 裡是不支援的語言：當作沒選，回到瀏覽器的偏好。
        Assert.Equal("en", await LangOfAsync("/ui/login", "en", "c%3Dfr%7Cuic%3Dfr"));
    }

    // 網址上的 ?culture= 不換語言：連結不能替人換語言，換語言只經過有 antiforgery 的 POST。
    [Fact]
    public async Task TheQueryStringDoesNotChangeTheLanguage() =>
        Assert.Equal("zh-TW", await LangOfAsync("/ui/login?culture=en&ui-culture=en"));

    [Fact]
    public async Task EveryEntrancePageOffersBothLanguagesAndMarksTheCurrentOne()
    {
        var (tenant, _, _) = await MemberAsync(factory, "i18n-a");
        using var browser = new PortalBrowser(factory);

        foreach (var path in new[] { "/ui/login", Login(tenant.Identifier) })
        {
            var page = await browser.GetAsync(path);

            Assert.Contains("value=\"zh-TW\" lang=\"zh-TW\" class=\"ep-lang-option\" aria-current=\"true\"", page.Html, StringComparison.Ordinal);
            Assert.Contains("value=\"en\" lang=\"en\" class=\"ep-lang-option\">English", page.Html, StringComparison.Ordinal);
            Assert.Contains("繁體中文", page.Html, StringComparison.Ordinal);
        }
    }

    [Fact]
    public async Task SwitchingInThePortalSetsTheCookieAndReturnsToThePage()
    {
        var (tenant, _, _) = await MemberAsync(factory, "i18n-b");
        var login = Login(tenant.Identifier);
        using var browser = new PortalBrowser(factory);

        var switched = await browser.SubmitAsync(login, new() { ["culture"] = "en", ["returnUrl"] = login }, $"{Home(tenant.Identifier)}/language");

        Assert.Equal(HttpStatusCode.Redirect, switched.Status);
        Assert.Equal(login, switched.Location);
        var after = await browser.GetAsync(login);
        Assert.Contains("<html lang=\"en\">", after.Html, StringComparison.Ordinal);
        Assert.Contains("value=\"en\" lang=\"en\" class=\"ep-lang-option\" aria-current=\"true\"", after.Html, StringComparison.Ordinal);

        // 換回來。
        await browser.SubmitAsync(login, new() { ["culture"] = "zh-TW", ["returnUrl"] = login }, $"{Home(tenant.Identifier)}/language");
        Assert.Contains("<html lang=\"zh-TW\">", (await browser.GetAsync(login)).Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task SwitchingInTheOperatorUiSetsTheCookieToo()
    {
        using var browser = new PortalBrowser(factory);

        var switched = await browser.SubmitAsync("/ui/login", new() { ["culture"] = "en", ["returnUrl"] = "/ui/login" }, "/ui/language");

        Assert.Equal(HttpStatusCode.Redirect, switched.Status);
        Assert.Equal("/ui/login", switched.Location);
        Assert.Contains("<html lang=\"en\">", (await browser.GetAsync("/ui/login")).Html, StringComparison.Ordinal);
    }

    // 導回只准留在那個面之下：外部網址、別的面、協定相對網址都回到該面的首頁。
    [Theory]
    [InlineData("https://evil.example/")]
    [InlineData("//evil.example/")]
    [InlineData("/v2/tenants")]
    [InlineData("/ui//evil.example")]
    [InlineData("/ui\\evil")]
    public async Task TheReturnAddressCannotLeaveTheSurface(string returnUrl)
    {
        using var browser = new PortalBrowser(factory);

        var switched = await browser.SubmitAsync("/ui/login", new() { ["culture"] = "en", ["returnUrl"] = returnUrl }, "/ui/language");

        Assert.Equal("/ui", switched.Location);
    }

    [Fact]
    public async Task AnUnsupportedLanguageIsRejectedAndSetsNothing()
    {
        using var browser = new PortalBrowser(factory);

        var switched = await browser.SubmitAsync("/ui/login", new() { ["culture"] = "fr", ["returnUrl"] = "/ui/login" }, "/ui/language");

        Assert.Equal(HttpStatusCode.BadRequest, switched.Status);
        Assert.Contains("<html lang=\"zh-TW\">", (await browser.GetAsync("/ui/login")).Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task SwitchingNeedsAnAntiforgeryTokenSoALinkCannotDoIt()
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        using var form = new FormUrlEncodedContent(new Dictionary<string, string> { ["culture"] = "en", ["returnUrl"] = "/ui/login" });

        using var response = await client.PostAsync(new Uri("/ui/language", UriKind.Relative), form, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.False(response.Headers.Contains("Set-Cookie"));
    }

    [Fact]
    public async Task AnUnknownTenantHasNoLanguageEndpoint()
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        using var form = new FormUrlEncodedContent(new Dictionary<string, string> { ["culture"] = "en" });

        using var response = await client.PostAsync(new Uri("/portal/no-such-tenant-xyz/language", UriKind.Relative), form, TestContext.Current.CancellationToken);

        Assert.True(response.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.BadRequest);
    }

    // 語言只影響後台與入口：資料 API 的回應不隨 Accept-Language 變（用真的端點與租戶 key，確認拿到的是 200 而不是兩邊都 404）。
    [Fact]
    public async Task TheDataApiIgnoresTheLanguage()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("i18n-api");
        using var client = factory.CreateTenantClient(key.Key);
        using var withEnglish = new HttpRequestMessage(HttpMethod.Get, new Uri("/v2/me", UriKind.Relative));
        withEnglish.Headers.AcceptLanguage.Add(new StringWithQualityHeaderValue("en"));
        using var plain = new HttpRequestMessage(HttpMethod.Get, new Uri("/v2/me", UriKind.Relative));

        using var a = await client.SendAsync(withEnglish, TestContext.Current.CancellationToken);
        using var b = await client.SendAsync(plain, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, a.StatusCode);
        Assert.Equal(
            await b.Content.ReadAsStringAsync(TestContext.Current.CancellationToken),
            await a.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
    }

    private static readonly Regex Cjk = new(@"[\u4e00-\u9fff]", RegexOptions.None, TimeSpan.FromSeconds(1));

    /// <summary>頁面文字去掉語言選項（各語言的自稱本來就是原文）之後，還剩的中文。</summary>
    private static string LeftoverChinese(string html) =>
        string.Concat(Cjk.Matches(Regex.Replace(html, @"<button[^>]*class=""ep-lang-option""[^>]*>[^<]*</button>", string.Empty, RegexOptions.None, TimeSpan.FromSeconds(1))).Select(m => m.Value));

    // 英文版的後台頁面（含登入、被拒、已登出）：沒有任何中文殘留。
    [Theory]
    [InlineData("/ui")]
    [InlineData("/ui/tenants")]
    [InlineData("/ui/operators")]
    [InlineData("/ui/audit")]
    [InlineData("/ui/settings")]
    [InlineData("/ui/login")]
    [InlineData("/ui/denied")]
    [InlineData("/ui/signed-out")]
    public async Task TheEnglishOperatorPagesHaveNoChineseLeft(string path)
    {
        using var client = path is "/ui/login" or "/ui/denied" or "/ui/signed-out"
            ? factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false })
            : factory.SignedInClient();
        client.DefaultRequestHeaders.AcceptLanguage.ParseAdd("en");

        using var response = await client.GetAsync(new Uri(path, UriKind.Relative), TestContext.Current.CancellationToken);
        var html = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("<html lang=\"en\">", html, StringComparison.Ordinal);
        Assert.Equal(string.Empty, LeftoverChinese(await WithoutTenantNamesAsync(html)));
    }

    // 租戶名稱是營運者輸入的資料，不是介面文字；別的測試建立的中文名稱留在共用資料庫裡，
    // 頁面列出它們不算殘留（與下面搜尋範例同理）。
    private async Task<string> WithoutTenantNamesAsync(string html)
    {
        var tenants = await factory.Services.GetRequiredService<ITenantDirectory>().ListAsync(TestContext.Current.CancellationToken);
        return tenants.Where(t => t.Name is not null).Aggregate(html, (current, t) => current.Replace(t.Name!, string.Empty, StringComparison.Ordinal));
    }

    // 搜尋範例是臺灣法規裡的詞（資料是中文），英文介面也維持中文，所以不算殘留。
    private static readonly string[] SearchExamples = ["噪音管制標準", "固定污染源", "廢棄物清理", "水污染防治"];

    // 英文版的客戶入口（登入前後）：用真的流程切換語言，之後每一頁都沒有中文殘留。
    [Fact]
    public async Task TheEnglishPortalHasNoChineseLeft()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "i18n-portal", name: "Alice");
        var home = Home(tenant.Identifier);
        using var browser = new PortalBrowser(factory);
        var login = Login(tenant.Identifier);
        await browser.SubmitAsync(login, new() { ["culture"] = "en", ["returnUrl"] = login }, $"{home}/language");

        var pages = new Dictionary<string, string> { [login] = (await browser.GetAsync(login)).Html };
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        foreach (var path in new[] { home, $"{home}/account", $"{home}/members", $"{home}/security", $"{home}/settings", $"{home}/history", PasswordPage(tenant.Identifier) })
        {
            var page = await browser.GetAsync(path);
            Assert.Equal(HttpStatusCode.OK, page.Status);
            pages[path] = page.Html;
        }

        foreach (var (path, html) in pages)
        {
            var text = SearchExamples.Aggregate(html, (current, example) => current.Replace(example, string.Empty, StringComparison.Ordinal));
            Assert.True(text.Contains("<html lang=\"en\">", StringComparison.Ordinal), path);
            Assert.True(LeftoverChinese(text).Length == 0, $"{path}: {LeftoverChinese(text)}");
        }
    }

    [Fact]
    public async Task TheEnglishTenantDetailHasNoChineseLeft()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("i18n-detail");
        using var client = factory.SignedInClient();
        client.DefaultRequestHeaders.AcceptLanguage.ParseAdd("en");

        using var response = await client.GetAsync(new Uri($"/ui/tenants/{tenant.Id}", UriKind.Relative), TestContext.Current.CancellationToken);
        var html = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal(string.Empty, LeftoverChinese(html));
    }

    [GeneratedRegex("""<html lang="([^"]*)">""", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex HtmlLang();
}
/// <summary>頁面文字的資源檔（<c>Ui.en.resx</c>）：以中文文字當鍵，來源裡的每個鍵都要有英文。</summary>
public static partial class UiTextTests
{
    private static string WebPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web"));

    private static Dictionary<string, string> English() =>
        XDocument.Load(Path.Combine(WebPath(), "Resources", "Ui.en.resx")).Root!.Elements("data")
            .ToDictionary(e => (string)e.Attribute("name")!, e => (string)e.Element("value")!, StringComparer.Ordinal);

    /// <summary>來源裡所有 <c>L["…"]</c> 的鍵（含逸出還原）。</summary>
    private static HashSet<string> KeysInSource() =>
        [.. Directory.EnumerateFiles(Path.Combine(WebPath(), "Components"), "*.*", SearchOption.AllDirectories)
            .Where(f => f.EndsWith(".razor", StringComparison.Ordinal) || f.EndsWith(".cs", StringComparison.Ordinal))
            .SelectMany(f => LocalizerKey().Matches(File.ReadAllText(f)).Select(m => m.Groups[1].Value
                .Replace("\\\"", "\"", StringComparison.Ordinal).Replace("\\\\", "\\", StringComparison.Ordinal)))];

    // 少了英文，英文介面就悄悄顯示中文；多了沒用的鍵，改了中文之後留下一堆孤兒。兩個方向都在 CI 擋下。
    [Fact]
    public static void EveryKeyInTheSourceHasAnEnglishText()
    {
        var english = English();

        Assert.Empty(KeysInSource().Except(english.Keys));
        Assert.All(english, pair => Assert.False(string.IsNullOrWhiteSpace(pair.Value), pair.Key));
    }

    [Fact]
    public static void NoEnglishTextIsLeftOverWithoutAKeyInTheSource() =>
        Assert.Empty(English().Keys.Except(KeysInSource()));

    // 格式字串的參數：中文與英文用到同一組 {n}（英文可以換順序，但不能少填或多填）。
    [Fact]
    public static void TheSamePlaceholdersAppearInTheKeyAndItsEnglish()
    {
        foreach (var (key, value) in English())
        {
            Assert.Equal(
                [.. Placeholder().Matches(key).Select(m => m.Value).Order()],
                [.. Placeholder().Matches(value).Select(m => m.Value).Order()]);
        }
    }

    [GeneratedRegex(@"\bL\[""((?:[^""\\]|\\.)*)""", RegexOptions.None, matchTimeoutMilliseconds: 2000)]
    private static partial Regex LocalizerKey();

    [GeneratedRegex(@"\{\d+\}", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Placeholder();
}
/// <summary>MudBlazor 內建文字：中文介面用繁體中文，英文介面用它內建的英文。</summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class MudBlazorTextTests(PlatformFactory factory)
{
    [Fact]
    public static void TheLocalizerAnswersInChineseAndStepsAsideForEnglish()
    {
        var localizer = new EnvCite.Platform.Web.Presentation.PlatformMudLocalizer();

        using (TestCulture.Use("zh-TW"))
        {
            Assert.Equal("第一頁", localizer["MudTablePager_FirstPage"].Value);
            Assert.False(localizer["MudTablePager_FirstPage"].ResourceNotFound);
            Assert.True(localizer["Mud_Not_A_Key"].ResourceNotFound);
        }

        using (TestCulture.Use("en"))
        {
            Assert.True(localizer["MudTablePager_FirstPage"].ResourceNotFound);
        }
    }

    [Fact]
    public async Task ThePagerOfTheTenantListSpeaksTheUsersLanguage()
    {
        using var zh = factory.SignedInClient();
        using var en = factory.SignedInClient();
        en.DefaultRequestHeaders.AcceptLanguage.ParseAdd("en");

        var chinese = WebUtility.HtmlDecode(await zh.GetStringAsync(new Uri("/ui/tenants", UriKind.Relative), TestContext.Current.CancellationToken));
        var english = WebUtility.HtmlDecode(await en.GetStringAsync(new Uri("/ui/tenants", UriKind.Relative), TestContext.Current.CancellationToken));

        Assert.Contains("第一頁", chinese, StringComparison.Ordinal);
        Assert.DoesNotContain("First page", chinese, StringComparison.Ordinal);
        Assert.Contains("First page", english, StringComparison.Ordinal);
    }
}

/// <summary>後台的切換器：頂欄的按鈕、鍵盤捷徑的腳本，按鈕文字依語言。</summary>
[Collection(PlatformTestGroup.Group3)]
public sealed class CommandPaletteTests(PlatformFactory factory)
{
    private async Task<string> HomeAsync(string? language)
    {
        using var client = factory.SignedInClient();
        if (language is not null)
            client.DefaultRequestHeaders.AcceptLanguage.ParseAdd(language);

        return WebUtility.HtmlDecode(await client.GetStringAsync(new Uri("/ui", UriKind.Relative), TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task TheConsoleHeaderHasTheButtonInBothLanguages()
    {
        var chinese = await HomeAsync(null);
        var english = await HomeAsync("en");

        Assert.Contains("id=\"ep-palette-open\"", chinese, StringComparison.Ordinal);
        Assert.Contains("搜尋頁面與租戶", chinese, StringComparison.Ordinal);
        Assert.Contains("aria-keyshortcuts=\"Control+K Meta+K\"", chinese, StringComparison.Ordinal);
        Assert.Contains("Search pages and tenants", english, StringComparison.Ordinal);
    }

    // 捷徑腳本有被引用、抓得到，而且只對按鈕按一下（不自己開任何東西）。
    [Fact]
    public async Task TheShortcutScriptIsServedAndOnlyClicksTheButton()
    {
        var html = await HomeAsync(null);
        var src = Regex.Match(html, "<script src=\"([^\"]*shortcuts[^\"]*\\.js)\"").Groups[1].Value;
        Assert.NotEmpty(src);

        using var client = factory.SignedInClient();
        var script = await client.GetStringAsync(new Uri("/" + src.TrimStart('/'), UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Contains("ep-palette-open", script, StringComparison.Ordinal);
        Assert.Contains(".click()", script, StringComparison.Ordinal);
    }
}