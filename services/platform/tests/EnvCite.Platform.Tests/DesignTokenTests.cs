using System.Globalization;
using System.Net;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

using EnvCite.Platform.Web.Design;

namespace EnvCite.Platform.Tests;

/// <summary>設計 token 的規則：對比度（WCAG AA）、單一來源、輸出完整。純函式，不需要資料庫。</summary>
public static partial class DesignTokenTests
{
    private static double Luminance(string hex)
    {
        static double Channel(int value)
        {
            var c = value / 255.0;
            return c <= 0.03928 ? c / 12.92 : Math.Pow((c + 0.055) / 1.055, 2.4);
        }

        var h = hex.TrimStart('#');
        return (0.2126 * Channel(int.Parse(h[..2], NumberStyles.HexNumber, CultureInfo.InvariantCulture)))
            + (0.7152 * Channel(int.Parse(h[2..4], NumberStyles.HexNumber, CultureInfo.InvariantCulture)))
            + (0.0722 * Channel(int.Parse(h[4..6], NumberStyles.HexNumber, CultureInfo.InvariantCulture)));
    }

    /// <summary>CSS 的 <c>color-mix(in srgb, color 10%, base)</c>：一成的前景疊在底色上。</summary>
    private static string Tint(string foreground, string background)
    {
        static int Channel(string hex, int index) => int.Parse(hex.AsSpan(1 + (index * 2), 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture);

        var mixed = Enumerable.Range(0, 3).Select(i => (int)Math.Round((Channel(foreground, i) * 0.1) + (Channel(background, i) * 0.9)));
        return "#" + string.Concat(mixed.Select(v => v.ToString("X2", CultureInfo.InvariantCulture)));
    }

    private static double Contrast(string a, string b)
    {
        var (hi, lo) = (Math.Max(Luminance(a), Luminance(b)), Math.Min(Luminance(a), Luminance(b)));
        return (hi + 0.05) / (lo + 0.05);
    }

    public static TheoryData<string, string, string, double> TextPairs()
    {
        var data = new TheoryData<string, string, string, double>();
        foreach (var (name, c) in new[] { ("light", DesignTokens.Light) })
        {
            // 一般文字 4.5:1。
            data.Add(name, "text on background", $"{c.Text}|{c.Background}", 4.5);
            data.Add(name, "text on surface", $"{c.Text}|{c.Surface}", 4.5);
            data.Add(name, "secondary text on background", $"{c.TextSecondary}|{c.Background}", 4.5);
            data.Add(name, "secondary text on surface", $"{c.TextSecondary}|{c.Surface}", 4.5);
            data.Add(name, "text on muted surface", $"{c.Text}|{c.SurfaceMuted}", 4.5);
            data.Add(name, "secondary text on muted surface", $"{c.TextSecondary}|{c.SurfaceMuted}", 4.5);
            // 徽章與提示框：狀態色的字壓在自己一成的淡底上；選取的導覽項是主色壓在主色一成的淡底上。
            data.Add(name, "success on its tint", $"{c.Success}|{Tint(c.Success, c.Surface)}", 4.5);
            data.Add(name, "warning on its tint", $"{c.Warning}|{Tint(c.Warning, c.Surface)}", 4.5);
            data.Add(name, "error on its tint", $"{c.Error}|{Tint(c.Error, c.Surface)}", 4.5);
            data.Add(name, "info on its tint", $"{c.Info}|{Tint(c.Info, c.Surface)}", 4.5);
            data.Add(name, "primary on its tint over background", $"{c.Primary}|{Tint(c.Primary, c.Background)}", 4.5);
            data.Add(name, "on-primary on error (destructive button)", $"{c.OnPrimary}|{c.Error}", 4.5);
            data.Add(name, "control border on muted surface", $"{c.BorderControl}|{c.SurfaceMuted}", 3.0);
            data.Add(name, "control border on background", $"{c.BorderControl}|{c.Background}", 3.0);
            data.Add(name, "on-primary on primary", $"{c.OnPrimary}|{c.Primary}", 4.5);
            data.Add(name, "on-ink on ink", $"{c.OnInk}|{c.Ink}", 4.5);
            data.Add(name, "link on surface", $"{c.Link}|{c.Surface}", 4.5);
            data.Add(name, "link on background", $"{c.Link}|{c.Background}", 4.5);
            data.Add(name, "success on surface", $"{c.Success}|{c.Surface}", 4.5);
            data.Add(name, "warning on surface", $"{c.Warning}|{c.Surface}", 4.5);
            data.Add(name, "error on surface", $"{c.Error}|{c.Surface}", 4.5);
            data.Add(name, "info on surface", $"{c.Info}|{c.Surface}", 4.5);
            // 控制項外框與焦點環是非文字元件，3:1。
            data.Add(name, "control border on surface", $"{c.BorderControl}|{c.Surface}", 3.0);
            data.Add(name, "focus ring on surface", $"{c.Focus}|{c.Surface}", 3.0);
            data.Add(name, "primary button against surface", $"{c.Primary}|{c.Surface}", 3.0);
        }

        return data;
    }

    [Theory]
    [MemberData(nameof(TextPairs))]
    public static void ColorPairsMeetWcagAa(string scheme, string pair, string colors, double minimum)
    {
        var parts = colors.Split('|');

        var ratio = Contrast(parts[0], parts[1]);

        Assert.True(ratio >= minimum, $"{scheme}: {pair} is {ratio:F2}:1, needs {minimum}:1");
    }

    [Fact]
    public static void TheCssDefinesEveryColorTokenForBothSchemes()
    {
        var css = TokenCss.Render();

        var baseBlock = css[..css.IndexOf("@media", StringComparison.Ordinal)];
        foreach (var variable in new[]
        {
            "--ep-bg", "--ep-surface", "--ep-surface-muted", "--ep-text", "--ep-text-secondary", "--ep-primary", "--ep-on-primary",
            "--ep-link", "--ep-success", "--ep-warning", "--ep-error", "--ep-info", "--ep-border-subtle", "--ep-border-control",
            "--ep-focus", "--ep-scrim", "--ep-press-scale", "--ep-touch-target", "--ep-label-s-size",
            "--ep-motion-fast", "--ep-motion-normal", "--ep-motion-slow", "--ep-motion-reveal",
            "--ep-motion-ease-standard", "--ep-motion-ease-enter", "--ep-motion-ease-exit", "--ep-motion-ease-move",
            "--ep-radius-pill",
        })
        {
            // 只有淺色：每個變數在 :root 只定義一次（使用者偏好的覆寫另在 @media 內，見下一個測試）。
            Assert.Equal(1, Regex.Count(baseBlock, Regex.Escape(variable + ":")));
        }

        // 產品決定只做淺色：不得出現任何深色的規則，系統設成深色時頁面也維持淺色。
        Assert.DoesNotContain("prefers-color-scheme", css, StringComparison.Ordinal);
        Assert.DoesNotContain("data-theme", css, StringComparison.Ordinal);
        Assert.Contains("color-scheme:light", css, StringComparison.Ordinal);
        Assert.Contains("--ep-font:\"Inter Variable\",\"Noto Sans TC Variable\",sans-serif;", css, StringComparison.Ordinal);
    }

    // 使用者偏好集中在 token 這一層：較高對比時髮絲線與次要文字加深，減少動態時位移與縮放歸零。
    [Fact]
    public static void UserPreferencesAreOverriddenInTheTokensNotPerComponent()
    {
        var css = TokenCss.Render();
        var c = DesignTokens.Light;

        Assert.Contains($"@media (prefers-contrast:more){{:root{{--ep-border-subtle:{c.BorderControl};--ep-text-secondary:{c.Text};}}}}", css, StringComparison.Ordinal);
        Assert.Contains("@media (prefers-reduced-motion:reduce){:root{--ep-press-scale:1;--ep-motion-scale:1;--ep-motion-travel:0rem;--ep-motion-travel-sm:0rem;}}", css, StringComparison.Ordinal);
        // 加深之後的次要文字與髮絲線也要過關。
        Assert.True(Contrast(c.Text, c.Surface) >= 4.5);
        Assert.True(Contrast(c.BorderControl, c.Surface) >= 3.0);
    }

    // 兩個斷點是唯一的視窗寬度分界：平板 48rem、桌機 80rem，rem 單位，由小到大。
    [Fact]
    public static void TheBreakpointsAreRemAndOrdered()
    {
        static double Rem(string value) => double.Parse(value.Replace("rem", string.Empty, StringComparison.Ordinal), CultureInfo.InvariantCulture);

        Assert.EndsWith("rem", DesignTokens.Breakpoints.Tablet, StringComparison.Ordinal);
        Assert.EndsWith("rem", DesignTokens.Breakpoints.Desktop, StringComparison.Ordinal);
        Assert.Equal(768, Rem(DesignTokens.Breakpoints.Tablet) * 16);
        Assert.Equal(1280, Rem(DesignTokens.Breakpoints.Desktop) * 16);
    }

    // 樣式表裡視窗寬度的媒體查詢只准用這兩個斷點；元件各自的容器查詢（依容器寬度）不受限。
    [Fact]
    public static void ViewportQueriesUseOnlyTheTwoBreakpoints()
    {
        var css = File.ReadAllText(StylesheetPath());
        var allowed = new[] { DesignTokens.Breakpoints.Tablet, DesignTokens.Breakpoints.Desktop };

        var widths = ViewportWidthQuery().Matches(css).Select(m => m.Groups[1].Value).ToList();

        Assert.NotEmpty(widths); // 至少有一條，守門才不是空轉
        Assert.All(widths, width => Assert.Contains(width, allowed));
    }

    // 樣式表不寫死毫秒、緩動曲線與縮放量：動態都從 token 取。
    [Fact]
    public static void TheStylesheetTakesMotionFromTheTokens()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.DoesNotMatch(LiteralDuration(), css.Replace("0.01ms", string.Empty, StringComparison.Ordinal));
        Assert.DoesNotContain("cubic-bezier", css, StringComparison.Ordinal);
        Assert.DoesNotMatch(LiteralScale(), css);
        Assert.DoesNotContain("2.75rem", css, StringComparison.Ordinal); // 觸控目標走 --ep-touch-target
    }

