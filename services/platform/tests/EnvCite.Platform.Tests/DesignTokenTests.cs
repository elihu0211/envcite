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
            "--ep-focus", "--ep-scrim", "--ep-press-scale", "--ep-touch-target", "--ep-micro-size",
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
        Assert.Contains("--ep-font:system-ui", css, StringComparison.Ordinal);
        Assert.Contains("\"PingFang TC\"", css, StringComparison.Ordinal);
    }

    // 使用者偏好集中在 token 這一層：較高對比時髮絲線與次要文字加深，減少動態時按下不縮放。
    [Fact]
    public static void UserPreferencesAreOverriddenInTheTokensNotPerComponent()
    {
        var css = TokenCss.Render();
        var c = DesignTokens.Light;

        Assert.Contains($"@media (prefers-contrast:more){{:root{{--ep-border-subtle:{c.BorderControl};--ep-text-secondary:{c.Text};}}}}", css, StringComparison.Ordinal);
        Assert.Contains("@media (prefers-reduced-motion:reduce){:root{--ep-press-scale:1;}}", css, StringComparison.Ordinal);
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
        static double Em(DesignTokens.TypeStyle s) =>
            double.Parse(s.LetterSpacing.Replace("em", string.Empty, StringComparison.Ordinal), CultureInfo.InvariantCulture);

        Assert.True(Em(DesignTokens.Display) < Em(DesignTokens.H1));
        Assert.True(Em(DesignTokens.H1) < Em(DesignTokens.H3));
        Assert.True(Em(DesignTokens.Body) <= Em(DesignTokens.BodySmall));
        Assert.True(Em(DesignTokens.BodySmall) < Em(DesignTokens.Caption));
        Assert.True(Em(DesignTokens.Caption) < Em(DesignTokens.Micro));
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
        Assert.Equal(1, Regex.Count(css, "--ep-h3-leading"));
        Assert.Contains("var(--ep-space-6)", css, StringComparison.Ordinal);
        Assert.Contains("var(--ep-space-12)", css, StringComparison.Ordinal);
        Assert.Equal(1, Regex.Count(css, @"scale\(var\(--ep-press-scale\)\)"));
        Assert.Equal(1, Regex.Count(css, @"var\(--ep-primary\) 10%"));
        Assert.Equal(1, Regex.Count(css, @"var\(--ep-primary\) 88%"));
        Assert.Equal(1, Regex.Count(css, "--ep-caption-size"));
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
        Assert.Contains("clamp(", DesignTokens.Display.Size, StringComparison.Ordinal);
        Assert.Contains("clamp(", DesignTokens.H1.Size, StringComparison.Ordinal);
        Assert.Contains("clamp(", TokenCss.Render(), StringComparison.Ordinal);
    }

    private static string StylesheetPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "wwwroot", "design", "platform.css"));

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