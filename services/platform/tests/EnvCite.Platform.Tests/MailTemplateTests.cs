using System.Globalization;
using System.Net;
using System.Text.RegularExpressions;

using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 信件版型（邀請、驗證、<c>mail test</c>）：純文字與 HTML 兩版語意相同；信裡唯一的外部網址是啟用連結；
/// 插入的值一律 HTML 編碼；版面是表格加內嵌樣式（傳統 Outlook 也要能讀）。不連網路也不連資料庫。
/// </summary>
public sealed partial class MailTemplateTests
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

    private static MailContent Activation(bool invitation, string? tenant, string brand = Brand, Uri? link = null, TimeSpan? lifetime = null) =>
        MailTemplate.Activation(brand, link ?? Link, lifetime ?? TimeSpan.FromDays(3), tenant, invitation);

    [GeneratedRegex(@"<table\b", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex TableOpen();

    [GeneratedRegex(@"<table role=""presentation""", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex PresentationTableOpen();

    [GeneratedRegex(@"<a\b[^>]*>(?<text>.*?)</a>", RegexOptions.Singleline, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Anchor();

    [GeneratedRegex(@"https?:|//|<img|<link|<script|<iframe|<video|<audio|<form|<object|<embed|url\(|@import|background=|src=", RegexOptions.IgnoreCase, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Remote();

    [GeneratedRegex(@"mso-hide:all;[^>]*>(?<text>[^<]*)</div>", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Preheader();

    // 同一條連結、同樣的期限與提示、同樣的租戶；兩版各有各的措辭（HTML 的說明指向下方按鈕），但事實一致。
    [Theory]
    [MemberData(nameof(Combinations))]
    public void TheTextAndTheHtmlCarryTheSameLinkLifetimeNoticeAndTenant(bool invitation, string? tenant)
    {
        var mail = Activation(invitation, tenant);
        var html = WebUtility.HtmlDecode(mail.Html);

        foreach (var version in new[] { mail.Text, html })
        {
            Assert.Contains(Link.AbsoluteUri, version, StringComparison.Ordinal);
            Assert.Contains("連結 3 天內有效，而且只能使用一次。", version, StringComparison.Ordinal);
            Assert.Contains(MailTemplate.IgnoreNotice, version, StringComparison.Ordinal);
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

    // 純文字版與改版之前逐字相同（既有的呼叫端與收件者不受影響）。
    [Fact]
    public void TheTextVersionKeepsTheOriginalWording()
    {
        var text = Activation(invitation: true, "acme").Text;

        Assert.Equal(
            $"您好，\n\n已為您在「{Brand}」建立帳號。請開啟下列連結，設定您自己的密碼並完成信箱驗證：\n\n{Link.AbsoluteUri}\n\n"
            + "租戶識別碼：acme\n連結 3 天內有效，而且只能使用一次。\n如果您沒有預期收到這封信，請忽略它：在您開啟連結之前，不會有任何變更。",
            text);
    }

    // 沒有遠端圖片、追蹤像素、點擊追蹤轉址、腳本或外部樣式：信裡唯一的外部網址是啟用連結。
    [Theory]
    [MemberData(nameof(Combinations))]
    public void TheActivationLinkIsTheOnlyExternalAddressInTheHtml(bool invitation, string? tenant)
    {
        var html = Activation(invitation, tenant).Html;
        var encodedLink = WebUtility.HtmlEncode(Link.AbsoluteUri);

        // 連結出現三次：VML 按鈕、一般按鈕、可複製的備用文字。
        Assert.Equal(3, html.Split(encodedLink, StringSplitOptions.None).Length - 1);
        Assert.DoesNotMatch(Remote(), html.Replace(encodedLink, string.Empty, StringComparison.Ordinal));
    }

    // 連結文字要有意義：按鈕寫動詞，不是網址或「點這裡」。
    [Theory]
    [InlineData(true, "設定密碼並啟用帳號")]
    [InlineData(false, "驗證信箱")]
    public void TheOnlyAnchorTextIsTheVerbOfTheButton(bool invitation, string label)
    {
        var html = Activation(invitation, tenant: null).Html;

        var texts = Anchor().Matches(html).Select(m => m.Groups["text"].Value).ToList();

        Assert.Equal([label], texts);
    }

    // 插入的值（系統名稱、租戶、連結）都編碼：含 < " & ' 的值不能長出標籤或掙脫屬性。
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void EveryInsertedValueIsHtmlEncoded(bool invitation)
    {
        const string HostileBrand = "A<script>&\"'x";
        const string HostileTenant = "t\"><img src=x onerror=alert(1)>";
        var link = new Uri("https://platform.test/x?userId=a&code=b'c<d\">");

        var mail = Activation(invitation, HostileTenant, HostileBrand, link);

        var encodedBrand = WebUtility.HtmlEncode(HostileBrand);
        var encodedTenant = WebUtility.HtmlEncode(HostileTenant);
        Assert.Equal("A&lt;script&gt;&amp;&quot;&#39;x", encodedBrand);
        Assert.Contains(encodedBrand, mail.Html, StringComparison.Ordinal);
        Assert.Contains(encodedTenant, mail.Html, StringComparison.Ordinal);
        Assert.Contains($"href=\"{WebUtility.HtmlEncode(link.AbsoluteUri)}\"", mail.Html, StringComparison.Ordinal);

        // 拿掉編碼過的值之後，不剩任何原始的危險片段；標籤只有版型自己的那些。
        var rest = mail.Html.Replace(encodedBrand, string.Empty, StringComparison.Ordinal)
            .Replace(encodedTenant, string.Empty, StringComparison.Ordinal)
            .Replace(WebUtility.HtmlEncode(link.AbsoluteUri), string.Empty, StringComparison.Ordinal);
        Assert.DoesNotContain("<script", rest, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("<img", rest, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("onerror", rest, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(HostileBrand, mail.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(HostileTenant, mail.Html, StringComparison.Ordinal);

        // 純文字版不是 HTML，原樣呈現。
        Assert.Contains($"租戶識別碼：{HostileTenant}", mail.Text, StringComparison.Ordinal);
        Assert.Equal(invitation, mail.Text.Contains(HostileBrand, StringComparison.Ordinal));
    }

    [Fact]
    public void TheTestMailEncodesTheNameToo()
    {
        const string HostileBrand = "A<script>&\"'x";

        var mail = MailTemplate.Test(HostileBrand);

        Assert.Contains("A&lt;script&gt;&amp;&quot;&#39;x", mail.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("<script", mail.Html, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(HostileBrand, mail.Html, StringComparison.Ordinal);
    }

    // mail test：沒有連結、沒有密碼，措辭維持原樣；HTML 版套同一個版型但沒有按鈕、沒有任何網址。
    [Fact]
    public void TheTestMailHasNoLinkNoButtonAndNoPassword()
    {
        var mail = MailTemplate.Test(Brand);
        var html = WebUtility.HtmlDecode(mail.Html);

        Assert.Equal($"這是一封測試信，用來確認「{Brand}」的寄信設定與中繼可用。\n沒有連結，也沒有密碼；收到後不需要做任何事。", mail.Text);
        Assert.Contains($"這是一封測試信，用來確認「{Brand}」的寄信設定與中繼可用。", html, StringComparison.Ordinal);
        Assert.Contains("沒有連結，也沒有密碼；收到後不需要做任何事。", html, StringComparison.Ordinal);
        Assert.Contains("寄信測試", html, StringComparison.Ordinal);
        Assert.DoesNotMatch(Remote(), mail.Html);
        Assert.DoesNotContain("<a ", mail.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("v:roundrect", mail.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("http", mail.Text, StringComparison.OrdinalIgnoreCase);
    }

    // 兩種信共用同一個版型：標題字樣、頁尾、語言、表格版面、預覽文字。
    [Fact]
    public void AllMailsShareTheLayout()
    {
        MailContent[] mails = [Activation(true, "acme"), Activation(false, null), MailTemplate.Test(Brand)];

        foreach (var mail in mails)
        {
            Assert.StartsWith("<!DOCTYPE html>", mail.Html, StringComparison.Ordinal);
            Assert.Contains("<html lang=\"zh-Hant\"", mail.Html, StringComparison.Ordinal);
            Assert.Contains("<meta charset=\"utf-8\">", mail.Html, StringComparison.Ordinal);
            Assert.Contains($"這是系統自動寄出的信 · {Brand}", mail.Html, StringComparison.Ordinal);
            Assert.Contains("'PingFang TC','Microsoft JhengHei','Noto Sans TC',system-ui,sans-serif", mail.Html, StringComparison.Ordinal);
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
    public void ThePreheaderIsTheLifetimeSentenceAndShortEnoughForAnInboxPreview()
    {
        var html = Activation(invitation: true, tenant: "acme", lifetime: TimeSpan.FromHours(12)).Html;

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
    public void TheButtonIsABridgeButtonWithAVmlFallbackForOutlook()
    {
        var html = Activation(invitation: true, tenant: null).Html;

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
    public void TheDarkPaletteIsAppliedThroughPrefersColorScheme()
    {
        var html = Activation(invitation: true, tenant: null).Html;

        Assert.Contains("@media (prefers-color-scheme: dark)", html, StringComparison.Ordinal);
        Assert.Contains("<meta name=\"color-scheme\" content=\"light dark\">", html, StringComparison.Ordinal);
        Assert.Contains(":root { color-scheme: light dark;", html, StringComparison.Ordinal);
        var darkCss = html[html.IndexOf("@media (prefers-color-scheme: dark)", StringComparison.Ordinal)..];
        foreach (var color in new[] { "#16191d", "#1f242a", "#e6e9ec", "#a3adb8", "#343b43", "#85b7eb", "#042c53", "#2a3038" })
        {
            Assert.Contains(color, darkCss, StringComparison.Ordinal);
        }

        // 不用純黑純白：深色的每個顏色，與淺色的頁面、文字（淺色的卡片與按鈕字是白色）。
        string[] pure = ["#000000", "#ffffff"];
        var dark = MailTemplate.Dark;
        Assert.All(new[] { dark.Page, dark.Card, dark.Text, dark.Muted, dark.Line, dark.Soft, dark.Brand, dark.Button, dark.ButtonText }, c => Assert.DoesNotContain(c, pure));
        Assert.All(new[] { MailTemplate.Light.Page, MailTemplate.Light.Text }, c => Assert.DoesNotContain(c, pure));
    }

    // 文字對比度至少 4.5:1（WCAG AA），淺色與深色兩組都要過。
    [Theory]
    [MemberData(nameof(PalettePairs))]
    public void EveryTextColorMeetsAaContrastOnItsBackground(string name, string foreground, string background)
    {
        var ratio = Contrast(foreground, background);

        Assert.True(ratio >= 4.5, $"{name}：{foreground} 在 {background} 上的對比度 {ratio:F2} 低於 4.5");
    }

    public static TheoryData<string, string, string> PalettePairs()
    {
        var data = new TheoryData<string, string, string>();
        foreach (var (name, p) in new[] { ("light", MailTemplate.Light), ("dark", MailTemplate.Dark) })
        {
            data.Add($"{name} 內文", p.Text, p.Card);
            data.Add($"{name} 次要文字", p.Muted, p.Card);
            data.Add($"{name} 頁尾", p.Muted, p.Page);
            data.Add($"{name} 淺底上的次要文字", p.Muted, p.Soft);
            data.Add($"{name} 淺底上的內文", p.Text, p.Soft);
            data.Add($"{name} 品牌色", p.Brand, p.Card);
            data.Add($"{name} 按鈕", p.ButtonText, p.Button);
        }

        return data;
    }

    [Theory]
    [InlineData(3 * 24, "3 天")]
    [InlineData(24, "1 天")]
    [InlineData(23, "23 小時")]
    [InlineData(12, "12 小時")]
    [InlineData(1, "1 小時")]
    public void TheLifetimeReadsInDaysOrHours(int hours, string expected) =>
        Assert.Equal(expected, MailTemplate.Lifetime(TimeSpan.FromHours(hours)));

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