    [Fact]
    public static void LargeTextTightensTrackingAndSmallTextLoosensIt()
    {
        static double Em(string role) =>
            double.Parse(DesignTokens.Type[role].LetterSpacing.Replace("em", string.Empty, StringComparison.Ordinal), CultureInfo.InvariantCulture);

        Assert.True(Em("display-l") < Em("headline-l"));
        Assert.True(Em("headline-l") < Em("headline-s"));
        Assert.True(Em("headline-s") < Em("title-l"));
        Assert.True(Em("body-l") <= Em("body-m"));
        Assert.True(Em("body-m") < Em("body-s"));
        Assert.True(Em("body-s") < Em("label-s"));
    }

    // 字級表是 Material 3 的十五個角色，一個不多、一個不少。
    [Fact]
    public static void TheTypeScaleIsTheFifteenMaterialRoles()
    {
        var roles = TypeGroups.SelectMany(group => TypeSizes.Select(size => $"{group}-{size}"));

        Assert.Equal(roles.Order(), DesignTokens.Type.Keys.Order());
        foreach (var role in roles)
        {
            Assert.Contains($"--ep-{role}-size:", TokenCss.Render(), StringComparison.Ordinal);
        }
    }

    // 中文長時間閱讀：沒有小於 12px 的字，內文至少 15px、行高 1.6 以上。clamp 取下限。
    [Fact]
    public static void NoTypeIsTooSmallToReadAndBodyTextBreathes()
    {
        static double Rem(string size) => double.Parse(
            (size.StartsWith("clamp(", StringComparison.Ordinal) ? size[6..size.IndexOf(',', StringComparison.Ordinal)] : size)
                .Replace("rem", string.Empty, StringComparison.Ordinal),
            CultureInfo.InvariantCulture);

        Assert.All(DesignTokens.Type.Values, style => Assert.True(Rem(style.Size) * 16 >= 12, style.Size));
        foreach (var role in new[] { "body-l", "body-m" })
        {
            Assert.True(Rem(DesignTokens.Type[role].Size) * 16 >= 15, role);
        }

        foreach (var role in new[] { "body-l", "body-m", "body-s" })
        {
            Assert.True(double.Parse(DesignTokens.Type[role].LineHeight, CultureInfo.InvariantCulture) >= 1.6, role);
        }
    }

