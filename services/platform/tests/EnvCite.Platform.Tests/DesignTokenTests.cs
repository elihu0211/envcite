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
        foreach (var (name, c) in new[] { ("light", DesignTokens.Light), ("dark", DesignTokens.Dark) })
        {
            // 一般文字 4.5:1。
            data.Add(name, "text on background", $"{c.Text}|{c.Background}", 4.5);
            data.Add(name, "text on surface", $"{c.Text}|{c.Surface}", 4.5);
            data.Add(name, "secondary text on background", $"{c.TextSecondary}|{c.Background}", 4.5);
            data.Add(name, "secondary text on surface", $"{c.TextSecondary}|{c.Surface}", 4.5);
            data.Add(name, "text on muted surface", $"{c.Text}|{c.SurfaceMuted}", 4.5);
            data.Add(name, "secondary text on muted surface", $"{c.TextSecondary}|{c.SurfaceMuted}", 4.5);
            // 浮起的表面（對話框、選單）：深色靠更亮的底分出層次，字與外框在上面也要過關。
            data.Add(name, "text on raised surface", $"{c.Text}|{c.SurfaceRaised}", 4.5);
            data.Add(name, "secondary text on raised surface", $"{c.TextSecondary}|{c.SurfaceRaised}", 4.5);
            data.Add(name, "control border on raised surface", $"{c.BorderControl}|{c.SurfaceRaised}", 3.0);
            data.Add(name, "link on raised surface", $"{c.Link}|{c.SurfaceRaised}", 4.5);
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

    // 深色是同一組變數換一份值：淺色在 :root 定義一次；深色在「使用者沒選亮色、而系統（瀏覽器）是深色」時（prefers-color-scheme）定義一次，
    // 使用者在個人設定手動選了暗色（data-theme="dark"）時再套同一份。預設（<html> 沒有 data-theme）行為只靠 prefers-color-scheme。
    [Fact]
    public static void TheCssDefinesEveryColorTokenForBothSchemes()
    {
        var css = TokenCss.Render();

        var baseBlock = css[..css.IndexOf("@media", StringComparison.Ordinal)];
        var chosen = Block(css, "@media (prefers-color-scheme:dark){html:root:not([data-theme=\"light\"]){");
        var chosenDark = Block(css, "html:root[data-theme=\"dark\"]{");
        Assert.Equal(chosen + "color-scheme:dark;", chosenDark); // 手動選暗色與系統是深色套的是同一份變數，只多鎖住 color-scheme
        Assert.Contains("html:root[data-theme=\"light\"]{color-scheme:light;}", css, StringComparison.Ordinal);
        Assert.DoesNotContain("data-theme=\"system\"", css, StringComparison.Ordinal); // 跟隨系統沒有屬性

        foreach (var variable in new[]
        {
            "--ep-bg", "--ep-surface", "--ep-surface-raised", "--ep-surface-muted", "--ep-text", "--ep-text-secondary", "--ep-primary",
            "--ep-on-primary", "--ep-ink", "--ep-on-ink", "--ep-accent", "--ep-link", "--ep-success", "--ep-warning", "--ep-error", "--ep-info",
            "--ep-border-subtle", "--ep-border-control", "--ep-focus", "--ep-scrim", "--ep-elevation-1",
        })
        {
            Assert.Equal(1, Regex.Count(baseBlock, Regex.Escape(variable + ":")));
            Assert.Equal(1, Regex.Count(chosen, Regex.Escape(variable + ":")));
        }

        // 與配色無關的 token 只在 :root 定義一次，深色不重複。
        foreach (var variable in new[]
        {
            "--ep-press-scale", "--ep-touch-target", "--ep-control-height", "--ep-label-s-size", "--ep-qr-surface",
            "--ep-motion-fast", "--ep-motion-normal", "--ep-motion-slow", "--ep-motion-reveal",
            "--ep-motion-ease-standard", "--ep-motion-ease-enter", "--ep-motion-ease-exit", "--ep-motion-ease-move",
            "--ep-radius-pill",
        })
        {
            Assert.Equal(1, Regex.Count(baseBlock, Regex.Escape(variable + ":")));
            Assert.DoesNotContain(variable + ":", chosen, StringComparison.Ordinal);
        }

        // 深色值逐項對得上調色盤；MudBlazor 的變數也蓋成同一份。
        var dark = DesignTokens.Dark;
        Assert.Contains($"--ep-bg:{dark.Background};", chosen, StringComparison.Ordinal);
        Assert.Contains($"--ep-surface:{dark.Surface};", chosen, StringComparison.Ordinal);
        Assert.Contains($"--ep-text:{dark.Text};", chosen, StringComparison.Ordinal);
        Assert.Contains($"--ep-primary:{dark.Primary};", chosen, StringComparison.Ordinal);
        Assert.Contains($"--mud-palette-surface:{dark.SurfaceRaised};", chosen, StringComparison.Ordinal);
        Assert.Contains($"--mud-palette-text-primary:{dark.Text};", chosen, StringComparison.Ordinal);
        Assert.Contains($"--mud-palette-primary:{dark.Primary};", chosen, StringComparison.Ordinal);
        Assert.DoesNotContain("--mud-palette", baseBlock, StringComparison.Ordinal); // 淺色那組由 MudThemeProvider 輸出

        // 預設，瀏覽器的原生控制項、捲軸跟著系統的配色，不鎖定任何一邊；只有手動選了才鎖到那一邊（上面兩條規則），
        // 系統深色那一塊不碰 color-scheme。
        Assert.Contains("color-scheme:light dark;", baseBlock, StringComparison.Ordinal);
        Assert.DoesNotContain("color-scheme", chosen, StringComparison.Ordinal);
        Assert.Equal(1, Regex.Count(css, "color-scheme:dark;"));
        Assert.Equal(1, Regex.Count(css, "color-scheme:light;"));
        Assert.Contains("--ep-font:\"Roboto Variable\",\"Noto Sans TC Variable\",sans-serif;", css, StringComparison.Ordinal);
        Assert.Contains("--ep-font-mono:\"Roboto Mono Variable\",\"Noto Sans TC Variable\",monospace;", css, StringComparison.Ordinal);
    }

    // 取出以 <paramref name="opening"/> 開頭的規則內文（到對應的右大括號為止）。
    private static string Block(string css, string opening)
    {
        var start = css.IndexOf(opening, StringComparison.Ordinal);
        Assert.True(start >= 0, opening);
        var end = css.IndexOf('}', start + opening.Length);
        return css[(start + opening.Length)..end];
    }

    // 使用者偏好集中在 token 這一層：較高對比時髮絲線與次要文字加深（兩套配色各用自己的一對），減少動態時位移與縮放歸零。
    [Fact]
    public static void UserPreferencesAreOverriddenInTheTokensNotPerComponent()
    {
        var css = TokenCss.Render();
        var light = DesignTokens.Light;
        var dark = DesignTokens.Dark;

        Assert.Contains($"@media (prefers-contrast:more){{:root{{--ep-border-subtle:{light.BorderControl};--ep-text-secondary:{light.Text};}}}}", css, StringComparison.Ordinal);
        // 較高對比：系統深色（且沒選亮色）與手動選暗色，都用深色那一對；手動選亮色時系統的深色不介入。
        Assert.Contains($"@media (prefers-contrast:more) and (prefers-color-scheme:dark){{html:root:not([data-theme=\"light\"]){{--ep-border-subtle:{dark.BorderControl};--ep-text-secondary:{dark.Text};}}}}", css, StringComparison.Ordinal);
        Assert.Contains($"@media (prefers-contrast:more){{html:root[data-theme=\"dark\"]{{--ep-border-subtle:{dark.BorderControl};--ep-text-secondary:{dark.Text};}}}}", css, StringComparison.Ordinal);
        Assert.Contains("@media (prefers-reduced-motion:reduce){:root{--ep-press-scale:1;--ep-motion-scale:1;--ep-motion-travel:0rem;--ep-motion-travel-sm:0rem;}}", css, StringComparison.Ordinal);
        // 加深之後的次要文字與髮絲線也要過關。
        foreach (var c in new[] { light, dark })
        {
            Assert.True(Contrast(c.Text, c.Surface) >= 4.5);
            Assert.True(Contrast(c.BorderControl, c.Surface) >= 3.0);
        }
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

    // 頁面進場的錯開（直接子元素的 animation-delay）不得壓過元件自己的 animation：右側抽屜與背景是主區的直接子元素，
    // 優先權更高的 :is() 會把延遲強加給它們，延遲期間（無 fill）面板停在全開位置，延遲結束才跳出畫面外重新滑入。
    [Fact]
    public static void TheEntranceStaggerNeverOutranksAComponentsOwnAnimation()
    {
        var css = File.ReadAllText(StylesheetPath());

        var selectors = Regex.Matches(css, @"(?m)^([^\n{]*> :nth-child\([^\n{]*)\{[^}]*animation-delay")
            .Select(m => m.Groups[1].Value.Trim())
            .ToList();

        Assert.NotEmpty(selectors); // 至少有一條，守門才不是空轉
        Assert.All(selectors, selector => Assert.StartsWith(":where(", selector, StringComparison.Ordinal));
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
            Assert.Contains($"--ep-{role}-size:", TokenCss.Render(), StringComparison.Ordinal);
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
            Assert.True(Rem(DesignTokens.Type[role].Size) * 16 >= 15, role);

        foreach (var role in new[] { "body-l", "body-m", "body-s" })
            Assert.True(double.Parse(DesignTokens.Type[role].LineHeight, CultureInfo.InvariantCulture) >= 1.6, role);
    }

    // 字型自己託管：token 的字型名稱都在 fonts.css 宣告過、每個字型檔都在、沒有外部網址；樣式表只用變數，不寫字型名稱。
    [Fact]
    public static void TheFontsAreSelfHostedAndNamedOnlyInTheTokens()
    {
        var fonts = File.ReadAllText(FontsPath());
        var css = File.ReadAllText(StylesheetPath());

        foreach (var family in DesignTokens.FontFamily.Concat(DesignTokens.CodeFontFamily).Where(f => f is not ("sans-serif" or "monospace")).Distinct())
        {
            Assert.Contains($"font-family: '{family}';", fonts, StringComparison.Ordinal);
            Assert.DoesNotContain(family, css, StringComparison.Ordinal);
        }

        Assert.DoesNotContain("http", fonts, StringComparison.OrdinalIgnoreCase);
        foreach (Match url in FontUrl().Matches(fonts))
            Assert.True(File.Exists(Path.Combine(Path.GetDirectoryName(FontsPath())!, url.Groups[1].Value)), url.Value);

        // 字型只從變數來：內文是 --ep-font，代碼、金鑰、識別碼是 --ep-font-mono。
        Assert.All(FontFamilyDeclaration().Matches(css), m => Assert.True(
            m.Groups[1].Value.StartsWith("var(--ep-font", StringComparison.Ordinal) || m.Groups[1].Value == "inherit", m.Value));
    }

    // Roboto 排在中文字型前面，所以它要讓出中文排版的標點：引號、破折號、刪節號、間隔號落到 Noto Sans TC，排成全形。
    [Theory]
    [InlineData(0x00B7)]
    [InlineData(0x2014)]
    [InlineData(0x2018)]
    [InlineData(0x201C)]
    [InlineData(0x201D)]
    [InlineData(0x2026)]
    public static void RobotoLeavesChinesePunctuationToTheChineseFont(int codePoint)
    {
        var fonts = File.ReadAllText(FontsPath());
        var robotoRanges = Regex.Matches(fonts, @"font-family: 'Roboto Variable';[^}]*unicode-range: ([^;]+);")
            .SelectMany(m => m.Groups[1].Value.Split(','))
            .Select(range => range.Trim()[2..].Split('-'))
            .Select(bounds => (Low: Convert.ToInt32(bounds[0], 16), High: Convert.ToInt32(bounds[^1], 16)))
            .ToList();

        Assert.NotEmpty(robotoRanges);
        Assert.DoesNotContain(robotoRanges, r => r.Low <= codePoint && codePoint <= r.High);
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

    // 視覺規格與執行期 token 是同一份：企業色、十五階字級、字型堆疊、間距與圓角都寫在 DESIGN.md。
    [Fact]
    public static void TheDesignSpecMatchesTheSingleTokenSource()
    {
        var spec = File.ReadAllText(DesignSpecPath());
        var darkStart = spec.IndexOf("colors-dark:", StringComparison.Ordinal);
        Assert.True(darkStart > 0, "DESIGN.md 要有 colors-dark 區塊");
        var darkSpec = spec[darkStart..spec.IndexOf("```", darkStart, StringComparison.Ordinal)];
        var lightSpec = spec[..darkStart];
        foreach (var (c, block) in new[] { (DesignTokens.Light, lightSpec), (DesignTokens.Dark, darkSpec) })
        {
            foreach (var (key, value) in new Dictionary<string, string>
            {
                ["primary"] = c.Primary,
                ["on-primary"] = c.OnPrimary,
                ["link"] = c.Link,
                ["focus"] = c.Focus,
                ["background"] = c.Background,
                ["surface"] = c.Surface,
                ["surface-raised"] = c.SurfaceRaised,
                ["surface-muted"] = c.SurfaceMuted,
                ["text"] = c.Text,
                ["text-secondary"] = c.TextSecondary,
                ["ink"] = c.Ink,
                ["on-ink"] = c.OnInk,
                ["accent"] = c.Accent,
                ["success"] = c.Success,
                ["warning"] = c.Warning,
                ["error"] = c.Error,
                ["info"] = c.Info,
                ["border-subtle"] = c.BorderSubtle,
                ["border-control"] = c.BorderControl,
                ["scrim"] = c.Scrim,
            })
            {
                Assert.Contains($"  {key}: \"{value}\"", block, StringComparison.Ordinal);
            }
        }

        var family = string.Join(", ", DesignTokens.FontFamily);
        Assert.Equal(DesignTokens.Type.Count, Regex.Count(spec, Regex.Escape($"fontFamily: \"{family}\"")));
        foreach (var (name, style) in DesignTokens.Type)
        {
            var block = TypeBlock(spec, name);
            Assert.Contains($"fontSize: \"{style.Size}\"", block, StringComparison.Ordinal);
            Assert.Contains($"fontWeight: {style.Weight}", block, StringComparison.Ordinal);
            Assert.Contains($"lineHeight: \"{style.LineHeight}\"", block, StringComparison.Ordinal);
            Assert.Contains($"letterSpacing: \"{style.LetterSpacing}\"", block, StringComparison.Ordinal);
        }

        foreach (var (key, value) in DesignTokens.Space)
        {
            Assert.Contains($"  \"{key}\": {value}", spec, StringComparison.Ordinal);
        }

        foreach (var (key, value) in DesignTokens.Radius)
        {
            Assert.Contains($"  {key}: {value}", spec, StringComparison.Ordinal);
        }

        var gallery = File.ReadAllText(GalleryPath());
        foreach (var role in DesignTokens.Type.Keys)
            Assert.Contains($"\"{role}\"", gallery, StringComparison.Ordinal);
    }

    private static string TypeBlock(string spec, string name)
    {
        var marker = $"\n  {name}:\n";
        var start = spec.IndexOf(marker, StringComparison.Ordinal);
        Assert.True(start >= 0, name);
        var rest = spec[(start + marker.Length)..];
        var end = Regex.Match(rest, @"\n  [a-z0-9-]+:\n|\n```").Index;
        Assert.True(end > 0, name);
        return rest[..end];
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
        Assert.Equal("2.25rem", DesignTokens.ControlHeight); // 緊湊高度只給滑鼠，觸控仍是 44px
        Assert.DoesNotMatch(PixelBreakpoint(), css);
        // 表格卡片不能是 overflow: hidden：會把寬表格的右半邊裁掉，看不到也捲不到。
        Assert.DoesNotMatch(HiddenOverflowOnCard(), css);
    }

    // 眼睛鈕與復原碼的複製、列印需要 JavaScript：沒有它們也要是可用的表單，所以樣式表預設收起，
    // 由 forms.js 在 <html> 上設 data-js 才打開（下載是 data: 連結，不需要腳本）。
    [Fact]
    public static void ControlsThatNeedScriptStayHiddenUntilItRuns()
    {
        var css = File.ReadAllText(StylesheetPath());
        var script = File.ReadAllText(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "forms.js"));

        Assert.Matches(@"\.ep-password-toggle,\s*\.ep-js-only\s*\{\s*display:\s*none", css);
        Assert.Contains("html[data-js] .ep-password-toggle", css, StringComparison.Ordinal);
        Assert.Contains("html[data-js] .ep-js-only", css, StringComparison.Ordinal);
        Assert.Contains("documentElement.dataset.js", script, StringComparison.Ordinal);
        Assert.Contains("iframe.ep-print-frame", script, StringComparison.Ordinal); // 列印用的 iframe 每次先清掉上一個
    }

    // 側欄在直欄時（平板起、或有 JavaScript 的手機抽屜）標籤放不下要換行，不是被邊緣切掉：英文標籤比中文長。
    // 橫列（沒有 JavaScript 的手機）維持單行，所以換行只加在直欄的兩個選擇器上。
    [Fact]
    public static void TheSidebarWrapsLongLabelsInsteadOfClippingThem()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Matches(@"html\[data-js\] \.ep-side-link \{[^}]*white-space: normal", css);
        Assert.Matches(@"@media \(width >= 48rem\) \{\s*\.ep-side-link \{[^}]*white-space: normal", css);
        Assert.Matches(@"html\[data-js\] \.ep-side-link \.ep-side-label \{[^}]*min-inline-size: 0", css); // flex 子項預設不肯縮到內容以下
        // 基本規則仍是單行：橫列（沒有 JavaScript 的手機）靠它橫向捲動。
        Assert.Matches(@"\.ep-side-link,\s*\.ep-recent-link \{[^}]*white-space: nowrap", css);
    }

    // 營運後台頂欄左邊的品牌（雲端主控台那一型）：常規字重、次要文字色、放大的圖形，沒有副標；入口（.ep-portal）不跟。
    [Fact]
    public static void TheOperatorBrandIsALightweightProductNameWithoutASubtitle()
    {
        var css = File.ReadAllText(StylesheetPath());
        var brand = CssBlock(css, ".ep-console:not(.ep-portal) .ep-shell-top .ep-brand");

        Assert.Contains("font-weight: var(--ep-body-l-weight)", brand, StringComparison.Ordinal);
        Assert.Contains("color: var(--ep-text-secondary)", brand, StringComparison.Ordinal);
        Assert.Contains("inline-size: 1.5em", CssBlock(css, ".ep-console:not(.ep-portal) .ep-shell-top .ep-brand-mark"), StringComparison.Ordinal);
        Assert.DoesNotContain("ep-console-product", css, StringComparison.Ordinal); // 副標（「營運後台」）已移除
    }

    // 搜尋鈕（雲端主控台那一型）：整條膠囊、沒有邊框、底色比頂欄深一階；手機收成只剩圖示的圓鈕。
    [Fact]
    public static void TheSearchButtonIsABorderlessPillAndShrinksToAnIconOnPhones()
    {
        var css = File.ReadAllText(StylesheetPath());
        var pill = CssBlock(css, ".ep-palette-open");

        Assert.Contains("border-radius: var(--ep-radius-pill)", pill, StringComparison.Ordinal);
        Assert.Contains("border: 0", pill, StringComparison.Ordinal);
        Assert.Contains("flex: 0 1 var(--ep-measure-ask)", pill, StringComparison.Ordinal);
        Assert.Matches(@"@media \(width < 48rem\) \{[^@]*\.ep-palette-open \{[^}]*inline-size: var\(--ep-touch-target\)", css);
    }

    // 數字對齊：表格裡的儲存格與時間一律等寬數字；數字後面的單位（次、tokens）占固定寬度，靠右的數字右緣才在同一條線上。
    [Fact]
    public static void NumbersInTablesAreTabularAndUnitsTakeAFixedSlot()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Matches(@"\.mud-table-cell,\s*\.ep-plain-table td,\s*time \{[^}]*font-variant-numeric: tabular-nums", css);
        Assert.Contains("min-inline-size: var(--ep-space-12)", CssBlock(css, ".ep-num-unit"), StringComparison.Ordinal);
        // 純表格與 Mud 表格各自把儲存格設成靠左，數字欄的靠右要在表格底下再寫一次才壓得過；標題也靠右。
        Assert.Matches(@"\.ep-num,\s*\.ep-plain-table :is\(th, td\)\.ep-num,\s*\.ep-table :is\(th, td\)\.ep-num \{[^}]*text-align: end", css);
    }

    // 可排序的欄位標題：沒有框的按鈕；沒排它時箭頭是淡的，排它時（aria-sort）變成主要文字色。
    [Fact]
    public static void SortableHeadersAreBorderlessButtonsWithAQuietArrowThatSharpensWhenActive()
    {
        var css = File.ReadAllText(StylesheetPath());
        var sort = CssBlock(css, ".ep-sort");

        Assert.Contains("border: 0", sort, StringComparison.Ordinal);
        Assert.Contains("background: transparent", sort, StringComparison.Ordinal);
        Assert.Contains("color-mix(in srgb, var(--ep-text-secondary) 55%, transparent)", CssBlock(css, ".ep-sort .ep-icon"), StringComparison.Ordinal);
        Assert.Matches(@"th\[aria-sort=""ascending""\] \.ep-icon,\s*th\[aria-sort=""descending""\] \.ep-icon,\s*\.ep-sort:hover \.ep-icon \{[^}]*color: var\(--ep-text\)", css);
    }

    // 側欄：桌機收成窄欄、手機是抽屜，兩者都靠 forms.js 設在 <html> 上的屬性；窄欄寬度是 token，收起的抽屜不在 Tab 順序裡。
    [Fact]
    public static void TheSidebarCollapsesToARailAndBecomesADrawerOnPhones()
    {
        var css = File.ReadAllText(StylesheetPath());
        var script = File.ReadAllText(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "forms.js"));

        Assert.Equal("4.5rem", DesignTokens.Measure["sidebar-rail"]);
        Assert.Contains("var(--ep-measure-sidebar-rail)", css, StringComparison.Ordinal);
        Assert.Contains("html[data-side=\"collapsed\"] .ep-side", css, StringComparison.Ordinal);
        Assert.Contains("html[data-js][data-drawer] .ep-side", css, StringComparison.Ordinal);
        Assert.Matches(@"html\[data-js\] \.ep-side \{[^}]*visibility: hidden", css); // 收起的抽屜不能被 Tab 到
        Assert.Contains("data-drawer", script, StringComparison.Ordinal);
        Assert.Contains("localStorage.setItem(\"ep-side\"", script, StringComparison.Ordinal);
    }

    // 外殼換頁不能動：靜態頁沒有載入 MudBlazor 的樣式表（它的重設會清掉 body 邊距），所以邊距自己歸零，否則靜態頁的外殼比互動頁
    // 內縮一圈；頂欄高度由導覽鈕（有 JavaScript 才出現）決定，沒有鈕的那一格也撐到同高，側欄的起點才不跟著腳本跳；
    // Blazor 強化導覽會抹掉 <html> 上由腳本加的 data-js 與 data-side：由 MutationObserver 在畫下一格之前補回（不等 enhancedload，
    // 換到互動頁時它要等新的樣式表載入才觸發）；enhancedload 只負責重設新側欄的 title 與 aria-expanded。
    [Fact]
    public static void TheShellStaysPutAcrossPageKindsScriptLoadAndEnhancedNavigation()
    {
        var css = File.ReadAllText(StylesheetPath());
        var script = File.ReadAllText(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "forms.js"));

        Assert.Matches(@"(?m)^body \{\s*margin: 0;", css);
        Assert.Contains(
            "--ep-appbar-height: calc(var(--ep-touch-target) + var(--ep-space-2) * 2 + 1px)",
            CssBlock(css, ".ep-portal"),
            StringComparison.Ordinal);
        Assert.Contains("new MutationObserver(applyRootState).observe(root, { attributes: true, attributeFilter: [\"data-js\", \"data-side\"] })", script, StringComparison.Ordinal);
        Assert.Contains("addEventListener(\"enhancedload\", syncSide)", script, StringComparison.Ordinal);
        Assert.Contains("root.dataset.js = \"\"", script, StringComparison.Ordinal);
        Assert.Contains("root.dataset.side = \"collapsed\"", script, StringComparison.Ordinal);
    }

    // 表格裡的動作格（td.ep-row-actions）要維持 table-cell：改成 flex 它就不再隨列拉高，底線畫在自己的高度，
    // 沒有動作的空格在列尾多出一小段線；表格儲存格預設不換行，所以要明確放開，動作放不下才會換行而不是把卡片撐出橫向捲動。
    [Fact]
    public static void TheRowActionsCellStaysATableCellAndLetsItsButtonsWrap()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Matches(@"\.ep-examples,\s*\.ep-actions \{\s*display: flex", css); // 動作列（不是儲存格）仍是 flex
        Assert.DoesNotMatch(@"\.ep-row-actions,?\s*\{[^}]*display: flex", css);
        var cell = CssBlock(css, ".ep-plain-table td.ep-row-actions");
        Assert.Contains("vertical-align: middle", cell, StringComparison.Ordinal);
        Assert.Contains("white-space: normal", cell, StringComparison.Ordinal);
        Assert.Contains("display: inline-flex", CssBlock(css, ".ep-row-actions > *"), StringComparison.Ordinal);
        // 窄卡片（儲存格已不是表格）才改回 flex 換行。
        Assert.Matches(@"@container ep-table \(max-width: 36rem\)[^@]*td\.ep-row-actions,\s*\.ep-cards table tbody tr td\.ep-cell-actions \{\s*display: flex", css);
    }

    // 首頁的 AI 輸入：多行輸入依內容長高（原生 field-sizing，Firefox 由 forms.js 補）、有上限；單行的高度（觸控 44px、滑鼠緊湊）
    // 是一個變數，輸入的內距與送出鈕都由它推；沒有日期芯片；說明面板是原生 popover，進出場與選單同一套。
    // Enter 送出、Shift+Enter 換行、組字中不送出，由 forms.js 守住。
    [Fact]
    public static void TheAskComposerGrowsWithItsTextAndKeepsItsHelpInANativeSheet()
    {
        var css = File.ReadAllText(StylesheetPath());
        var script = File.ReadAllText(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "forms.js"));

        var input = CssBlock(css, ".ep-composer-input");
        Assert.Contains("field-sizing: content", input, StringComparison.Ordinal);
        Assert.Contains("resize: none", input, StringComparison.Ordinal);
        Assert.Contains("max-block-size: calc(7lh + var(--ep-composer-row))", input, StringComparison.Ordinal);
        Assert.Contains("--ep-composer-row: var(--ep-touch-target)", CssBlock(css, ".ep-composer"), StringComparison.Ordinal);
        Assert.Matches(@"@media \(pointer: fine\) \{[^@]*\.ep-composer \{\s*--ep-composer-row: var\(--ep-control-height\)", css);
        Assert.Contains(".ep-composer-footer", css, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-new-chat", css, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-date-chip", css, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-composer-bar", css, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-bubble-meta", css, StringComparison.Ordinal);
        Assert.DoesNotContain("ep-ask-lead", css, StringComparison.Ordinal); // 首頁沒有第二行說明

        var sheet = CssBlock(css, ".ep-sheet");
        Assert.Contains("allow-discrete", sheet, StringComparison.Ordinal); // popover 開關是 display 的離散切換
        Assert.Contains(".ep-sheet:popover-open", css, StringComparison.Ordinal);
        Assert.Matches(@"@media \(prefers-reduced-motion: reduce\) \{[^@]*\.ep-sheet,", css);

        Assert.Contains("textarea[data-composer]", script, StringComparison.Ordinal);
        Assert.Contains("event.isComposing || event.keyCode === 229", script, StringComparison.Ordinal);
        Assert.Contains("requestSubmit()", script, StringComparison.Ordinal);
        Assert.Contains("(hover: hover) and (pointer: fine)", script, StringComparison.Ordinal); // 觸控裝置的 Enter 是換行
        Assert.Contains("CSS.supports(\"field-sizing\", \"content\")", script, StringComparison.Ordinal);
    }

    // 六格驗證碼：真的輸入框疊在方格上、文字與游標透明（輸入與自動填入都走它），沒有 JavaScript 時方格不出現。
    [Fact]
    public static void TheOtpBoxesSitOnTopOfARealInput()
    {
        var css = File.ReadAllText(StylesheetPath());
        var script = File.ReadAllText(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "forms.js"));

        Assert.Matches(@"\.ep-otp-slots \{\s*display: none", css);
        Assert.Matches(@"html\[data-js\] \.ep-otp-input \{[^}]*color: transparent[^}]*caret-color: transparent", css);
        Assert.Contains(".ep-otp-slot[data-active]", css, StringComparison.Ordinal);
        Assert.Contains("addEventListener(\"paste\"", script, StringComparison.Ordinal);
        Assert.Contains("autosubmit", script, StringComparison.Ordinal);
        Assert.Contains("h1[tabindex=\"-1\"]:focus-visible", css, StringComparison.Ordinal); // iOS Safari 對程式聚焦的標題會畫焦點環
    }

    // 動態有四種來源（回饋、進出場、載入、手勢）。載入用骨架屏，不用進度條；頁面的區塊進場時錯開；
    // 表頭在寬卡片裡固定；抽屜有跟手的邊緣滑動。減少動態時的退化由既有規則（token 歸零、動畫收成瞬間）負責。
    [Fact]
    public static void MotionHasFourSourcesAndTheStylesheetImplementsThem()
    {
        var css = File.ReadAllText(StylesheetPath());
        var script = File.ReadAllText(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "forms.js"));

        Assert.Equal("1600ms", DesignTokens.Motion["pulse"]);
        Assert.Contains("animation: ep-pulse var(--ep-motion-pulse)", css, StringComparison.Ordinal);
        Assert.Contains("@keyframes ep-pulse", css, StringComparison.Ordinal);
        Assert.DoesNotContain("頁面載入不做進場動畫", css, StringComparison.Ordinal);
        Assert.Matches(@"\.ep-console-main > \*,[^{]*\{[^}]*animation: ep-rise", css); // 頁面的區塊進場
        Assert.Matches(@"@container ep-table \(min-width: 56rem\)[^@]*position: sticky", css); // 表頭固定
        Assert.Contains("html[data-js][data-dragging] .ep-side", css, StringComparison.Ordinal);
        Assert.Contains("addEventListener(\"pointerdown\"", script, StringComparison.Ordinal);
        Assert.Contains("project(velocity)", script, StringComparison.Ordinal); // 依速度投射落點
        Assert.Contains("rubberband(", script, StringComparison.Ordinal); // 拖過頭有阻力
        Assert.Contains("--ep-drawer-duration", script, StringComparison.Ordinal); // 速度承接
    }

    // 預設跟隨系統（prefers-color-scheme），手動選擇只有一個入口：個人設定裡的 AppearanceSetting。偏好存在伺服器的 cookie，
    // 由 App.razor 輸出 data-theme，所以腳本不讀寫任何儲存（沒有 localStorage 的 ep-theme）；靜態樣式表沒有 data-theme
    // （那些選擇器由 TokenCss 產生）。腳本只做即時預覽：換 data-theme、color-scheme 與 theme-color 的 media，系統時還原。
    // 工具列顏色（theme-color）跟著選擇走，不只改頁面背景。QR 固定白底，不隨主題。
    [Fact]
    public static void ManualAppearanceIsAServerSideCookieAndTheScriptOnlyPreviews()
    {
        var css = File.ReadAllText(StylesheetPath());
        var script = File.ReadAllText(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "forms.js"));

        foreach (var leftover in new[] { "data-theme", "ep-theme", "theme-color" })
            Assert.DoesNotContain(leftover, css, StringComparison.Ordinal);

        Assert.DoesNotContain("ep-theme", script, StringComparison.Ordinal);
        Assert.DoesNotContain("localStorage.getItem(\"ep-theme", script, StringComparison.Ordinal);
        // 偏好不經瀏覽器儲存：除了側欄收合，腳本沒有別的 localStorage 用途。
        Assert.Equal(1, Regex.Count(script, @"localStorage\.setItem\("));
        Assert.Contains("localStorage.setItem(\"ep-side\"", script, StringComparison.Ordinal);
        // 即時預覽：選了就換、系統時還原；送出走 fetch（Accept: application/json，伺服器回 204）。
        Assert.Contains("root.dataset.theme = choice", script, StringComparison.Ordinal);
        Assert.Contains("delete root.dataset.theme", script, StringComparison.Ordinal);
        Assert.Contains("scheme.dataset.system", script, StringComparison.Ordinal);
        Assert.Contains("meta.dataset.media", script, StringComparison.Ordinal);
        Assert.Contains("meta.dataset.scheme === choice ? \"all\" : \"not all\"", script, StringComparison.Ordinal);
        Assert.Contains("startViewTransition", script, StringComparison.Ordinal);
        Assert.Contains("Accept: \"application/json\"", script, StringComparison.Ordinal);

        Assert.DoesNotContain(".ep-theme", css, StringComparison.Ordinal);
        Assert.DoesNotContain(".ep-lang-option[aria-pressed", css, StringComparison.Ordinal); // 語言選項用 aria-current
        Assert.Contains(".ep-lang-option[aria-current=\"true\"]", css, StringComparison.Ordinal);
        Assert.Matches(@"\.ep-qr \{[^}]*background: var\(--ep-qr-surface\)", css);
        Assert.Equal("#FFFFFF", DesignTokens.QrSurface);
    }

    // 分段控制（外觀選項）的樣式：選中的塊只動 translate（合成層），滑動時長與緩動取自 token；選中不只靠塊（字色、字重、塊的外框）；
    // 焦點環與按下縮放併進既有的那一條規則（各只寫一次）；沒有 JavaScript 時多一顆「套用」，有腳本才收起。
    [Fact]
    public static void TheSegmentedControlMovesOnlyTranslateAndTakesMotionFromTheTokens()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Matches(@"\.ep-segmented::before \{[^}]*translate: calc\(var\(--ep-segment-index\)[^}]*transition: translate var\(--ep-motion-normal\) var\(--ep-motion-ease-move\)", css);
        Assert.Contains(".ep-segmented:has(> .ep-segment:nth-child(2) > input:checked)", css, StringComparison.Ordinal);
        Assert.Contains(".ep-segmented:has(> .ep-segment:nth-child(3) > input:checked)", css, StringComparison.Ordinal);
        Assert.Matches(@"\.ep-segmented::before \{[^}]*border: 1px solid var\(--ep-border-control\)", css); // 塊對軌道有 3:1 的外框
        Assert.Matches(@"\.ep-segment:has\(> input:checked\) \{\s*color: var\(--ep-text\);\s*font-weight: var\(--ep-label-l-weight\)", css);
        Assert.Matches(@"\.ep-segment > input \{[^}]*opacity: 0", css); // 原生 radio 蓋在整格上
        Assert.Contains(".ep-segment:has(> input:focus-visible)", css, StringComparison.Ordinal);
        Assert.Contains(".ep-segment:has(> input:active)", css, StringComparison.Ordinal);
        Assert.Matches(@"html\[data-js\] \.ep-no-js \{\s*display: none", css);
        // 選項的樣式不另開動態來源：減少動態時沿用全域那條規則收成瞬間。
        Assert.Contains("transition-duration: 0.01ms !important", css, StringComparison.Ordinal);
    }

    // 手動外觀只有一個入口：AppearanceSetting 元件，且只被兩個個人設定頁使用（營運者與客戶入口各一頁）。
    // 版面、匿名頁與其他頁面都不放切換；舊的 ThemeSwitch 不存在；data-theme 只出現在產生它的那幾個檔案。
    [Fact]
    public static void NoSourceOffersAManualAppearanceChoiceExceptThePersonalSettings()
    {
        var web = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "..", ".."));
        var sources = Directory.EnumerateFiles(web, "*.*", SearchOption.AllDirectories)
            .Where(file => file.EndsWith(".razor", StringComparison.Ordinal) || file.EndsWith(".cs", StringComparison.Ordinal)
                || file.EndsWith(".js", StringComparison.Ordinal) || file.EndsWith(".css", StringComparison.Ordinal))
            .Where(file => !file.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
                && !file.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}", StringComparison.Ordinal))
            .ToList();

        Assert.True(sources.Count > 50, "掃不到原始碼，這個測試就沒有意義");
        Assert.False(File.Exists(Path.Combine(web, "Components", "Shared", "ThemeSwitch.razor")));
        string[] settingsPages = [Path.Combine("Components", "Account", "Settings.razor"), Path.Combine("Components", "Portal", "Settings.razor")];
        string[] mayMentionDataTheme = [Path.Combine("Components", "App.razor"), Path.Combine("Components", "App.razor.cs"), Path.Combine("Design", "TokenCss.cs"), Path.Combine("wwwroot", "design", "forms.js"), "Appearance.cs"];
        var shown = new List<string>();
        foreach (var file in sources)
        {
            var text = File.ReadAllText(file);
            var name = Path.GetRelativePath(web, file);
            Assert.False(text.Contains("ThemeSwitch", StringComparison.Ordinal), $"{name}：舊的切換元件已移除，外觀只在個人設定的 AppearanceSetting 選");
            Assert.False(
                text.Contains("data-theme", StringComparison.Ordinal) && !mayMentionDataTheme.Contains(name) && !name.EndsWith("AppearanceSetting.razor", StringComparison.Ordinal),
                $"{name}：只有 App.razor、TokenCss、forms.js 與 Appearance.cs 可以處理 data-theme");
            Assert.False(
                text.Contains("ep-theme", StringComparison.Ordinal) && Path.GetFileName(file) != "App.razor",
                $"{name}：只有 App.razor 可以提到 ep-theme（清掉舊版 localStorage 的偏好）");
            if (text.Contains("<AppearanceSetting", StringComparison.Ordinal))
                shown.Add(name);
        }

        Assert.Equal(settingsPages.Order(), shown.Order());
    }

    // 窄卡片裡操作超過一個時，整排放到卡片最下面並可換行；留在右欄會讓右欄寬度等於整排按鈕，把左邊的電子郵件擠成一行兩三個字。
    [Fact]
    public static void CardRowsWithSeveralActionsPutThemOnTheirOwnLine()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Matches(
            @"@container ep-table \(max-width: 36rem\)[\s\S]*?td:last-child:has\(> :nth-child\(2\)\) \{[^}]*grid-column: 1;[^}]*display: flex;[^}]*flex-wrap: wrap;",
            css);
    }

    // 簡單表格的格子預設不換行；窄卡片（一格一列）要放開，否則長的電子郵件超出卡片、卡片裡多出橫向捲軸。
    [Fact]
    public static void PlainTableCellsWrapInNarrowCards()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Matches(@"@container ep-table \(max-width: 36rem\)[\s\S]*?\.ep-cards \.ep-plain-table td \{\s*white-space: normal;", css);
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

    // 屬性列（徽章與識別碼）往上收是為了貼著頁首的標題；放在卡片裡接在說明文字後面時，收上去會蓋住說明的第二行。
    // 所以預設有上緣，只有緊接在頁首後面那一個才往上收。
    [Fact]
    public static void MetaLineOnlyPullsUpWhenItFollowsThePageHeader()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.DoesNotContain("-1", CssBlock(css, ".ep-meta"), StringComparison.Ordinal);
        Assert.Contains("calc(var(--ep-space-4) * -1)", CssBlock(css, ".ep-page-header + .ep-meta"), StringComparison.Ordinal);
    }

    // 欄標題裡視覺隱藏的字是 position: absolute；表格的捲動容器不是它的定位基準，它就落在被裁掉的表格右端，
    // 把整頁撐出橫向捲軸（頁面本身不該橫向捲動，只有表格在卡片裡捲）。
    [Fact]
    public static void TableScrollWrapContainsItsHiddenAbsoluteText() =>
        Assert.Contains("position: relative", CssBlock(File.ReadAllText(StylesheetPath()), ".ep-table-wrap"), StringComparison.Ordinal);

    private static string CssBlock(string css, string selector)
    {
        var match = Regex.Match(css, @"(?m)^" + Regex.Escape(selector) + @"\s*\{(?<body>[^}]*)\}");
        Assert.True(match.Success, $"找不到規則 {selector}");
        return match.Groups["body"].Value;
    }

    private static string StylesheetPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "wwwroot", "design", "platform.css"));

    private static string DesignSpecPath() =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "..", "..", "Design", "DESIGN.md"));

    private static string GalleryPath() =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(StylesheetPath())!, "..", "..", "Components", "Anonymous", "DesignGallery.razor"));

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

