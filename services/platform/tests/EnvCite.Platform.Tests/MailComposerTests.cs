using System.Globalization;
using System.Net;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using System.Xml.Linq;

using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

using static EnvCite.Platform.Tests.MailTestServices;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 信件的組成（邀請、驗證、<c>mail test</c>）：用 <see cref="MailServiceCollectionExtensions.AddPlatformMail"/> 註冊的真實 <see cref="IMailComposer"/> 測，
/// 不 new 任何東西手動接線。純文字與 HTML 兩版語意相同；信裡唯一的外部網址是啟用連結；插入的值由 Razor 編碼；
/// 版面是表格加內嵌樣式（傳統 Outlook 也要能讀）；版型可由設定覆寫並在啟動驗證。不連網路也不連資料庫。
/// </summary>
public sealed partial class MailComposerTests
{
    private const string Brand = "環境知識";

    private static readonly Uri Link = new("https://platform.test/portal/acme/activate?userId=u-1&code=Zm9vYmFy");

    // 邀請與驗證、有租戶與沒租戶：四種組合。
    public static TheoryData<bool, string?> Combinations => new()
    {
        { true, null },
        { true, "acme" },
        { false, null },
        { false, "acme" },
    };

    private static async Task<ComposedMail> ActivationAsync(
        bool invitation, string? tenant, TimeSpan? lifetime = null, Uri? link = null, Dictionary<string, string?>? settings = null)
    {
        await using var provider = Build(settings);
        return await provider.GetRequiredService<IMailComposer>()
            .ComposeActivationAsync(new ActivationMailModel(link ?? Link, lifetime ?? TimeSpan.FromDays(3), tenant, invitation), TestContext.Current.CancellationToken);
    }

    private static async Task<ComposedMail> TestMailAsync(Dictionary<string, string?>? settings = null)
    {
        await using var provider = Build(settings);
        return await provider.GetRequiredService<IMailComposer>().ComposeTestAsync(TestContext.Current.CancellationToken);
    }