    // 字型自己託管：token 的字型名稱都在 fonts.css 宣告過、每個字型檔都在、沒有外部網址；樣式表只用變數，不寫字型名稱。
    [Fact]
    public static void TheFontsAreSelfHostedAndNamedOnlyInTheTokens()
    {
        var fonts = File.ReadAllText(FontsPath());
        var css = File.ReadAllText(StylesheetPath());

        foreach (var family in DesignTokens.FontFamily.Where(f => f != "sans-serif"))
        {
            Assert.Contains($"font-family: '{family}';", fonts, StringComparison.Ordinal);
            Assert.DoesNotContain(family, css, StringComparison.Ordinal);
        }

        Assert.DoesNotContain("http", fonts, StringComparison.OrdinalIgnoreCase);
        foreach (Match url in FontUrl().Matches(fonts))
        {
            Assert.True(File.Exists(Path.Combine(Path.GetDirectoryName(FontsPath())!, url.Groups[1].Value)), url.Value);
        }

        Assert.All(FontFamilyDeclaration().Matches(css), m => Assert.StartsWith("var(--ep-font", m.Groups[1].Value, StringComparison.Ordinal));
    }

    // Inter 排在中文字型前面，所以它要讓出中文排版的標點：引號、破折號、刪節號、間隔號落到 Noto Sans TC，排成全形。
    [Theory]
    [InlineData(0x00B7)]
    [InlineData(0x2014)]
    [InlineData(0x2018)]
    [InlineData(0x201C)]
    [InlineData(0x201D)]
    [InlineData(0x2026)]
    public static void InterLeavesChinesePunctuationToTheChineseFont(int codePoint)
    {
        var fonts = File.ReadAllText(FontsPath());
        var interRanges = Regex.Matches(fonts, @"font-family: 'Inter Variable';[^}]*unicode-range: ([^;]+);")
            .SelectMany(m => m.Groups[1].Value.Split(','))
            .Select(range => range.Trim()[2..].Split('-'))
            .Select(bounds => (Low: Convert.ToInt32(bounds[0], 16), High: Convert.ToInt32(bounds[^1], 16)))
            .ToList();

        Assert.NotEmpty(interRanges);
        Assert.DoesNotContain(interRanges, r => r.Low <= codePoint && codePoint <= r.High);
    }

