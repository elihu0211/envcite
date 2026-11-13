using System.Net;
using System.Net.Http.Headers;
using System.Text.RegularExpressions;

using EnvCite.Platform.Web;

using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 頁面的標記，去掉內嵌的 token 樣式表：樣式表本來就寫著 <c>[data-theme="dark"]</c> 這類選擇器（手動選擇才會用到），
/// 那不代表頁面套用了偏好；頁面「有沒有套用」要看 <c>&lt;html&gt;</c> 標籤與樣式表以外的標記。
/// </summary>
internal static partial class PageMarkup
{
    public static string WithoutStyles(string html) => Style().Replace(html, string.Empty);

    public static string HtmlTag(string html)
    {
        var match = Html().Match(html);
        Assert.True(match.Success, "頁面沒有 <html>");
        return match.Value;
    }

    [GeneratedRegex("<style>.*?</style>", RegexOptions.Singleline, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Style();

    [GeneratedRegex("<html[^>]*>", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Html();
}

/// <summary>外觀偏好的讀寫：預設跟隨系統；cookie 沒有、被清掉或值不合法都退回系統。純函式，不需要資料庫。</summary>
public static class AppearancePreferenceTests
{
    private static IAppearancePreference Preference(Action<AppearanceOptions>? configure = null)
    {
        var services = new ServiceCollection().AddPlatformAppearance();
        if (configure is not null)
            services.Configure(configure);

        return services.BuildServiceProvider().GetRequiredService<IAppearancePreference>();
    }

    private static DefaultHttpContext Request(string? cookie, bool https = false)
    {
        var context = new DefaultHttpContext();
        context.Request.IsHttps = https;
        if (cookie is not null)
            context.Request.Headers.Cookie = cookie;

        return context;
    }

    [Theory]
    [InlineData(null, Appearance.System)] // 沒有 cookie：預設跟隨系統
    [InlineData("ep-appearance=light", Appearance.Light)]
    [InlineData("ep-appearance=dark", Appearance.Dark)]
    [InlineData("ep-appearance=DARK", Appearance.Dark)]
    [InlineData("ep-appearance=system", Appearance.System)]
    [InlineData("ep-appearance=", Appearance.System)] // 被清成空值
    [InlineData("ep-appearance=sepia", Appearance.System)] // 值不合法
    [InlineData("ep-appearance=darkest", Appearance.System)]
    [InlineData("ep-appearance=%3Cscript%3E", Appearance.System)]
    [InlineData("other=dark", Appearance.System)] // 別的 cookie 不算
    public static void ReadingFallsBackToTheSystemUnlessTheCookieHoldsAKnownValue(string? cookie, Appearance expected) =>
        Assert.Equal(expected, Preference().Read(Request(cookie)));

    [Fact]
    public static void TheCookieIsDescribedByOptionsNotHardCoded()
    {
        var options = new AppearanceOptions().Cookie;

        Assert.Equal("ep-appearance", options.Name);
        Assert.Equal(TimeSpan.FromDays(365), options.MaxAge);
        Assert.True(options.HttpOnly);
        Assert.True(options.IsEssential);
        Assert.Equal(SameSiteMode.Lax, options.SameSite);

        // 換掉選項，讀與寫都跟著走。
        var custom = Preference(o => o.Cookie.Name = "custom-appearance");
        Assert.Equal(Appearance.Dark, custom.Read(Request("custom-appearance=dark")));
        Assert.Equal(Appearance.System, custom.Read(Request("ep-appearance=dark")));
        var written = Request(null);
        custom.Write(written, Appearance.Light);
        Assert.StartsWith("custom-appearance=light", written.Response.Headers.SetCookie.ToString(), StringComparison.Ordinal);
    }

    [Theory]
    [InlineData(Appearance.Light, "light")]
    [InlineData(Appearance.Dark, "dark")]
    public static void AChoiceIsAHttpOnlyLaxCookieThatLastsAYear(Appearance appearance, string value)
    {
        var context = Request(null, https: true);

        Preference().Write(context, appearance);

        var header = context.Response.Headers.SetCookie.ToString();
        Assert.StartsWith($"ep-appearance={value}", header, StringComparison.Ordinal);
        Assert.Contains("path=/", header, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("max-age=31536000", header, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("httponly", header, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("samesite=lax", header, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("secure", header, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public static void TheCookieIsOnlySecureWhenTheRequestIs()
    {
        var plain = Request(null);

        Preference().Write(plain, Appearance.Dark);

        Assert.DoesNotContain("secure", plain.Response.Headers.SetCookie.ToString(), StringComparison.OrdinalIgnoreCase);
    }

    // 選回「系統」是回到從沒設定過的狀態：刪掉 cookie，不是存一個 system。
    [Fact]
    public static void ChoosingTheSystemDeletesTheCookie()
    {
        var context = Request("ep-appearance=dark");

        Preference().Write(context, Appearance.System);

        var header = context.Response.Headers.SetCookie.ToString();
        Assert.StartsWith("ep-appearance=;", header, StringComparison.Ordinal);
        Assert.Contains("expires=Thu, 01 Jan 1970", header, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("path=/", header, StringComparison.OrdinalIgnoreCase);
    }

    [Theory]
    [InlineData("system", Appearance.System)]
    [InlineData("Light", Appearance.Light)]
    [InlineData("DARK", Appearance.Dark)]
    [InlineData("auto", null)]
    [InlineData("", null)]
    [InlineData(null, null)]
    public static void OnlyThreeWordsAreAnAppearance(string? word, Appearance? expected)
    {
        Assert.Equal(expected, AppearanceNames.Parse(word));
        if (expected is { } known)
            Assert.Equal(known.Name(), AppearanceNames.Parse(known.Name())!.Value.Name());
    }
}

/// <summary>
/// 個人設定的外觀：預設「系統」不輸出 <c>data-theme</c>；手動選了亮色或暗色，由伺服器依 cookie 在第一格就輸出；
/// 營運者與客戶入口各有個人設定頁，共用同一個元件；匿名頁面不放切換，但尊重已存的偏好。
/// </summary>
[Collection(PlatformTestGroup.Group1)]
public sealed partial class AppearanceSettingTests(PlatformFactory factory)
{
    private const string Cookie = "ep-appearance";

    private static async Task<string> BodyAsync(HttpClient client, string path, string? appearanceCookie = null, string? language = null)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(path, UriKind.Relative));
        // 已登入的用戶端把登入 cookie 放在預設標頭；請求自己的標頭會取代預設的，所以把兩者合成一條。
        var cookies = new List<string>();
        if (client.DefaultRequestHeaders.TryGetValues("Cookie", out var existing))
            cookies.AddRange(existing);

        if (appearanceCookie is not null)
            cookies.Add(appearanceCookie);

        if (cookies.Count > 0)
        {
            request.Headers.Remove("Cookie");
            request.Headers.TryAddWithoutValidation("Cookie", string.Join("; ", cookies));
        }

        if (language is not null)
            request.Headers.AcceptLanguage.ParseAdd(language);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
    }

    private HttpClient Anonymous() => factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

    private static string Meta(string html, string name, string? scheme = null)
    {
        var pattern = scheme is null
            ? $"""<meta name="{name}"[^>]*>"""
            : $"""<meta name="{name}"[^>]*data-scheme="{scheme}"[^>]*>""";
        var match = Regex.Match(html, pattern, RegexOptions.None, TimeSpan.FromSeconds(1));
        Assert.True(match.Success, $"沒有 {name} {scheme}");
        return match.Value;
    }

    private static string Section(string html)
    {
        var match = SettingSection().Match(html);
        Assert.True(match.Success, "頁面沒有外觀設定");
        return match.Value;
    }

    private static List<(string Value, bool Checked, string Label)> Radios(string html) =>
        [.. RadioLabel().Matches(Section(html)).Select(m => (m.Groups["value"].Value, m.Value.Contains(" checked", StringComparison.Ordinal), m.Groups["label"].Value))];

    // ---- 預設與回退 ------------------------------------------------------------------------------

    [Fact]
    public async Task WithNoPreferenceThereIsNoDataThemeAndTheSystemMetasStay()
    {
        using var client = factory.SignedInClient();

        foreach (var html in new[] { await BodyAsync(client, "/ui/settings"), await BodyAsync(client, "/ui/denied"), await BodyAsync(client, "/ui") })
        {
            Assert.Contains("<html lang=\"zh-TW\">", html, StringComparison.Ordinal);
            Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(html), StringComparison.Ordinal);
            Assert.Contains("<meta name=\"color-scheme\" content=\"light dark\"", html, StringComparison.Ordinal);
            Assert.Contains("media=\"(prefers-color-scheme: light)\"", Meta(html, "theme-color", "light"), StringComparison.Ordinal);
            Assert.Contains("media=\"(prefers-color-scheme: dark)\"", Meta(html, "theme-color", "dark"), StringComparison.Ordinal);
            Assert.Contains("prefers-color-scheme:dark", html, StringComparison.Ordinal); // token 的切換還在
        }
    }

    [Theory]
    [InlineData("ep-appearance=sepia")]
    [InlineData("ep-appearance=")]
    [InlineData("ep-appearance=system")]
    [InlineData("ep-appearance=%3Cscript%3E")]
    [InlineData("ep-appearance=darker")]
    [InlineData("x-appearance=dark")]
    public async Task ACookieThatIsClearedOrInvalidFallsBackToTheSystem(string cookie)
    {
        using var client = Anonymous();

        var html = await BodyAsync(client, "/ui/login", cookie);

        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(html), StringComparison.Ordinal);
        Assert.Contains("<meta name=\"color-scheme\" content=\"light dark\"", html, StringComparison.Ordinal);
        Assert.Contains("media=\"(prefers-color-scheme: light)\"", Meta(html, "theme-color", "light"), StringComparison.Ordinal);
        Assert.Contains("media=\"(prefers-color-scheme: dark)\"", Meta(html, "theme-color", "dark"), StringComparison.Ordinal);
    }

    // ---- 手動選擇：第一格就是對的，token、工具列顏色與原生控制項都跟著 -------------------------------

    [Theory]
    [InlineData("light", "not all", "all")]
    [InlineData("dark", "all", "not all")]
    public async Task AChosenAppearanceIsRenderedOnTheFirstPage(string choice, string darkMedia, string lightMedia)
    {
        using var client = Anonymous();

        var html = await BodyAsync(client, "/ui/login", $"{Cookie}={choice}");

        Assert.Contains($"<html lang=\"zh-TW\" data-theme=\"{choice}\">", html, StringComparison.Ordinal);
        // 畫布與原生控制項：color-scheme 鎖到選的那一邊（沒有選才是 light dark）。
        Assert.Contains($"<meta name=\"color-scheme\" content=\"{choice}\" data-system=\"light dark\"", html, StringComparison.Ordinal);
        // 工具列顏色跟著選擇：選的那一個對所有人生效，另一個關掉；不是只改頁面背景。
        var light = Meta(html, "theme-color", "light");
        var dark = Meta(html, "theme-color", "dark");
        Assert.Contains($"media=\"{lightMedia}\"", light, StringComparison.Ordinal);
        Assert.Contains($"media=\"{darkMedia}\"", dark, StringComparison.Ordinal);
        Assert.Contains("data-media=\"(prefers-color-scheme: light)\"", light, StringComparison.Ordinal); // 腳本預覽時還原用
        Assert.Contains("data-media=\"(prefers-color-scheme: dark)\"", dark, StringComparison.Ordinal);
        // 手動選的樣式規則在輸出的 <style> 裡：暗色鎖 color-scheme，亮色擋住系統的深色。
        Assert.Contains("html:root[data-theme=\"dark\"]{", html, StringComparison.Ordinal);
        Assert.Contains("html:root[data-theme=\"light\"]{color-scheme:light;}", html, StringComparison.Ordinal);
        Assert.Contains("html:root:not([data-theme=\"light\"]){", html, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("/ui/login")]
    [InlineData("/ui/denied")]
    [InlineData("/ui/signed-out")]
    public async Task AnonymousPagesHonorTheStoredChoiceButOfferNoSwitch(string path)
    {
        using var client = Anonymous();

        var html = await BodyAsync(client, path, $"{Cookie}=dark");

        Assert.Contains("data-theme=\"dark\"", PageMarkup.HtmlTag(html), StringComparison.Ordinal);
        Assert.DoesNotContain("data-appearance-form", html, StringComparison.Ordinal);
        Assert.DoesNotContain("name=\"appearance\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-segmented", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ThePortalLoginPageHonorsTheStoredChoiceButOffersNoSwitch()
    {
        var (tenant, _, _) = await MemberAsync(factory, "appearance-login");
        using var client = Anonymous();

        var html = await BodyAsync(client, Login(tenant.Identifier), $"{Cookie}=light");

        Assert.Contains("data-theme=\"light\"", PageMarkup.HtmlTag(html), StringComparison.Ordinal);
        Assert.DoesNotContain("name=\"appearance\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("data-appearance-form", html, StringComparison.Ordinal);
    }

    // ---- 營運者的個人設定 ------------------------------------------------------------------------

    [Fact]
    public async Task TheOperatorSettingsPageOffersSystemLightAndDarkWithSystemSelectedByDefault()
    {
        using var client = factory.SignedInClient();

        var html = await BodyAsync(client, "/ui/settings");

        Assert.Contains("<title>個人設定 ·", html, StringComparison.Ordinal);
        Assert.Contains("<h1 class=\"ep-page-title\">個人設定</h1>", html, StringComparison.Ordinal);
        Assert.Equal([("system", true, "系統"), ("light", false, "亮色"), ("dark", false, "暗色")], Radios(html));
        Assert.Contains("跟隨裝置的外觀", Section(html), StringComparison.Ordinal);
        Assert.Contains("<form method=\"post\" action=\"/ui/appearance\"", html, StringComparison.Ordinal);
        Assert.Contains("name=\"__RequestVerificationToken\"", Section(html), StringComparison.Ordinal); // antiforgery
        Assert.Contains("name=\"returnUrl\" value=\"/ui/settings\"", Section(html), StringComparison.Ordinal);
        // 沒有 JavaScript 時多一顆「套用」，有腳本才收起（樣式表 html[data-js] .ep-no-js）。
        Assert.Contains("class=\"ep-btn ep-btn-quiet ep-no-js\"", Section(html), StringComparison.Ordinal);
        Assert.Contains("<a class=\"ep-btn ep-btn-primary\" href=\"/ui\">回後台</a>", html, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("light")]
    [InlineData("dark")]
    public async Task TheStoredChoiceIsTheSelectedOption(string choice)
    {
        using var client = factory.SignedInClient();

        var html = await BodyAsync(client, "/ui/settings", $"{Cookie}={choice}");

        var radios = Radios(html);
        Assert.Single(radios, r => r.Checked);
        Assert.Equal(choice, radios.Single(r => r.Checked).Value);
        Assert.Contains($"<html lang=\"zh-TW\" data-theme=\"{choice}\">", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnInvalidStoredChoiceSelectsTheSystemOnTheSettingsPage()
    {
        using var client = factory.SignedInClient();

        var html = await BodyAsync(client, "/ui/settings", $"{Cookie}=sepia");

        Assert.Equal("system", Radios(html).Single(r => r.Checked).Value);
        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(html), StringComparison.Ordinal);
    }

    // 選項是原生 radio 群組：role=radiogroup 由標題命名；每個 radio 同名、包在自己的 label 裡（點文字也能選），
    // 並指向自己的說明（螢幕閱讀器念出「跟隨裝置的外觀」）；圖示只是裝飾。方向鍵切換是原生 radio 的行為。
    [Fact]
    public async Task TheOptionsAreANativeRadioGroupWithAccessibleNamesAndDescriptions()
    {
        using var client = factory.SignedInClient();

        var section = Section(await BodyAsync(client, "/ui/settings"));

        var group = Regex.Match(section, "<fieldset[^>]*>", RegexOptions.None, TimeSpan.FromSeconds(1)).Value;
        Assert.Contains("role=\"radiogroup\"", group, StringComparison.Ordinal);
        Assert.Contains("aria-labelledby=\"ep-appearance-title\"", group, StringComparison.Ordinal);
        Assert.Single(Regex.Matches(section, "id=\"ep-appearance-title\""));
        Assert.Contains(">外觀</h2>", section, StringComparison.Ordinal);

        var inputs = Regex.Matches(section, "<input type=\"radio\"[^>]*>", RegexOptions.None, TimeSpan.FromSeconds(1)).Select(m => m.Value).ToList();
        Assert.Equal(3, inputs.Count);
        Assert.All(inputs, input => Assert.Contains("name=\"appearance\"", input, StringComparison.Ordinal));
        Assert.Equal(1, inputs.Count(input => input.Contains(" checked", StringComparison.Ordinal)));
        Assert.Equal(3, Regex.Count(section, "<label class=\"ep-segment\">"));
        Assert.Equal(3, Regex.Count(section, "aria-hidden=\"true\"")); // 三個圖示都是裝飾，名稱靠文字

        var hints = Regex.Matches(section, "<p id=\"(ep-appearance-hint-[a-z]+)\" class=\"ep-hint ep-appearance-hint\" data-for=\"([a-z]+)\">([^<]+)</p>", RegexOptions.None, TimeSpan.FromSeconds(1))
            .ToDictionary(m => m.Groups[2].Value, m => (Id: m.Groups[1].Value, Text: m.Groups[3].Value));
        Assert.Equal(["system", "light", "dark"], hints.Keys);
        Assert.Equal("跟隨裝置的外觀", hints["system"].Text);
        foreach (var input in inputs)
        {
            var value = Regex.Match(input, "value=\"([a-z]+)\"", RegexOptions.None, TimeSpan.FromSeconds(1)).Groups[1].Value;
            Assert.Contains($"aria-describedby=\"{hints[value].Id}\"", input, StringComparison.Ordinal);
        }

        // 失敗提醒是 role=status，沒有字時仍在無障礙樹裡。
        Assert.Contains("role=\"status\" data-appearance-status", section, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheEnglishPageNamesTheOptionsInEnglish()
    {
        using var client = factory.SignedInClient();

        var html = await BodyAsync(client, "/ui/settings", language: "en");

        Assert.Equal([("system", true, "System"), ("light", false, "Light"), ("dark", false, "Dark")], Radios(html));
        Assert.Contains(">Appearance</h2>", html, StringComparison.Ordinal);
        Assert.Contains("Matches your device's appearance", html, StringComparison.Ordinal);
        Assert.Contains("<h1 class=\"ep-page-title\">Personal settings</h1>", html, StringComparison.Ordinal);
        Assert.Contains("Apply", Section(html), StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheAccountMenuOfTheOperatorConsoleLinksToThePersonalSettings()
    {
        using var client = factory.SignedInClient();

        var html = await BodyAsync(client, "/ui");

        var menu = Regex.Match(html, "<div id=\"ep-ui-account-menu\".*?</div>", RegexOptions.Singleline, TimeSpan.FromSeconds(1)).Value;
        Assert.Contains("<a class=\"ep-menu-item\" href=\"/ui/settings\">個人設定</a>", menu, StringComparison.Ordinal);
        Assert.DoesNotContain("name=\"appearance\"", html, StringComparison.Ordinal); // 選單只連過去，不放切換
    }

    [Fact]
    public async Task TheOperatorSettingsNeedAnOperator()
    {
        using var anonymous = Anonymous();

        using var response = await anonymous.GetAsync(new Uri("/ui/settings", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/login", response.Headers.Location?.AbsolutePath);
    }

    // ---- 客戶入口的個人設定：和營運者共用同一個元件 -----------------------------------------------

    [Fact]
    public async Task ThePortalHasThePersonalSettingsInItsMenuAndSharesTheComponent()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "appearance-portal");
        var home = Home(tenant.Identifier);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync($"{home}/settings");
        using var operatorClient = factory.SignedInClient();
        var operatorPage = await BodyAsync(operatorClient, "/ui/settings");

        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("<title>個人設定 ·", page.Html, StringComparison.Ordinal);
        Assert.Contains($"<form method=\"post\" action=\"{home}/appearance\"", page.Html, StringComparison.Ordinal);
        Assert.Equal([("system", true, "系統"), ("light", false, "亮色"), ("dark", false, "暗色")], Radios(page.Html));
        Assert.Contains($"<a class=\"ep-menu-item\" href=\"{home}/settings\">個人設定</a>", page.Html, StringComparison.Ordinal);
        // 同一個元件：兩邊的標記除了端點、返回位址與 antiforgery 權杖外逐字相同。
        Assert.Equal(Normalize(Section(operatorPage)), Normalize(Section(page.Html)));
        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(page.Html), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ThePortalSettingsNeedASignIn()
    {
        var (tenant, _, _) = await MemberAsync(factory, "appearance-guard");
        using var browser = new PortalBrowser(factory);

        var page = await browser.GetAsync($"{Home(tenant.Identifier)}/settings");

        Assert.Equal(HttpStatusCode.Redirect, page.Status);
        Assert.StartsWith(Login(tenant.Identifier), page.Location, StringComparison.Ordinal);
    }

    // 從頭走一遍：登入、在個人設定選暗色（沒有 JavaScript 的表單送出）、導回個人設定、整個入口都是暗色且暗色被選中；
    // 再選回「系統」就回到什麼都沒存的狀態。
    [Fact]
    public async Task ChoosingInThePortalSettingsAppliesEverywhereAndSystemClearsIt()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "appearance-flow");
        var home = Home(tenant.Identifier);
        var settings = $"{home}/settings";
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var chosen = await browser.SubmitAsync(settings, new() { ["appearance"] = "dark" }, $"{home}/appearance");

        Assert.Equal(HttpStatusCode.Redirect, chosen.Status);
        Assert.Equal(settings, chosen.Location);
        var after = await browser.GetAsync(settings);
        Assert.Contains("data-theme=\"dark\"", PageMarkup.HtmlTag(after.Html), StringComparison.Ordinal);
        Assert.Equal("dark", Radios(after.Html).Single(r => r.Checked).Value);
        Assert.Contains("data-theme=\"dark\"", PageMarkup.HtmlTag((await browser.GetAsync(home)).Html), StringComparison.Ordinal);
        Assert.Contains("data-theme=\"dark\"", PageMarkup.HtmlTag((await browser.GetAsync(Login(tenant.Identifier))).Html), StringComparison.Ordinal); // 匿名頁也尊重

        await browser.SubmitAsync(settings, new() { ["appearance"] = "light" }, $"{home}/appearance");
        Assert.Contains("data-theme=\"light\"", PageMarkup.HtmlTag((await browser.GetAsync(home)).Html), StringComparison.Ordinal);

        await browser.SubmitAsync(settings, new() { ["appearance"] = "system" }, $"{home}/appearance");
        var system = await browser.GetAsync(settings);
        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(system.Html), StringComparison.Ordinal);
        Assert.Equal("system", Radios(system.Html).Single(r => r.Checked).Value);
    }

    // ---- 端點 ------------------------------------------------------------------------------------

    [Fact]
    public async Task TheOperatorEndpointStoresTheChoiceAndReturnsToThePage()
    {
        using var browser = new PortalBrowser(factory);

        var chosen = await browser.SubmitAsync("/ui/login", new() { ["appearance"] = "dark", ["returnUrl"] = "/ui/settings" }, "/ui/appearance");

        Assert.Equal(HttpStatusCode.Redirect, chosen.Status);
        Assert.Equal("/ui/settings", chosen.Location);
        Assert.Contains("data-theme=\"dark\"", PageMarkup.HtmlTag((await browser.GetAsync("/ui/login")).Html), StringComparison.Ordinal);

        await browser.SubmitAsync("/ui/login", new() { ["appearance"] = "system", ["returnUrl"] = "/ui/settings" }, "/ui/appearance");
        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles((await browser.GetAsync("/ui/login")).Html), StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("sepia")]
    [InlineData("")]
    [InlineData("dark; Path=/evil")]
    [InlineData("auto")]
    public async Task AnUnknownChoiceIsRejectedAndSetsNothing(string choice)
    {
        using var browser = new PortalBrowser(factory);

        var chosen = await browser.SubmitAsync("/ui/login", new() { ["appearance"] = choice, ["returnUrl"] = "/ui/settings" }, "/ui/appearance");

        Assert.Equal(HttpStatusCode.BadRequest, chosen.Status);
        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles((await browser.GetAsync("/ui/login")).Html), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ChoosingNeedsAnAntiforgeryTokenSoALinkCannotDoIt()
    {
        using var client = Anonymous();
        using var form = new FormUrlEncodedContent(new Dictionary<string, string> { ["appearance"] = "dark", ["returnUrl"] = "/ui/settings" });

        using var response = await client.PostAsync(new Uri("/ui/appearance", UriKind.Relative), form, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.False(response.Headers.Contains("Set-Cookie"));
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

        var chosen = await browser.SubmitAsync("/ui/login", new() { ["appearance"] = "dark", ["returnUrl"] = returnUrl }, "/ui/appearance");

        Assert.Equal("/ui", chosen.Location);
    }

    [Fact]
    public async Task ThePortalEndpointStaysInsideTheTenantsPortal()
    {
        var (tenant, _, _) = await MemberAsync(factory, "appearance-return");
        using var browser = new PortalBrowser(factory);

        var chosen = await browser.SubmitAsync(Login(tenant.Identifier), new() { ["appearance"] = "light", ["returnUrl"] = "https://evil.example/" }, $"{Home(tenant.Identifier)}/appearance");

        Assert.Equal(Home(tenant.Identifier), chosen.Location);
    }

    [Fact]
    public async Task AnUnknownTenantHasNoAppearanceEndpoint()
    {
        using var client = Anonymous();
        using var form = new FormUrlEncodedContent(new Dictionary<string, string> { ["appearance"] = "dark" });

        using var response = await client.PostAsync(new Uri("/portal/no-such-tenant-xyz/appearance", UriKind.Relative), form, TestContext.Current.CancellationToken);

        Assert.True(response.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.BadRequest);
        Assert.False(response.Headers.Contains("Set-Cookie"));
    }

    // 腳本用 fetch 送同一張表單並要 JSON：回 204，不轉址；cookie 的屬性不變（HttpOnly、Lax、一年）。
    [Fact]
    public async Task AScriptRequestGetsNoContentInsteadOfARedirect()
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { BaseAddress = new Uri("https://localhost"), AllowAutoRedirect = false, HandleCookies = true });
        var page = await client.GetStringAsync(new Uri("/ui/login", UriKind.Relative), TestContext.Current.CancellationToken);
        var fields = new Dictionary<string, string>(PortalBrowser.HiddenFields(page)) { ["appearance"] = "dark" };
        using var request = new HttpRequestMessage(HttpMethod.Post, new Uri("/ui/appearance", UriKind.Relative)) { Content = new FormUrlEncodedContent(fields) };
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
        Assert.Null(response.Headers.Location);
        var cookie = Assert.Single(response.Headers.GetValues("Set-Cookie"), c => c.StartsWith("ep-appearance=", StringComparison.Ordinal));
        Assert.StartsWith("ep-appearance=dark", cookie, StringComparison.Ordinal);
        Assert.Contains("httponly", cookie, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("samesite=lax", cookie, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("max-age=31536000", cookie, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("secure", cookie, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("data-theme=\"dark\"", PageMarkup.HtmlTag(await client.GetStringAsync(new Uri("/ui/login", UriKind.Relative), TestContext.Current.CancellationToken)), StringComparison.Ordinal);
    }

    // cookie 的名稱來自選項，不是寫死：換掉選項，伺服器就認新的名字、不認舊的。
    [Fact]
    public async Task TheCookieNameComesFromTheOptions()
    {
        await using var custom = factory.WithWebHostBuilder(builder =>
            builder.ConfigureServices(services => services.Configure<AppearanceOptions>(o => o.Cookie.Name = "custom-appearance")));
        using var client = custom.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        Assert.Contains("data-theme=\"dark\"", PageMarkup.HtmlTag(await BodyAsync(client, "/ui/login", "custom-appearance=dark")), StringComparison.Ordinal);
        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(await BodyAsync(client, "/ui/login", "ep-appearance=dark")), StringComparison.Ordinal);
    }

    private static string Normalize(string section) =>
        NoiseInSection().Replace(section, "$1…");

    [GeneratedRegex("""<section aria-labelledby="ep-appearance-title">.*?</section>""", RegexOptions.Singleline, matchTimeoutMilliseconds: 1000)]
    private static partial Regex SettingSection();

    [GeneratedRegex("""<label class="ep-segment">\s*<input type="radio"[^>]*?value="(?<value>[a-z]+)"[^>]*>.*?<span class="ep-segment-label">(?<label>[^<]*)</span>\s*</label>""", RegexOptions.Singleline, matchTimeoutMilliseconds: 1000)]
    private static partial Regex RadioLabel();

    // 端點（action）、返回位址與每次不同的 antiforgery 權杖，是兩邊唯一允許不同的值。
    [GeneratedRegex("""((?:action|value)=")(?:/ui/appearance|/ui/settings|/portal/[^"/]+/appearance|/portal/[^"/]+/settings|[A-Za-z0-9_\-]{40,})(?=")""", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex NoiseInSection();
}