    [GeneratedRegex(@"<table\b", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex TableOpen();

    [GeneratedRegex(@"<table role=""presentation""", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex PresentationTableOpen();

    [GeneratedRegex(@"<a\b[^>]*>(?<text>.*?)</a>", RegexOptions.Singleline, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Anchor();

    [GeneratedRegex(@"href=""(?<url>[^""]*)""", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Href();

    [GeneratedRegex(@"https?:|//|<img|<link|<script|<iframe|<video|<audio|<form|<object|<embed|url\(|@import|background=|src=", RegexOptions.IgnoreCase, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Remote();

    [GeneratedRegex(@"mso-hide:all;[^>]*>(?<text>[^<]*)</div>", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Preheader();

    [GeneratedRegex(@"\p{IsCJKUnifiedIdeographs}|[，。：「」]", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Chinese();

    // 同一條連結、同樣的期限與提示、同樣的租戶；兩版各有各的措辭（HTML 的說明指向下方按鈕），但事實一致。
    [Theory]
    [MemberData(nameof(Combinations))]
    public async Task TheTextAndTheHtmlCarryTheSameLinkLifetimeNoticeAndTenant(bool invitation, string? tenant)
    {
        var mail = (await ActivationAsync(invitation, tenant)).Content;
        var html = WebUtility.HtmlDecode(mail.Html);

        foreach (var version in new[] { mail.Text, html })
        {
            Assert.Contains(Link.AbsoluteUri, version, StringComparison.Ordinal);
            Assert.Contains("連結 3 天內有效，而且只能使用一次。", version, StringComparison.Ordinal);
            Assert.Contains("如果您沒有預期收到這封信，請忽略它：在您開啟連結之前，不會有任何變更。", version, StringComparison.Ordinal);
            Assert.Equal(tenant is not null, version.Contains($"租戶識別碼：{tenant}", StringComparison.Ordinal));
            Assert.Equal(tenant is not null, version.Contains("租戶識別碼", StringComparison.Ordinal));
        }

        // 系統名稱：HTML 的標頭與頁尾一定有；純文字版只在邀請的說明句裡（驗證信的說明句不帶名稱，措辭維持原樣）。
        Assert.Contains(Brand, html, StringComparison.Ordinal);
        Assert.Equal(invitation, mail.Text.Contains(Brand, StringComparison.Ordinal));
        Assert.Equal(invitation, mail.Text.Contains($"已為您在「{Brand}」建立帳號", StringComparison.Ordinal));
        Assert.Equal(invitation, html.Contains($"已為您在「{Brand}」建立帳號", StringComparison.Ordinal));
        Assert.Contains(invitation ? "邀請您啟用帳號" : "請驗證您的信箱", html, StringComparison.Ordinal);
        Assert.Contains(invitation ? "設定密碼並啟用帳號" : "驗證信箱", html, StringComparison.Ordinal);
        Assert.Contains("按鈕無法點選時，請複製下列連結到瀏覽器：", html, StringComparison.Ordinal);
        Assert.Contains(invitation ? "完成信箱驗證" : "驗證這個信箱", mail.Text, StringComparison.Ordinal);
    }

    // 主旨維持原本的格式；HTML 檔案裡的中文是中文，不是一串數字實體。
    [Theory]
    [InlineData(true, "環境知識：邀請您啟用帳號")]
    [InlineData(false, "環境知識：請驗證您的信箱")]
    public async Task TheSubjectKeepsItsFormatAndTheHtmlIsReadableUtf8(bool invitation, string subject)
    {
        var mail = await ActivationAsync(invitation, "acme");

        Assert.Equal(subject, mail.Subject);
        Assert.Contains(invitation ? "邀請您啟用帳號" : "請驗證您的信箱", mail.Content.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("&#x9", mail.Content.Html, StringComparison.Ordinal); // 預設的編碼器會把中文轉成 &#x…;
    }

    // 純文字版與改版之前逐字相同（既有的呼叫端與收件者不受影響）。
    [Fact]
    public async Task TheTextVersionKeepsTheOriginalWording()
    {
        var text = (await ActivationAsync(invitation: true, "acme")).Content.Text;

        Assert.Equal(
            $"您好，\n\n已為您在「{Brand}」建立帳號。請開啟下列連結，設定您自己的密碼並完成信箱驗證：\n\n{Link.AbsoluteUri}\n\n"
            + "租戶識別碼：acme\n連結 3 天內有效，而且只能使用一次。\n如果您沒有預期收到這封信，請忽略它：在您開啟連結之前，不會有任何變更。",
            text);
    }

    // 沒有遠端圖片、追蹤像素、點擊追蹤轉址、腳本或外部樣式：信裡唯一的外部網址是啟用連結。
    [Theory]
    [MemberData(nameof(Combinations))]
    public async Task TheActivationLinkIsTheOnlyExternalAddressInTheHtml(bool invitation, string? tenant)
    {
        var html = (await ActivationAsync(invitation, tenant)).Content.Html;

        // 連結出現三次：VML 按鈕、一般按鈕、可複製的備用文字；其餘不含任何網址或遠端資源。
        var encodedLink = WebUtility.HtmlEncode(Link.AbsoluteUri);
        Assert.Equal(3, CountOccurrences(html, encodedLink));
        var rest = html.Replace(encodedLink, string.Empty, StringComparison.Ordinal);
        Assert.DoesNotMatch(Remote(), rest);
    }

    // 連結文字要有意義：按鈕寫動詞，不是網址或「點這裡」。
    [Theory]
    [InlineData(true, "設定密碼並啟用帳號")]
    [InlineData(false, "驗證信箱")]
    public async Task TheOnlyAnchorTextIsTheVerbOfTheButton(bool invitation, string label)
    {
        var html = (await ActivationAsync(invitation, tenant: null)).Content.Html;

        var texts = Anchor().Matches(html).Select(m => m.Groups["text"].Value).ToList();

        Assert.Equal([label], texts);
    }

    // 插入的值（系統名稱、租戶、連結）由 Razor 編碼：含 < " & ' 的值不能長出標籤或掙脫屬性。
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task EveryInsertedValueIsHtmlEncoded(bool invitation)
    {
        const string HostileBrand = "A<script>&\"'x";
        const string HostileTenant = "t\"><img src=x onerror=alert(1)>";
        var link = new Uri("https://platform.test/x?userId=a&code=b'c<d\">");

        var mail = (await ActivationAsync(invitation, HostileTenant, link: link, settings: With(("Platform:Brand:Name", HostileBrand)))).Content;

        // 危險字元都被編碼：原始值不會出現在輸出裡，解碼後才是原本的值。
        Assert.DoesNotContain(HostileBrand, mail.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(HostileTenant, mail.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("<script", mail.Html, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("<img", mail.Html, StringComparison.OrdinalIgnoreCase);
        var decoded = WebUtility.HtmlDecode(mail.Html);
        Assert.Contains(HostileBrand, decoded, StringComparison.Ordinal);
        Assert.Contains($"租戶識別碼：{HostileTenant}", decoded, StringComparison.Ordinal);

        // 每個 href 都是完整的一個屬性值，解碼後正好是那條連結（沒有被 ' 或 " 截斷）。
        var hrefs = Href().Matches(mail.Html).Select(m => WebUtility.HtmlDecode(m.Groups["url"].Value)).ToList();
        Assert.Equal(2, hrefs.Count);
        Assert.All(hrefs, href => Assert.Equal(link.AbsoluteUri, href));

        // 純文字版不是 HTML，原樣呈現。
        Assert.Contains($"租戶識別碼：{HostileTenant}", mail.Text, StringComparison.Ordinal);
        Assert.Equal(invitation, mail.Text.Contains(HostileBrand, StringComparison.Ordinal));
    }

    [Fact]
    public async Task TheTestMailEncodesTheNameToo()
    {
        const string HostileBrand = "A<script>&\"'x";

        var mail = (await TestMailAsync(With(("Platform:Brand:Name", HostileBrand)))).Content;

        Assert.DoesNotContain("<script", mail.Html, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(HostileBrand, mail.Html, StringComparison.Ordinal);
        Assert.Contains(HostileBrand, WebUtility.HtmlDecode(mail.Html), StringComparison.Ordinal);
    }

    // mail test：沒有連結、沒有密碼，措辭維持原樣；HTML 版套同一個版型但沒有按鈕、沒有任何網址。
    [Fact]
    public async Task TheTestMailHasNoLinkNoButtonAndNoPassword()
    {
        var mail = await TestMailAsync();
        var html = WebUtility.HtmlDecode(mail.Content.Html);

        Assert.Equal("環境知識：寄信測試", mail.Subject);
        Assert.Equal($"這是一封測試信，用來確認「{Brand}」的寄信設定與中繼可用。\n沒有連結，也沒有密碼；收到後不需要做任何事。", mail.Content.Text);
        Assert.Contains($"這是一封測試信，用來確認「{Brand}」的寄信設定與中繼可用。", html, StringComparison.Ordinal);
        Assert.Contains("沒有連結，也沒有密碼；收到後不需要做任何事。", html, StringComparison.Ordinal);
        Assert.Contains("寄信測試", html, StringComparison.Ordinal);
        Assert.DoesNotMatch(Remote(), mail.Content.Html);
        Assert.DoesNotContain("<a ", mail.Content.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("v:roundrect", mail.Content.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("http", mail.Content.Text, StringComparison.OrdinalIgnoreCase);
    }

    // 兩種信共用同一個版型：標題字樣、頁尾、語言、表格版面、預覽文字。
    [Fact]
    public async Task AllMailsShareTheLayout()
    {
        MailContent[] mails =
        [
            (await ActivationAsync(true, "acme")).Content,
            (await ActivationAsync(false, null)).Content,
            (await TestMailAsync()).Content,
        ];

        foreach (var mail in mails)
        {
            var decoded = WebUtility.HtmlDecode(mail.Html);
            Assert.StartsWith("<!DOCTYPE html>", mail.Html, StringComparison.Ordinal);
            Assert.Contains("<html lang=\"zh-Hant\"", mail.Html, StringComparison.Ordinal);
            Assert.Contains("<meta charset=\"utf-8\">", mail.Html, StringComparison.Ordinal);
            Assert.Contains($"這是系統自動寄出的信 · {Brand}", mail.Html, StringComparison.Ordinal);
            Assert.Contains("'PingFang TC','Microsoft JhengHei','Noto Sans TC',system-ui,sans-serif", decoded, StringComparison.Ordinal);
            Assert.Contains("letter-spacing:.08em", mail.Html, StringComparison.Ordinal);
            Assert.Contains("max-width:600px", mail.Html, StringComparison.Ordinal);
            Assert.Contains("<td width=\"600\">", mail.Html, StringComparison.Ordinal); // Outlook 的固定寬度後備表格
            Assert.Contains("padding:28px 32px", mail.Html, StringComparison.Ordinal);
            Assert.Contains(".container { padding: 22px 18px !important; }", mail.Html, StringComparison.Ordinal);
            Assert.DoesNotContain("display:flex", mail.Html, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("display:grid", mail.Html, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("display: flex", mail.Html, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("display: grid", mail.Html, StringComparison.OrdinalIgnoreCase);

            // 版面表格一律標 role="presentation"（螢幕閱讀器不要當資料表朗讀）。
            Assert.Equal(TableOpen().Count(mail.Html), PresentationTableOpen().Count(mail.Html));
        }
    }

    [Fact]
    public async Task ThePreheaderIsTheLifetimeSentenceAndShortEnoughForAnInboxPreview()
    {
        var html = (await ActivationAsync(invitation: true, tenant: "acme", lifetime: TimeSpan.FromHours(12))).Content.Html;

        var preheader = WebUtility.HtmlDecode(Preheader().Match(html).Groups["text"].Value)
            .Replace("‌", string.Empty, StringComparison.Ordinal)
            .Replace(" ", string.Empty, StringComparison.Ordinal);

        Assert.Equal("連結 12 小時內有效，只能使用一次", preheader);
        Assert.InRange(preheader.Length, 1, 100);
        Assert.Contains("display:none", html, StringComparison.Ordinal);
        Assert.Contains("mso-hide:all", html, StringComparison.Ordinal);
    }

    // 橋接按鈕：padding 在連結上、整塊可點；Outlook 用條件註解包 VML 圓角矩形。
    [Fact]
    public async Task TheButtonIsABridgeButtonWithAVmlFallbackForOutlook()
    {
        var html = (await ActivationAsync(invitation: true, tenant: null)).Content.Html;

        Assert.Contains("<a class=\"btn\" href=\"", html, StringComparison.Ordinal);
        Assert.Contains("display:block;padding:13px 20px;", html, StringComparison.Ordinal);
        Assert.Contains("border-radius:8px", html, StringComparison.Ordinal);
        Assert.Contains("font-size:16px", html, StringComparison.Ordinal);
        Assert.Contains("background-color:#185fa5", html, StringComparison.Ordinal);
        Assert.Contains("color:#ffffff", html, StringComparison.Ordinal);

        var vml = html.IndexOf("<!--[if mso]>\n<v:roundrect", StringComparison.Ordinal);
        var plain = html.IndexOf("<!--[if !mso]><!-->", StringComparison.Ordinal);
        Assert.InRange(vml, 0, plain - 1); // VML 先、一般按鈕後；Outlook 只看得到前者
        Assert.Contains("<![endif]-->", html, StringComparison.Ordinal);
        Assert.Contains("<!--<![endif]-->", html, StringComparison.Ordinal);
        Assert.Contains("arcsize=", html, StringComparison.Ordinal);
        Assert.Contains("<w:anchorlock/>", html, StringComparison.Ordinal);
        Assert.Contains("width:536px", html, StringComparison.Ordinal); // 600 扣掉左右內距 32px
    }

    // 深色配色用 prefers-color-scheme 與 color-scheme 宣告；不用純黑純白。
    [Fact]
    public async Task TheDarkPaletteIsAppliedThroughPrefersColorScheme()
    {
        var html = (await ActivationAsync(invitation: true, tenant: null)).Content.Html;

        Assert.Contains("@media (prefers-color-scheme: dark)", html, StringComparison.Ordinal);
        Assert.Contains("<meta name=\"color-scheme\" content=\"light dark\">", html, StringComparison.Ordinal);
        Assert.Contains(":root { color-scheme: light dark;", html, StringComparison.Ordinal);
        var darkCss = html[html.IndexOf("@media (prefers-color-scheme: dark)", StringComparison.Ordinal)..];
        foreach (var color in new[] { "#16191d", "#1f242a", "#e6e9ec", "#a3adb8", "#343b43", "#85b7eb", "#042c53", "#2a3038" })
        {
            Assert.Contains(color, darkCss, StringComparison.Ordinal);
        }

        // 不用純黑純白：深色的每個顏色，與淺色的頁面、文字（淺色的卡片與按鈕字是白色）。
        await using var provider = Build();
        var options = provider.GetRequiredService<IOptions<MailTemplateOptions>>().Value;
        string[] pure = ["#000000", "#ffffff"];
        var dark = options.Dark;
        Assert.All(
            new[] { dark.Page, dark.Card, dark.Text, dark.Muted, dark.Line, dark.Soft, dark.Brand, dark.Button, dark.ButtonText },
            c => Assert.DoesNotContain(c, pure));
        Assert.All(new[] { options.Light.Page, options.Light.Text }, c => Assert.DoesNotContain(c, pure));
    }

    // 預設色票的文字對比度至少 4.5:1（WCAG AA），淺色與深色兩組都要過（驗證器另有測試守住覆寫值）。
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void EveryDefaultTextColorMeetsAaContrastOnItsBackground(bool dark)
    {
        using var provider = Build();
        var options = provider.GetRequiredService<IOptions<MailTemplateOptions>>().Value;
        var p = dark ? options.Dark : options.Light;

        (string Name, string Foreground, string Background)[] pairs =
        [
            ("內文", p.Text, p.Card),
            ("次要文字", p.Muted, p.Card),
            ("頁尾", p.Muted, p.Page),
            ("淺底上的次要文字", p.Muted, p.Soft),
            ("淺底上的內文", p.Text, p.Soft),
            ("品牌色", p.Brand, p.Card),
            ("按鈕", p.ButtonText, p.Button),
        ];
        foreach (var (name, foreground, background) in pairs)
        {
            var ratio = Contrast(foreground, background);
            Assert.True(ratio >= 4.5, $"{(dark ? "dark" : "light")} {name}：{foreground} 在 {background} 上的對比度 {ratio:F2} 低於 4.5");
        }
    }

    [Theory]
    [InlineData(3 * 24, "3 天")]
    [InlineData(24, "1 天")]
    [InlineData(23, "23 小時")]
    [InlineData(12, "12 小時")]
    [InlineData(1, "1 小時")]
    public async Task TheLifetimeReadsInDaysOrHours(int hours, string expected)
    {
        var mail = (await ActivationAsync(invitation: true, tenant: null, lifetime: TimeSpan.FromHours(hours))).Content;

        Assert.Contains($"連結 {expected}內有效，而且只能使用一次。", mail.Text, StringComparison.Ordinal);
        Assert.Contains($"連結 {expected}內有效，而且只能使用一次。", WebUtility.HtmlDecode(mail.Html), StringComparison.Ordinal);
    }

    // 版型的可調值走選項：色票、寬度、內距、字型都能由設定覆寫，而且是同一份值進內嵌樣式、樣式表與 Outlook 的 VML。
    [Fact]
    public async Task TheLookFollowsTheTemplateOptions()
    {
        var settings = With(
            ("Platform:Mail:Template:Width", "560"),
            ("Platform:Mail:Template:PaddingHorizontal", "20"),
            ("Platform:Mail:Template:CompactPaddingHorizontal", "12"),
            ("Platform:Mail:Template:FontFamily", "'Noto Sans TC',sans-serif"),
            ("Platform:Mail:Template:Light:Button", "#0b5cad"),
            ("Platform:Mail:Template:Dark:Button", "#9ec5f2"));

        var html = (await ActivationAsync(invitation: true, tenant: null, settings: settings)).Content.Html;
        var decoded = WebUtility.HtmlDecode(html);

        Assert.Contains("max-width:560px", html, StringComparison.Ordinal);
        Assert.Contains("<td width=\"560\">", html, StringComparison.Ordinal);
        Assert.Contains("padding:28px 20px", html, StringComparison.Ordinal);
        Assert.Contains(".container { padding: 22px 12px !important; }", html, StringComparison.Ordinal);
        Assert.Contains("width:520px", html, StringComparison.Ordinal); // 560 扣掉左右內距 20px
        Assert.Contains("font-family:'Noto Sans TC',sans-serif;", decoded, StringComparison.Ordinal);
        Assert.DoesNotContain("#185fa5;border-radius:8px", html, StringComparison.Ordinal);
        Assert.Contains("background-color:#0b5cad", html, StringComparison.Ordinal);
        Assert.Contains("fillcolor=\"#0b5cad\"", html, StringComparison.Ordinal);
        Assert.Contains(".btn { background-color: #9ec5f2 !important;", html, StringComparison.Ordinal);
    }

    // 信件的語言由設定決定（信是寄給還沒登入過的人）：英文版沒有任何中文，語系屬性與主旨也跟著變。
    [Fact]
    public async Task TheEnglishMailHasNoChineseAndTheCallersCultureIsUntouched()
    {
        var settings = With(("Platform:Mail:Template:Culture", "en"), ("Platform:Brand:Name", "Acme Law"));
        var before = (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture);

        var invitation = await ActivationAsync(invitation: true, "acme", settings: settings);
        var verification = await ActivationAsync(invitation: false, null, settings: settings);
        var test = await TestMailAsync(settings);

        Assert.Equal("Acme Law: You're invited to activate your account", invitation.Subject);
        Assert.Equal("Acme Law: Please verify your email address", verification.Subject);
        Assert.Equal("Acme Law: Mail delivery test", test.Subject);
        foreach (var mail in new[] { invitation, verification, test })
        {
            Assert.DoesNotMatch(Chinese(), mail.Subject + mail.Content.Text + WebUtility.HtmlDecode(mail.Content.Html));
            Assert.Contains("<html lang=\"en\"", mail.Content.Html, StringComparison.Ordinal);
        }

        Assert.StartsWith("Hello,", invitation.Content.Text, StringComparison.Ordinal);
        Assert.Contains("The link is valid for 3 day(s) and can be used only once.", invitation.Content.Text, StringComparison.Ordinal);
        Assert.Contains("Tenant identifier: acme", invitation.Content.Text, StringComparison.Ordinal);
        Assert.Contains(">Set password and activate account</a>", invitation.Content.Html, StringComparison.Ordinal);
        Assert.Contains(">Verify email address</a>", verification.Content.Html, StringComparison.Ordinal);
        Assert.Equal(before, (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture));
    }

    // 組信不受呼叫端目前語言的影響（操作者可能用英文介面）：信的語言只看設定。
    [Fact]
    public async Task TheMailLanguageDoesNotFollowTheCallersCulture()
    {
        using var _ = TestCulture.Use("en");

        var mail = await ActivationAsync(invitation: true, "acme");

        Assert.Equal("環境知識：邀請您啟用帳號", mail.Subject);
        Assert.Contains("設定密碼並啟用帳號", mail.Content.Html, StringComparison.Ordinal);
    }

    // 文案兩個語言的鍵與參數個數一致，每個值都不是空的；少一個鍵，那個語言的信就會丟例外而不是寄出鍵名。
    [Fact]
    public void BothLanguagesOfTheMailTextsDefineTheSameKeysAndPlaceholders()
    {
        var zh = LoadTexts("MailTexts.resx");
        var en = LoadTexts("MailTexts.en.resx");

        Assert.Empty(zh.Keys.Except(en.Keys));
        Assert.Empty(en.Keys.Except(zh.Keys));
        Assert.All(zh.Concat(en), pair => Assert.False(string.IsNullOrWhiteSpace(pair.Value), pair.Key));
        foreach (var (key, value) in zh)
        {
            Assert.Equal(
                [.. Placeholder().Matches(value).Select(m => m.Value).Order()],
                [.. Placeholder().Matches(en[key]).Select(m => m.Value).Order()]);
        }
    }

    [GeneratedRegex(@"\{\d+\}", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Placeholder();

    private static Dictionary<string, string> LoadTexts(string file, [CallerFilePath] string here = "") =>
        XDocument.Load(Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.SharedKernel", file)))
            .Root!.Elements("data")
            .ToDictionary(e => (string)e.Attribute("name")!, e => (string)e.Element("value")!, StringComparer.Ordinal);

    private static int CountOccurrences(string text, string value) => text.Split(value, StringSplitOptions.None).Length - 1;

    // 獨立於驗證器的對比度算法（WCAG 2.x），讓預設色票有第二個見證。
    private static double Contrast(string foreground, string background)
    {
        var (a, b) = (Luminance(foreground), Luminance(background));
        return (Math.Max(a, b) + 0.05) / (Math.Min(a, b) + 0.05);
    }

    private static double Luminance(string hex)
    {
        static double Channel(string pair)
        {
            var value = int.Parse(pair, NumberStyles.HexNumber, CultureInfo.InvariantCulture) / 255.0;
            return value <= 0.03928 ? value / 12.92 : Math.Pow((value + 0.055) / 1.055, 2.4);
        }

        return (0.2126 * Channel(hex[1..3])) + (0.7152 * Channel(hex[3..5])) + (0.0722 * Channel(hex[5..7]));
    }
}