    [Fact]
    public static void TheThemeIsBuiltFromTheTokens()
    {
        var theme = PlatformTheme.Create();

        // MudBlazor 的色碼輸出可能小寫、可能帶透明度；只比 RGB。
        Assert.Equal(
            DesignTokens.Light.Primary,
            theme.PaletteLight.Primary.ToString(MudBlazor.Utilities.MudColorOutputFormats.Hex)[..7],
            ignoreCase: true);
        Assert.Equal("none", theme.Typography.Button.TextTransform);
    }

    // 企業色只寫一次：主色與焦點環是同一個 Brand，連結是文字級的 BrandText。
    [Fact]
    public static void TheEnterpriseColorIsWrittenOnce()
    {
        var c = DesignTokens.Light;

        Assert.Equal(DesignTokens.Brand, c.Primary);
        Assert.Equal(DesignTokens.Brand, c.Focus);
        Assert.Equal(DesignTokens.BrandText, c.Link);
        Assert.NotEqual(c.Primary, c.Link);
    }

    // 字重與置中欄寬都從 token 取。字重沿用既有字級（標題、按鈕），不另開一組字重表。
    [Fact]
    public static void TheStylesheetTakesWeightAndMeasureFromTheTokens()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.DoesNotMatch(LiteralFontWeight(), css);
        // 半透明材質與小標字級各只寫一次。圖示與色票的尺寸走既有間距階。
        Assert.Equal(1, Regex.Count(css, @"(?<!-)backdrop-filter:\s*blur\("));
        Assert.Equal(1, Regex.Count(css, @"(?<!-)backdrop-filter:\s*none"));
        Assert.Equal(1, Regex.Count(css, "--ep-headline-s-leading"));
        Assert.Contains("var(--ep-space-6)", css, StringComparison.Ordinal);
        Assert.Contains("var(--ep-space-12)", css, StringComparison.Ordinal);
        Assert.Equal(1, Regex.Count(css, @"scale\(var\(--ep-press-scale\)\)"));
        Assert.Equal(1, Regex.Count(css, @"var\(--ep-primary\) 10%"));
        Assert.Equal(1, Regex.Count(css, @"var\(--ep-primary\) 88%"));
        Assert.Equal(1, Regex.Count(css, "--ep-body-s-size"));
        Assert.Equal(1, Regex.Count(css, @"outline: 2px solid var\(--ep-focus\)"));
        foreach (var (key, value) in DesignTokens.Measure)
        {
            Assert.Contains($"var(--ep-measure-{key})", css, StringComparison.Ordinal);
            Assert.DoesNotContain(value, css, StringComparison.Ordinal);
            Assert.Contains($"--ep-measure-{key}:{value}", TokenCss.Render(), StringComparison.Ordinal);
        }
    }

    // token 只有 DesignTokens.cs 一個來源：靜態樣式表不得自己寫色碼。
    [Fact]
    public static void TheStylesheetHardCodesNoColors()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.DoesNotMatch(HexColor(), css);
        Assert.DoesNotMatch(RgbColor(), css);
        // 表面全是平面：樣式表與 token 都沒有漸層。
        Assert.DoesNotContain("gradient(", css, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("gradient", TokenCss.Render(), StringComparison.OrdinalIgnoreCase);
        Assert.Contains("prefers-reduced-motion", css, StringComparison.Ordinal);
        Assert.Contains("prefers-reduced-transparency", css, StringComparison.Ordinal);
        Assert.Contains("prefers-contrast", css, StringComparison.Ordinal);
    }

    // 響應式用原生 CSS：容器查詢、觸控目標、hover 只給能 hover 的裝置、安全區；斷點用 rem，不用 px。
    [Fact]
    public static void TheStylesheetIsResponsiveWithNativeCss()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Contains("container: ep-table / inline-size", css, StringComparison.Ordinal);
        Assert.Contains("@container ep-table", css, StringComparison.Ordinal);
        Assert.Contains("@media (pointer: coarse)", css, StringComparison.Ordinal);
        Assert.Contains("@media (hover: hover)", css, StringComparison.Ordinal);
        Assert.Contains("env(safe-area-inset-left)", css, StringComparison.Ordinal);
        Assert.Contains("var(--ep-touch-target)", css, StringComparison.Ordinal); // 觸控目標至少 44px
        Assert.Equal("2.75rem", DesignTokens.TouchTarget);
        Assert.DoesNotMatch(PixelBreakpoint(), css);
        // 表格卡片不能是 overflow: hidden：會把寬表格的右半邊裁掉，看不到也捲不到。
        Assert.DoesNotMatch(HiddenOverflowOnCard(), css);
    }

    [Fact]
    public static void TheLargestTypeSizesAreFluid()
    {
        Assert.Contains("clamp(", DesignTokens.Type["display-l"].Size, StringComparison.Ordinal);
        Assert.Contains("clamp(", DesignTokens.Type["headline-l"].Size, StringComparison.Ordinal);
        Assert.Contains("clamp(", TokenCss.Render(), StringComparison.Ordinal);
    }

    // 檔案版品牌圖示（favicon、app icon）是靜態檔，引用不到 CSS 變數；用測試守住它的藍色就是 Brand，
    // 否則改了品牌色，分頁圖示會悄悄不一致。
    [Theory]
    [InlineData("icon.svg")]
    [InlineData("icon-small.svg")]
    public static void BrandIconFilesUseTheBrandColor(string file)
    {
        var path = Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(StylesheetPath())!, "..", "brand", file));
        var colors = HexColor().Matches(File.ReadAllText(path)).Select(m => m.Value.ToUpperInvariant()).ToHashSet();

        Assert.Contains(DesignTokens.Brand.ToUpperInvariant(), colors);
    }

    private static string StylesheetPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "wwwroot", "design", "platform.css"));

    private static readonly string[] TypeGroups = ["display", "headline", "title", "body", "label"];

    private static readonly string[] TypeSizes = ["l", "m", "s"];

    private static string FontsPath() =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "..", "fonts", "fonts.css"));

    [GeneratedRegex(@"url\(([^)]+)\)")]
    private static partial Regex FontUrl();

    [GeneratedRegex(@"font-family:\s*([^;]+);")]
    private static partial Regex FontFamilyDeclaration();

    [GeneratedRegex(@"#[0-9a-fA-F]{3,8}\b")]
    private static partial Regex HexColor();

    [GeneratedRegex(@"\brgba?\(")]
    private static partial Regex RgbColor();

    // 視窗寬度的條件，兩種寫法：(min-width: 48rem) 與範圍語法 (width < 48rem)、(width >= 48rem)。
    [GeneratedRegex(@"@media[^{]*?(?:width\s*[<>]=?|(?:min|max)-width\s*:)\s*([\d.]+rem)")]
    private static partial Regex ViewportWidthQuery();

    [GeneratedRegex(@"font-weight:\s*\d")]
    private static partial Regex LiteralFontWeight();

    [GeneratedRegex(@"\b\d+(?:\.\d+)?ms\b")]
    private static partial Regex LiteralDuration();

    [GeneratedRegex(@"scale\(\s*[\d.]+\s*\)")]
    private static partial Regex LiteralScale();

    [GeneratedRegex(@"@(media|container)[^{]*\d+px")]
    private static partial Regex PixelBreakpoint();

    [GeneratedRegex(@"\.ep-table-card\s*\{[^}]*overflow:\s*hidden")]
    private static partial Regex HiddenOverflowOnCard();
}