[Collection(PlatformTestGroup.Group3)]
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
        Assert.Contains("design/forms", html, StringComparison.Ordinal); // 表單的漸進增強（密碼顯示、復原碼保存）
        Assert.Contains("lang=\"zh-TW\"", html, StringComparison.Ordinal);
        // 手機：延伸到瀏海下（再用安全區內縮）、不禁用縮放、瀏覽器工具列顏色跟著主題（顏色來自 token）。
        Assert.Contains("viewport-fit=cover", html, StringComparison.Ordinal);
        Assert.DoesNotContain("user-scalable", html, StringComparison.Ordinal);
        Assert.DoesNotContain("maximum-scale", html, StringComparison.Ordinal);
        Assert.Contains($"<meta name=\"theme-color\" content=\"{DesignTokens.Light.Background}\"", html, StringComparison.Ordinal);
        Assert.Contains($"<meta name=\"theme-color\" content=\"{DesignTokens.Dark.Background}\"", html, StringComparison.Ordinal);
        Assert.Contains("<meta name=\"color-scheme\" content=\"light dark\"", html, StringComparison.Ordinal); // 沒有偏好：跟隨系統
        Assert.Contains("media=\"(prefers-color-scheme: light)\"", html, StringComparison.Ordinal); // 工具列顏色各自對應系統
        Assert.Contains("media=\"(prefers-color-scheme: dark)\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("fonts.googleapis.com", html, StringComparison.Ordinal);
        // 沒有設定偏好（預設跟隨系統）：頁面不帶 data-theme，<head> 的小腳本不讀偏好，只清掉舊版 localStorage 的 ep-theme。
        Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(html), StringComparison.Ordinal); // 樣式表裡的選擇器不算，標記裡沒有
        Assert.DoesNotContain("getItem(\"ep-theme\")", html.Replace("&quot;", "\"", StringComparison.Ordinal), StringComparison.Ordinal);
        Assert.Contains("removeItem(\"ep-theme\")", html.Replace("&quot;", "\"", StringComparison.Ordinal), StringComparison.Ordinal);
        // 有 JavaScript 的記號在 <head> 就設，早於樣式表與頁尾的 forms.js：第一格就有導覽鈕，頂欄不先矮一截、腳本一跑再長高。
        var markup = html.Replace("&quot;", "\"", StringComparison.Ordinal);
        Assert.InRange(markup.IndexOf("d.dataset.js=\"\"", StringComparison.Ordinal), 1, markup.IndexOf("design/platform", StringComparison.Ordinal));
        Assert.Contains("<h1", html, StringComparison.Ordinal);
    }

    // 各種版面都不輸出外觀切換：登入（帳號版面）、匿名頁與展示頁（匿名版面）、營運後台（主版面）。切換只在個人設定頁
    // （見 AppearanceTests）。客戶入口的外殼沒有 HTTP 之外的入口，由 DesignTokenTests.NoSourceOffersAManualAppearanceChoiceExceptThePersonalSettings
    // 掃原始碼涵蓋。兩種語言都要看：標籤（Appearance／外觀）是本地化文字，不能只在一種語言下沒出現。
    [Theory]
    [InlineData("/ui/login", false)]
    [InlineData("/ui/denied", false)]
    [InlineData("/ui/signed-out", false)]
    [InlineData("/design", false)]
    [InlineData("/ui", true)]
    [InlineData("/ui/tenants", true)]
    public async Task NoPageOffersAManualAppearanceChoice(string path, bool signedIn)
    {
        using var client = signedIn ? factory.SignedInClient() : factory.CreateClient();

        foreach (var language in new[] { "zh-TW", "en" })
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(path, UriKind.Relative));
            request.Headers.AcceptLanguage.ParseAdd(language);
            using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
            var html = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            Assert.DoesNotContain("data-theme", PageMarkup.WithoutStyles(html), StringComparison.Ordinal);
            Assert.DoesNotContain("ep-theme\"", html.Replace("&quot;", "\"", StringComparison.Ordinal).Replace("removeItem(\"ep-theme\")", "", StringComparison.Ordinal), StringComparison.Ordinal);
            Assert.DoesNotContain("aria-label=\"Appearance\"", html, StringComparison.Ordinal);
            Assert.DoesNotContain("aria-label=\"外觀\"", html, StringComparison.Ordinal);
            Assert.DoesNotContain("data-appearance-form", html, StringComparison.Ordinal);
            Assert.DoesNotContain("name=\"appearance\"", html, StringComparison.Ordinal);
            // 語言選項還在（同一種文字鈕列），旁邊沒有外觀；深淺色的 token 與兩個工具列顏色都在。
            Assert.Contains("prefers-color-scheme:dark", html, StringComparison.Ordinal);
            Assert.Contains("name=\"theme-color\"", html, StringComparison.Ordinal);
        }
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

    // 靜態頁（登入、入口）沒有 MudBlazor 的元件，就不下載它的樣式與腳本；互動的後台頁才載入。
    // 每一頁都預載拉丁字型，路徑要與 fonts.css 引用的同一個檔（不加指紋），否則瀏覽器會再抓一次。
    [Fact]
    public async Task OnlyInteractivePagesLoadMudBlazorAndEveryPagePreloadsTheLatinFont()
    {
        using var client = factory.CreateClient(new Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        var staticPage = await client.GetStringAsync(new Uri("/ui/login", UriKind.Relative), TestContext.Current.CancellationToken);
        var interactivePage = await client.GetStringAsync(new Uri("/ui/denied", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.DoesNotContain("MudBlazor", staticPage, StringComparison.Ordinal);
        Assert.Matches(@"_content/MudBlazor/MudBlazor\.min\.[\w.]*css", interactivePage);
        Assert.Matches(@"_content/MudBlazor/MudBlazor\.min\.[\w.]*js", interactivePage);

        var preload = $"<link rel=\"preload\" href=\"{EnvCite.Platform.Web.Components.App.RobotoLatin}\" as=\"font\" type=\"font/woff2\" crossorigin";
        Assert.Contains(preload, staticPage, StringComparison.Ordinal);
        Assert.Contains(preload, interactivePage, StringComparison.Ordinal);
        var fonts = await client.GetStringAsync(new Uri("_content/EnvCite.Platform.Web/fonts/fonts.css", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Contains($"url({EnvCite.Platform.Web.Components.App.RobotoLatin["_content/EnvCite.Platform.Web/fonts/".Length..]})", fonts, StringComparison.Ordinal);
        using var font = await client.GetAsync(new Uri(EnvCite.Platform.Web.Components.App.RobotoLatin, UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, font.StatusCode);
    }
}