[Collection(PlatformTestGroup.Name)]
public sealed class DesignPageTests(PlatformFactory factory)
{
    [Fact]
    public async Task AnAnonymousPageCarriesTheTokensAndNoThirdPartyRequests()
    {
        using var client = factory.CreateClient(new Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var response = await client.GetAsync(new Uri("/ui/denied", UriKind.Relative), TestContext.Current.CancellationToken);
        var html = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("--ep-primary:", html, StringComparison.Ordinal);
        Assert.Contains("design/platform", html, StringComparison.Ordinal);
        Assert.Contains("fonts/fonts", html, StringComparison.Ordinal); // 自己託管的字型
        Assert.Contains("lang=\"zh-TW\"", html, StringComparison.Ordinal);
        // 手機：延伸到瀏海下（再用安全區內縮）、不禁用縮放、瀏覽器工具列顏色跟著主題（顏色來自 token）。
        Assert.Contains("viewport-fit=cover", html, StringComparison.Ordinal);
        Assert.DoesNotContain("user-scalable", html, StringComparison.Ordinal);
        Assert.DoesNotContain("maximum-scale", html, StringComparison.Ordinal);
        Assert.Contains($"<meta name=\"theme-color\" content=\"{DesignTokens.Light.Background}\"", html, StringComparison.Ordinal);
        Assert.Contains("<meta name=\"color-scheme\" content=\"light\"", html, StringComparison.Ordinal); // 只有淺色
        Assert.DoesNotContain("prefers-color-scheme", html, StringComparison.Ordinal);
        Assert.DoesNotContain("fonts.googleapis.com", html, StringComparison.Ordinal);
        Assert.Contains("<h1", html, StringComparison.Ordinal);
    }

    // 頁面只有 HTML 沒有互動連線也看起來正常，所以只測 HTML 抓不到「框架腳本 404」：
    // 主機專案自己沒有 .razor 檔時，SDK 不會附上 blazor.web.js（見 Api.csproj 的 RequiresAspNetWebAssets）。
    // 這裡真的去抓頁面引用的腳本。
    [Fact]
    public async Task TheScriptsThePageReferencesAreServed()
    {
        using var client = factory.CreateClient(new Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        var html = await client.GetStringAsync(new Uri("/ui/denied", UriKind.Relative), TestContext.Current.CancellationToken);

        var sources = Regex.Matches(html, "<script src=\"([^\"]+)\"")
            .Select(m => m.Groups[1].Value)
            .ToList();

        Assert.Contains(sources, s => s.Contains("blazor.web", StringComparison.Ordinal));
        foreach (var source in sources)
        {
            using var response = await client.GetAsync(new Uri(source, UriKind.RelativeOrAbsolute), TestContext.Current.CancellationToken);
            Assert.True(response.StatusCode == HttpStatusCode.OK, $"{source} -> {(int)response.StatusCode}");
        }
    }
}